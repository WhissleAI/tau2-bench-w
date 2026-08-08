# Copyright Sierra
"""The adapter: a real Whissle platform agent playing AgentClinic's DOCTOR.

Transport is ``POST {WHISSLE_BASE}/api/bench/agent-turn`` — messages + tool schemas in,
text and/or tool calls out — the same endpoint, auth header, retry policy and error
handling as :mod:`tau2.agent.whissle_agent` (Bearer ``wsk_`` key, 3 attempts, retry on
5xx with linear backoff, 120 s timeout). Nothing about the agent is special-cased for
the benchmark: its own prompt scaffolding, model, tools and guardrails run.

Two histories are supported, because they answer different questions:

``native`` (default)
    A real multi-turn ``messages`` array — the way our product is actually driven,
    and the way tool results must be threaded (``tool_use`` → ``tool_result``).

``agentclinic``
    Upstream's exact prompting: a single user message per turn containing the rolling
    ``agent_hist`` string ("Here is a history of your dialogue: … Now please continue
    your dialogue\\nDoctor: "). Stateless, and byte-identical to what the published
    baselines received. Use it when you want the tightest possible comparison.

Both regenerate the system prompt every turn so the doctor's live question budget is
accurate, exactly as upstream does.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

from tau2.health.agentclinic.errors import DoctorInfraError
from tau2.health.agentclinic.protocol import (
    DoctorAction,
    doctor_system_prompt,
    parse_doctor_output,
    tool_schemas,
)
from tau2.health.agentclinic.vision import (
    BLOCK,
    BOTH,
    MAX_IMAGES_PER_REQUEST,
    OFF,
    CaseImage,
    image_user_content,
    vision_tools,
)

DEFAULT_BASE = "https://aws-gateway-backend.whissle.ai/bot"

# When the agent's own configured prompt is left in place (``prompt_mode="agent"``),
# the AgentClinic contract has to travel as ordinary conversation instead. This rides
# on the FIRST user message only.
PROTOCOL_BRIEF_MARKERS = (
    "[Consultation protocol] You are speaking with a patient in a clinic. Ask "
    "questions to understand their condition. You may order tests by writing "
    '"REQUEST TEST: [test]" on its own. When you are ready to state the single most '
    'likely diagnosis, write "DIAGNOSIS READY: [diagnosis here]". Keep each reply to '
    "1-3 sentences.\n\n"
)
PROTOCOL_BRIEF_TOOLS = (
    "[Consultation protocol] You are speaking with a patient in a clinic. Ask "
    "questions to understand their condition, order tests with `request_test`, and "
    "commit to the single most likely diagnosis with `make_diagnosis` when you are "
    "ready. Keep each reply to 1-3 sentences.\n\n"
)


def prune_images(messages: list[dict], cap: int = MAX_IMAGES_PER_REQUEST
                 ) -> list[dict]:
    """Keep at most ``cap`` image blocks across the whole message list (the backend
    rejects more with a 400), dropping the OLDEST first and leaving a breadcrumb so
    the model knows an earlier image was elided rather than imagining it never
    existed. Returns a new list; the caller's history is untouched."""
    idx: list[tuple[int, int]] = []
    for mi, m in enumerate(messages):
        content = m.get("content")
        if isinstance(content, list):
            for bi, b in enumerate(content):
                if isinstance(b, dict) and b.get("type") == "image":
                    idx.append((mi, bi))
    if len(idx) <= cap:
        return messages
    drop = set(idx[: len(idx) - cap])
    out: list[dict] = []
    for mi, m in enumerate(messages):
        content = m.get("content")
        if not isinstance(content, list):
            out.append(m)
            continue
        blocks = []
        for bi, b in enumerate(content):
            if (mi, bi) in drop:
                blocks.append({"type": "text",
                               "text": "[earlier copy of the case image omitted]"})
            else:
                blocks.append(b)
        out.append({**m, "content": blocks})
    return out


@dataclass
class DoctorConfig:
    agent_id: str
    base: str = field(default_factory=lambda: (os.getenv("WHISSLE_BASE")
                                               or DEFAULT_BASE).rstrip("/"))
    api_key: str = field(default_factory=lambda: os.getenv("WHISSLE_API_KEY") or "")
    model: Optional[str] = field(default_factory=lambda: os.getenv("WHISSLE_MODEL")
                                 or None)
    protocol: str = "markers"       # "markers" | "tools"
    history: str = "native"         # "native"  | "agentclinic"
    # "override" — send AgentClinic's doctor prompt as ``system`` (upstream's contract,
    #   the comparable arm; the agent's own configured persona is replaced).
    # "agent"    — send NO system at all, so the agent runs its OWN shipped prompt and
    #   guardrails, with the clinic protocol delivered as conversation. This is the arm
    #   where a deliberate "I don't diagnose" boundary actually shows up, and the only
    #   honest way to measure it.
    prompt_mode: str = "override"
    vision: str = OFF               # see vision.VISION_MODES
    max_infs: int = 20
    img_request: bool = False
    bias_prompt: str = ""
    timeout: float = 120.0
    attempts: int = 3

    def require(self) -> None:
        if not self.agent_id:
            raise ValueError("no agent id — pass --agent-id or set WHISSLE_AGENT_ID")
        if not self.api_key:
            raise ValueError("WHISSLE_API_KEY is required (a wsk_ key for the org)")


class WhissleDoctor:
    """One doctor, for one case. Stateful across the case's turns."""

    def __init__(self, cfg: DoctorConfig, presentation: Any,
                 image: Optional[CaseImage] = None) -> None:
        cfg.require()
        self.cfg = cfg
        self.presentation = presentation
        self.image = image
        self.infs = 0                     # doctor inferences consumed (upstream's)
        self.messages: list[dict] = []    # native history
        self.agent_hist = ""              # upstream's rolling string
        self.turns: list[dict] = []       # raw request/response records (artifact)
        self._s = requests.Session()
        self._s.headers.update({
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        })
        self._image_sent = False

    # -- prompt ------------------------------------------------------------------

    def system(self) -> str:
        return doctor_system_prompt(
            self.presentation, max_infs=self.cfg.max_infs, infs=self.infs,
            bias_prompt=self.cfg.bias_prompt, img_request=self.cfg.img_request,
            protocol=self.cfg.protocol)

    def tools(self) -> list[dict]:
        if self.cfg.protocol != "tools":
            # Marker protocol advertises NO tools — upstream's exact contract. The
            # vision tool is the one exception: it is an input channel, not an action,
            # so it is available in either protocol when vision is in tool mode.
            return vision_tools(self.cfg.vision)
        return tool_schemas(img_request=self.cfg.img_request) + vision_tools(
            self.cfg.vision)

    # -- one doctor turn ---------------------------------------------------------

    def act(self, incoming: Optional[str], *,
            attach_image: bool = False) -> DoctorAction:
        """Feed the doctor the patient's (or measurement reader's) latest line and
        get back the doctor's next action.

        ``incoming`` is ``""`` on the very first turn — upstream opens with an empty
        ``pi_dialogue`` and the doctor speaks first — and ``None`` when the previous
        turn's tool result has already been threaded into history (tools protocol), in
        which case the agent continues from that result with no new user utterance."""
        if self.infs >= self.cfg.max_infs:
            # Upstream returns the literal string "Maximum inferences reached".
            return DoctorAction("question", "Maximum inferences reached")

        send_image = (attach_image and self.image is not None
                      and self.cfg.vision in (BLOCK, BOTH))
        if incoming is None and self.cfg.history != "agentclinic":
            if send_image and self.image is not None:
                self.messages.append(
                    {"role": "user", "content": [self.image.content_block()]})
            msgs = list(self.messages)
        else:
            content = self._user_content(incoming or "", send_image)
            if self.cfg.history == "agentclinic":
                msgs = [{"role": "user", "content": content}]
            else:
                self.messages.append({"role": "user", "content": content})
                msgs = list(self.messages)

        resp = self._agent_turn(msgs)
        reply = (resp.get("reply") or "").strip()
        blocks = resp.get("content") or []
        calls = list(resp.get("tool_calls") or [])
        action = parse_doctor_output(reply, calls)

        if self.cfg.history != "agentclinic":
            self.messages.append({"role": "assistant",
                                  "content": blocks or (reply or action.text)})
        self.agent_hist += (incoming or "") + "\n\n" + action.text + "\n\n"
        self.infs += 1
        if send_image:
            self._image_sent = True
        self.turns.append({
            "inference": self.infs,
            "incoming": incoming,
            "reply": reply,
            "tool_calls": calls,
            "kind": action.kind,
            "payload": action.payload,
            "format_deviation": action.format_deviation,
            "image_attached": bool(send_image),
            "usage": resp.get("usage"),
            "stop_reason": resp.get("stop_reason"),
        })
        return action

    def deliver_tool_result(self, action: DoctorAction, result: str) -> None:
        """Thread a tool RESULT back into native history.

        In tools mode the measurement reader's answer is the ``request_test`` tool's
        result, not a new user utterance — so the agent sees a well-formed
        ``tool_use`` → ``tool_result`` pair rather than a dangling call. In marker
        mode (or agentclinic history) there is nothing to thread; the result arrives
        as the next ``incoming`` line, exactly as upstream delivers it."""
        if self.cfg.history == "agentclinic" or not action.tool_calls:
            return
        blocks = [{"type": "tool_result", "tool_use_id": c.get("id"),
                   "content": result}
                  for c in action.tool_calls if c.get("id")]
        if blocks:
            self.messages.append({"role": "user", "content": blocks})

    # -- internals ---------------------------------------------------------------

    def _user_content(self, incoming: str, send_image: bool) -> Any:
        if self.cfg.history == "agentclinic":
            text = ("\nHere is a history of your dialogue: " + self.agent_hist
                    + "\n Here was the patient response: " + incoming
                    + "Now please continue your dialogue\nDoctor: ")
        else:
            text = incoming or ("(The patient is in front of you. Begin the "
                                "consultation.)")
        if self.cfg.prompt_mode == "agent" and self.infs == 0:
            brief = (PROTOCOL_BRIEF_TOOLS if self.cfg.protocol == "tools"
                     else PROTOCOL_BRIEF_MARKERS)
            text = brief + text
        if send_image:
            return image_user_content(text, self.image, self.cfg.vision)
        return text

    def _agent_turn(self, messages: list[dict]) -> dict:
        """POST /api/bench/agent-turn with the same retry policy as
        ``tau2.agent.whissle_agent._turn``: retry 5xx and transport errors with a
        linear backoff, never retry a 4xx, and raise a typed infra error at the end
        so the runner buckets the case out instead of scoring it."""
        body: dict[str, Any] = {
            "agent_id": self.cfg.agent_id,
            "messages": prune_images(messages),
            "tools": self.tools(),
        }
        if self.cfg.prompt_mode != "agent":
            # Omitting `system` entirely leaves the agent's OWN configured prompt in
            # force — that is the whole point of the "agent" arm.
            body["system"] = self.system()
        if self.cfg.model:
            body["model"] = self.cfg.model
        last = "unknown"
        for attempt in range(self.cfg.attempts):
            try:
                r = self._s.post(f"{self.cfg.base}/api/bench/agent-turn",
                                 data=json.dumps(body), timeout=self.cfg.timeout)
                if r.status_code >= 500:
                    last = f"{r.status_code} {r.text[:160]}"
                    time.sleep(2 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    # A 4xx is a contract error (bad agent id, unsupported content
                    # block, exhausted credit). Not retried, and still infra: the case
                    # measured nothing about clinical reasoning.
                    raise DoctorInfraError(
                        f"agent-turn -> HTTP {r.status_code}: {r.text[:300]}")
                return r.json()
            except requests.RequestException as e:
                last = str(e)
                time.sleep(2 * (attempt + 1))
        raise DoctorInfraError(f"agent-turn failed after retries: {last}")


# ── agent provisioning ──────────────────────────────────────────────────────────

@dataclass
class ProvisionedAgent:
    agent_id: str
    agent_type: Optional[str]
    created: bool
    name: Optional[str] = None


def resolve_agent(agent_id: Optional[str], agent_type: Optional[str],
                  *, base: Optional[str] = None,
                  api_key: Optional[str] = None) -> ProvisionedAgent:
    """Decide WHICH agent plays the doctor.

    Precedence: an explicit ``--agent-id`` / ``WHISSLE_AGENT_ID`` wins. Otherwise, if
    an ``--agent-type`` is given, create a throwaway agent of that seeded type — this
    is how you point the harness at the purpose-built ``clinical_intake_triage`` type
    (interview-and-assess shape, red-flag escalation, explicit "gathering information,
    not diagnosing" boundary) and compare it head-to-head with a generic agent, and it
    does not block on that type existing: if the backend rejects the type, you get a
    clear error naming it rather than a silent fallback to a different agent.
    """
    if agent_id:
        return ProvisionedAgent(agent_id, agent_type, created=False)
    env_id = os.getenv("WHISSLE_AGENT_ID")
    if env_id and not agent_type:
        return ProvisionedAgent(env_id, None, created=False)
    if not agent_type:
        raise ValueError(
            "no doctor agent — pass --agent-id, set WHISSLE_AGENT_ID, or pass "
            "--agent-type to create a throwaway agent of a seeded type")

    from tau2.flow.client import FlowClient

    client = FlowClient(base=base, api_key=api_key)
    name = f"agentclinic-{agent_type}-{uuid.uuid4().hex[:8]}"
    created = client.create_typed_agent(
        name, agent_type,
        "You are a clinical intake agent participating in a benchmark evaluation.")
    return ProvisionedAgent(created["id"], agent_type, created=True, name=name)


def teardown_agent(p: ProvisionedAgent, *, base: Optional[str] = None,
                   api_key: Optional[str] = None) -> bool:
    """Delete a throwaway agent. Never raises — a leaked agent must not fail a run,
    but the run report says whether cleanup succeeded."""
    if not p.created:
        return True
    try:
        from tau2.flow.client import FlowClient

        FlowClient(base=base, api_key=api_key).delete_agent(p.agent_id, confirm=True)
        return True
    except Exception:  # noqa: BLE001
        return False
