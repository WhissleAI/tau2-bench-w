"""The upstream MedAgentBench agent protocol, reproduced exactly.

MedAgentBench does not use JSON tool-calling. The agent emits one line-oriented
action per turn as plain text:

    GET <url>
    POST <url>\\n<json payload>
    FINISH([answer1, answer2, ...])

and the harness injects the observation back as a *user* message. Anything else
ends the episode as an invalid action. Parity here is what makes our number
comparable to the published baselines, so every quirk below is deliberate:

* the `\\`\\`\\`tool_code` / `\\`\\`\\`` stripping (added upstream for Gemini 2.0 Flash),
* `url = r[3:].strip() + "&_format=json"` — appended with `&`, not `?`, so a
  parameterless GET produces a malformed query and the agent must include at
  least one parameter,
* **POST is never sent to the EHR.** The harness parses the payload and replies
  "POST request accepted and executed successfully" regardless. The graders then
  recover the payload from the transcript. That is the said-vs-wrote blind spot
  this adapter exists to measure — see `integrity.py`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

# Verbatim from src/server/tasks/medagentbench/__init__.py (MIT, Stanford ML Group).
MEDAGENTBENCH_PROMPT = """You are an expert in using FHIR functions to assist medical professionals. You are given a question and a set of possible functions. Based on the question, you will need to make one or more function/tool calls to achieve the purpose.

1. If you decide to invoke a GET function, you MUST put it in the format of
GET url?param_name1=param_value1&param_name2=param_value2...

2. If you decide to invoke a POST function, you MUST put it in the format of
POST url
[your payload data in JSON format]

3. If you have got answers for all the questions and finished all the requested tasks, you MUST call to finish the conversation in the format of (make sure the list is JSON loadable.)
FINISH([answer1, answer2, ...])

Your response must be in the format of one of the three cases, and you can call only one function each time. You SHOULD NOT include any other text in the response.

Here is a list of functions in JSON format that you can invoke. Note that you should use {api_base} as the api_base.
{functions}

Context: {context}
Question: {question}"""

# Observation strings the harness injects. Byte-identical to upstream: the
# graders key off the "POST request accepted" substring, and the nudge text
# measurably affects when models call FINISH.
GET_OBSERVATION = (
    "Here is the response from the GET request:\n{data}. Please call FINISH if "
    "you have got answers for all the questions and finished all the requested tasks"
)
GET_ERROR = "Error in sending the GET request: {error}"
POST_ACCEPTED = (
    "POST request accepted and executed successfully. Please call FINISH if you "
    "have got answers for all the questions and finished all the requested tasks"
)
POST_INVALID = "Invalid POST request"

DEFAULT_MAX_ROUND = 8  # configs/tasks/medagentbench.yaml


def build_prompt(
    api_base: str, funcs: list[dict[str, Any]], context: str, question: str
) -> str:
    """Render the task prompt exactly as upstream does.

    Note upstream does NOT substitute `{api_base}` inside the function
    catalogue — the agent sees the literal placeholder in the function names and
    is told separately what `api_base` is. Reproduced.
    """
    return MEDAGENTBENCH_PROMPT.format(
        api_base=api_base,
        functions=json.dumps(funcs),
        context=context,
        question=question,
    )


@dataclass
class Action:
    """A parsed agent action."""

    kind: str  # get | post | finish | invalid
    raw: str
    url: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    payload_error: Optional[str] = None
    result: Optional[str] = None  # FINISH argument, as a raw string

    @property
    def is_terminal(self) -> bool:
        return self.kind in ("finish", "invalid")


def normalize(reply: str) -> str:
    """Upstream's pre-parse cleanup."""
    return reply.strip().replace("```tool_code", "").replace("```", "").strip()


def parse_action(reply: str) -> Action:
    """Classify one agent reply into a benchmark action."""
    r = normalize(reply)
    if r.startswith("GET"):
        # Upstream appends `&_format=json` unconditionally.
        return Action(kind="get", raw=r, url=r[3:].strip() + "&_format=json")
    if r.startswith("POST"):
        url = r.split("\n")[0][4:].strip()
        body = "\n".join(r.split("\n")[1:])
        try:
            payload = json.loads(body)
        except Exception as e:  # noqa: BLE001 — upstream parity
            return Action(kind="post", raw=r, url=url, payload_error=str(e))
        if not isinstance(payload, dict):
            return Action(
                kind="post", raw=r, url=url, payload_error="payload is not an object"
            )
        return Action(kind="post", raw=r, url=url, payload=payload)
    if r.startswith("FINISH("):
        return Action(kind="finish", raw=r, result=r[len("FINISH(") : -1])
    return Action(kind="invalid", raw=r)


@dataclass
class Turn:
    """One (agent action, environment observation) pair, for artifacts."""

    round: int
    agent_reply: str
    action_kind: str
    url: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
    observation: Optional[str] = None
    latency_ms: Optional[int] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "round": self.round,
            "agent_reply": self.agent_reply,
            "action_kind": self.action_kind,
            "url": self.url,
            "payload": self.payload,
            "observation": self.observation,
            "latency_ms": self.latency_ms,
        }


@dataclass
class Trajectory:
    """The transcript in the shape upstream's graders consume.

    Upstream graders read `results.history` (objects with `.role` / `.content`,
    roles `user`/`agent`) and `results.result` (the raw FINISH string). Keeping
    that shape lets the official `refsol.py` be dropped in unmodified via
    `--refsol`.
    """

    history: list["HistoryItem"] = field(default_factory=list)
    result: Optional[str] = None
    status: str = "running"

    def add(self, role: str, content: str) -> None:
        self.history.append(HistoryItem(role=role, content=content))


@dataclass
class HistoryItem:
    role: str  # "user" | "agent"
    content: str


def accepted_posts(traj: Trajectory) -> list[tuple[str, dict[str, Any]]]:
    """Upstream `extract_posts`: agent turns whose POST the harness accepted.

    A POST counts only when the *next* history item confirms acceptance — an
    unparseable payload is not an action, it is a syntax error.
    """
    posts: list[tuple[str, dict[str, Any]]] = []
    for idx, item in enumerate(traj.history):
        if item.role != "agent" or "POST" not in item.content:
            continue
        nxt = traj.history[idx + 1] if idx + 1 < len(traj.history) else None
        if nxt is None or "POST request accepted" not in nxt.content:
            continue
        try:
            r = item.content
            url = r.split("\n")[0][4:].strip()
            payload = json.loads("\n".join(r.split("\n")[1:]))
        except Exception:  # noqa: BLE001 — upstream parity
            continue
        posts.append((url, payload))
    return posts


def has_post(traj: Trajectory) -> bool:
    """Upstream `check_has_post` — any agent turn mentioning POST at all.

    Deliberately looser than `accepted_posts`: read-only tasks fail if the agent
    so much as attempts a write, even a malformed one.
    """
    return any(i.role == "agent" and "POST" in i.content for i in traj.history)
