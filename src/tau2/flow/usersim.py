# Copyright Sierra
"""LLM user-simulator + LLM judges, built on Whissle's OWN à-la-carte model API.

The simulated user is an LLM given a PERSONA + GOAL + the running transcript, which
produces the next user utterance and decides when the user would hang up. To stay
self-contained (no external ANTHROPIC/OPENAI key), it uses the Whissle backend's own
chat-model endpoint as the driver:

    POST {WHISSLE_BASE}/api/models/chat   {"messages":[{role,content}, ...]}
      -> {"text": str, "usage": {...}, "cost_usd": str, "latency_ms": int}

That endpoint accepts standard ``system`` / ``user`` / ``assistant`` roles (verified
live with the wsk_ key), so we map the dialogue with the AGENT-UNDER-TEST as the
counterparty: from the simulator's point of view, the agent's replies arrive as
``user`` messages and the simulator's own lines are ``assistant`` messages. The
model then generates the next ``assistant`` message = the next USER utterance.

Three roles all run through the same endpoint:
  * :class:`UserSimulator` — plays the caller, in character, pursuing the goal.
  * :func:`judge_task_success` — did the caller achieve the goal? (1 call/session)
  * :func:`judge_goal_drift`   — per agent turn: did the agent pursue the CURRENT
    flow state's goal, or drift? (optional; costs one call per turn)
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_BASE = "https://aws-gateway-backend.whissle.ai/bot"
END_SENTINEL = "[[END]]"
GOAL_SENTINEL = "[[GOAL_MET]]"


class ModelError(RuntimeError):
    pass


class WhissleModel:
    """Thin wrapper over ``POST /api/models/chat`` — the user-sim / judge LLM."""

    def __init__(self, base: Optional[str] = None, api_key: Optional[str] = None,
                 timeout: float = 90.0, max_tokens: Optional[int] = None) -> None:
        self.base = (base or os.getenv("WHISSLE_BASE") or DEFAULT_BASE).rstrip("/")
        self.api_key = api_key or os.getenv("WHISSLE_API_KEY") or ""
        if not self.api_key:
            raise RuntimeError("WHISSLE_API_KEY not set — put a wsk_ key in .env.")
        self.timeout = timeout
        # The endpoint's DEFAULT output cap is ~512 tokens and it truncates silently —
        # fine for a user-sim utterance, fatal for a caller that needs a long
        # completion (PatientAgentBench's sandbox generator emits a multi-kB JSON
        # document, which arrived unterminated and failed to parse). Send max_tokens
        # only when a caller asks for it, so existing behaviour is byte-identical.
        self.max_tokens = max_tokens
        self._s = requests.Session()
        self._s.headers.update({"Authorization": f"Bearer {self.api_key}"})
        self.total_cost_usd = 0.0
        self.calls = 0

    def chat(self, messages: list[dict[str, str]], *, attempts: int = 6,
             max_tokens: Optional[int] = None) -> str:
        """POST /api/models/chat with retry. The driver LLM intermittently returns a
        transient 5xx / 502 (the backend chat worker is momentarily STARVED by a live
        voice session sharing its event loop → gateway 502) or an EMPTY completion
        ("all providers failed; gemini empty completion"). A single failure would kill
        the whole session (empty trace — the ~40% drop). The driver isn't
        latency-sensitive, so we retry generously with a LONG backoff (3,6,9,12,12s ≈
        40s total) to span the busy window; a 4xx is a real client error and is NOT
        retried. Raises only after all attempts are exhausted."""
        last = ""
        body: dict[str, Any] = {"messages": messages}
        cap = max_tokens if max_tokens is not None else self.max_tokens
        if cap:
            body["max_tokens"] = int(cap)
        for i in range(max(1, attempts)):
            try:
                r = self._s.post(f"{self.base}/api/models/chat",
                                 json=body, timeout=self.timeout)
            except requests.RequestException as e:  # conn/read timeout, conn reset…
                last = f"request error: {e}"
            else:
                if r.status_code < 300:
                    d = r.json(strict=False)
                    text = (d.get("text") or "").strip()
                    if text:
                        self.calls += 1
                        try:
                            self.total_cost_usd += float(d.get("cost_usd") or 0.0)
                        except (TypeError, ValueError):
                            pass
                        return text
                    last = "empty completion"          # transient — retry
                elif r.status_code < 500:
                    raise ModelError(f"models/chat -> HTTP {r.status_code}: {r.text[:300]}")
                else:
                    last = f"HTTP {r.status_code}: {r.text[:200]}"  # 5xx — retry
            if i < attempts - 1:
                time.sleep(min(3.0 * (i + 1), 12.0))   # 3,6,9,12,12s — spans the busy window
        raise ModelError(f"models/chat failed after {attempts} attempts: {last}")

    def chat_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """Chat then best-effort parse a JSON object out of the reply."""
        text = self.chat(messages)
        return _extract_json(text)


# ── persona/goal task ──────────────────────────────────────────────────────────

@dataclass
class Task:
    """One persona + goal for the simulated user, plus optional grading hints."""

    id: str
    agent_type: str
    persona: str
    goal: str
    scenario: str = ""                       # e.g. "reschedule", "dispute"
    compliance: Optional[dict] = None        # forbidden-before-gate spec, per type
    max_turns: int = 14
    # How many cooperative turns the sim grants AFTER its goal is met for the AGENT
    # to deliver its closing and reach flow_end. Within the allowance the sim keeps
    # responding naturally (acknowledgements, "no, that's all", returning the
    # goodbye); if the agent still hasn't closed when it runs out, that is the
    # agent's failure (analyze.py classifies it ``agent_no_close``), not the sim's.
    post_goal_turns: int = 4

    @staticmethod
    def from_dict(agent_type: str, d: dict, defaults: dict) -> "Task":
        return Task(
            id=d["id"],
            agent_type=agent_type,
            persona=d["persona"],
            goal=d["goal"],
            scenario=d.get("scenario", ""),
            compliance=d.get("compliance") or defaults.get("compliance"),
            max_turns=d.get("max_turns", defaults.get("max_turns", 14)),
            post_goal_turns=d.get("post_goal_turns",
                                  defaults.get("post_goal_turns", 4)),
        )


# ── the simulated user ──────────────────────────────────────────────────────────

USER_SYSTEM_TEMPLATE = """\
You are role-playing a CUSTOMER on a phone/chat call with an automated business \
agent. You are NOT the agent. Stay strictly in character and pursue your goal.

# Who you are
{persona}

# Your goal for this call
{goal}

# Rules (obey exactly)
- Reply with ONE short, natural customer utterance — one or two sentences, the way a \
real person speaks on a call. No stage directions, no narration, no quotation marks, \
no labels like "User:".
- Play ONLY the customer. Never write the agent's lines or describe what the agent \
does.
- Pursue your goal. Provide details the agent asks for that a person like you would \
know (make up plausible specifics — name, dates, amounts — and stay consistent).
- Do not be maximally helpful or robotic; behave like your persona (impatient, \
confused, skeptical, etc. as described).

# Ending the call (follow this exactly)
- The moment your goal has been achieved, append the token {goal_sentinel} at the \
very end of that utterance (once only). This does NOT end the call.
- After your goal is achieved, do NOT go silent and do NOT hang up on your own. A \
real caller stays on the line for the wrap-up: give brief natural acknowledgements, \
answer "anything else?" with a simple no-thanks, and return the agent's goodbye. \
Keep these closing turns short (a few words).
- Append the token {sentinel} at the very end of an utterance ONLY when the call is \
actually over: the agent has delivered its goodbye/closing (return the goodbye and \
append it), OR the agent has clearly refused / cannot help, OR your persona would \
genuinely abandon the call. Never append {sentinel} merely because your goal was \
just achieved — let the agent close first.
- If the agent says nothing (silence), prompt them once ("Hello? Are you still \
there?") rather than hanging up immediately.
"""


@dataclass
class UserSimulator:
    task: Task
    model: WhissleModel
    history: list[dict[str, str]] = field(default_factory=list)  # agent<->user turns
    done: bool = False        # the CALL is over (agent closed / refused / abandoned)
    goal_met: bool = False    # the sim's goal is satisfied (call may still be open)

    def _system(self) -> dict[str, str]:
        return {"role": "system", "content": USER_SYSTEM_TEMPLATE.format(
            persona=self.task.persona, goal=self.task.goal,
            sentinel=END_SENTINEL, goal_sentinel=GOAL_SENTINEL)}

    def first_utterance(self) -> str:
        """Open the call (the user speaks first in the text channel)."""
        msgs = [self._system(),
                {"role": "user", "content": "(The call has connected. Say your "
                 "opening line as the customer.)"}]
        return self._gen(msgs)

    def next_utterance(self, agent_reply: str) -> str:
        """Given the agent's latest reply, produce the next user turn."""
        # An EMPTY agent reply (the agent stalled / produced nothing) must not be
        # sent as empty content — surface the silence so the sim reacts naturally
        # (prompting once instead of hanging up), per its closing rules.
        content = (agent_reply or "").strip() or \
            "(The agent said nothing — silence on the line.)"
        # Map: agent reply -> incoming "user" msg; our prior lines are "assistant".
        self.history.append({"role": "user", "content": content})
        msgs = [self._system(), *self.history]
        return self._gen(msgs)

    def _gen(self, msgs: list[dict[str, str]]) -> str:
        raw = self.model.chat(msgs)
        text = raw
        if END_SENTINEL in raw:
            self.done = True
            text = text.replace(END_SENTINEL, "").strip()
        if GOAL_SENTINEL in text:
            self.goal_met = True
            text = text.replace(GOAL_SENTINEL, "").strip()
        # Strip any accidental role label the model prepended.
        text = re.sub(r"^\s*(user|customer)\s*:\s*", "", text, flags=re.I)
        self.history.append({"role": "assistant", "content": text})
        return text or "Okay."


# ── LLM judges ──────────────────────────────────────────────────────────────────

def judge_task_success(model: WhissleModel, task: Task,
                       transcript: str) -> dict[str, Any]:
    """One call: did the simulated user achieve the goal? -> {success, reason}."""
    sys = ("You are a strict QA grader. You are given a customer's GOAL for a call "
           "and the full transcript. Decide whether the goal was ACHIEVED by the end "
           "of the call. Reply with a JSON object only: "
           '{"success": true|false, "reason": "<one sentence>"}.')
    user = (f"GOAL:\n{task.goal}\n\nTRANSCRIPT:\n{transcript}\n\n"
            "Did the customer achieve the goal? JSON only.")
    try:
        d = model.chat_json([{"role": "system", "content": sys},
                             {"role": "user", "content": user}])
    except Exception as e:  # noqa: BLE001
        return {"success": None, "reason": f"judge error: {e}"}
    return {"success": bool(d.get("success")) if "success" in d else None,
            "reason": d.get("reason", "")}


def judge_goal_drift(model: WhissleModel, state_goal: str, agent_reply: str,
                     user_msg: str) -> dict[str, Any]:
    """Per turn: did the agent pursue the CURRENT state's goal? -> {on_goal, reason}.

    ``state_goal`` is the declared goal/say of the flow state that was active. Empty
    goal (e.g. a tool state) short-circuits to on_goal=True (nothing to drift from).
    """
    if not (state_goal or "").strip():
        return {"on_goal": True, "reason": "no state goal to judge"}
    sys = ("You are a strict QA grader for a scripted phone agent. The agent is "
           "supposed to be pursuing a specific CONVERSATION-STATE GOAL. Given that "
           "goal, the customer's last message, and the agent's reply, decide whether "
           "the agent's reply pursued the state goal (on_goal) or drifted off it. "
           'Reply with JSON only: {"on_goal": true|false, "reason": "<one sentence>"}.')
    user = (f"STATE GOAL:\n{state_goal}\n\nCUSTOMER SAID:\n{user_msg}\n\n"
            f"AGENT REPLIED:\n{agent_reply}\n\nJSON only.")
    try:
        d = model.chat_json([{"role": "system", "content": sys},
                             {"role": "user", "content": user}])
    except Exception as e:  # noqa: BLE001
        return {"on_goal": None, "reason": f"judge error: {e}"}
    return {"on_goal": bool(d.get("on_goal")) if "on_goal" in d else None,
            "reason": d.get("reason", "")}


# ── helpers ──────────────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict[str, Any]:
    """Pull the first {...} JSON object out of a model reply (tolerates code fences
    and prose around it)."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", text).strip()
    try:
        return json.loads(text, strict=False)
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r"\{.*\}", text, flags=re.S)
    if m:
        try:
            return json.loads(m.group(0), strict=False)
        except Exception:  # noqa: BLE001
            pass
    return {}
