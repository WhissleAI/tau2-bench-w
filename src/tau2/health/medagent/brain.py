"""The Whissle brain, driven over POST {WHISSLE_BASE}/api/bench/agent-turn.

Same auth, retry and error shape as `tau2.agent.whissle_agent.WhissleAgent`,
with two differences that matter for this benchmark:

* MedAgentBench is a **text** protocol, so we send no tool schemas and read
  `reply` rather than `tool_calls`. (`/api/bench/agent-turn` executes nothing
  server-side in any case — the caller owns the environment.)
* Transport failures are raised as `BrainInfraError` so the runner can classify
  the episode `infra_fail` and exclude it from scores, rather than scoring a
  network outage as a wrong clinical answer.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

import requests

DEFAULT_BASE = "https://aws-gateway-backend.whissle.ai/bot"

# A minimal system prompt whose only job is to stop the platform agent's own
# persona from overriding the benchmark protocol. Upstream sends no system
# prompt at all; passing `system=None` here would let the configured agent's
# prompt, KB and company brain layer in, which is not what mode A measures.
NEUTRAL_SYSTEM = (
    "You are completing a structured evaluation task. Follow the protocol in "
    "the user's message exactly: reply with exactly one action per turn, in one "
    "of the three specified formats, and no other text, commentary, or "
    "formatting."
)


class BrainInfraError(RuntimeError):
    """The Whissle endpoint could not be reached, or answered unusably.

    Distinct from a bad clinical answer: the session was never measured, so the
    caller must record it as `infra_fail` and drop it from the denominator.
    """


class WhissleBrain:
    """One-shot, stateless turn taker against the bench endpoint."""

    def __init__(
        self,
        *,
        base: Optional[str] = None,
        agent_id: Optional[str] = None,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        system_mode: str = "neutral",
        max_tokens: int = 1024,
        timeout: float = 120.0,
        retries: int = 3,
    ):
        self.base = (base or os.getenv("WHISSLE_BASE") or DEFAULT_BASE).rstrip("/")
        self.agent_id = agent_id or os.getenv("WHISSLE_AGENT_ID")
        self.api_key = api_key or os.getenv("WHISSLE_API_KEY")
        self.model = model or os.getenv("WHISSLE_MODEL") or None
        if not self.agent_id or not self.api_key:
            raise ValueError("WHISSLE_AGENT_ID and WHISSLE_API_KEY are required")
        if system_mode not in ("neutral", "prompt-as-system", "agent-default"):
            raise ValueError(f"unknown system_mode: {system_mode!r}")
        self.system_mode = system_mode
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.retries = retries

    def describe(self) -> dict[str, Any]:
        """Run-metadata block, so a result file says what was measured."""
        return {
            "base": self.base,
            "agent_id": self.agent_id,
            "model": self.model or "(agent default)",
            "system_mode": self.system_mode,
            "max_tokens": self.max_tokens,
            "endpoint": "/api/bench/agent-turn",
        }

    def system_for(self, task_prompt: str) -> Optional[str]:
        if self.system_mode == "neutral":
            return NEUTRAL_SYSTEM
        if self.system_mode == "prompt-as-system":
            return task_prompt
        return None  # agent-default: the platform agent's own prompt applies

    def turn(self, messages: list[dict], system: Optional[str]) -> str:
        """Return the agent's next reply as plain text.

        Raises `BrainInfraError` on transport failure, persistent 5xx, or a
        response with no usable text.
        """
        body: dict[str, Any] = {
            "agent_id": self.agent_id,
            "messages": messages,
            "tools": [],
            "max_tokens": self.max_tokens,
        }
        if system is not None:
            body["system"] = system
        if self.model:
            body["model"] = self.model

        last = "unknown"
        for attempt in range(self.retries):
            try:
                r = requests.post(
                    f"{self.base}/api/bench/agent-turn",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    data=json.dumps(body),
                    timeout=self.timeout,
                )
                if r.status_code >= 500:
                    last = f"{r.status_code} {r.text[:200]}"
                    time.sleep(2 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    # 4xx is a configuration fault (bad key, agent not in org).
                    # Retrying cannot fix it — fail fast and loudly.
                    raise BrainInfraError(
                        f"bench agent-turn rejected the request: "
                        f"{r.status_code} {r.text[:300]}"
                    )
                return _extract_text(r.json())
            except BrainInfraError:
                raise
            except requests.exceptions.RequestException as e:
                last = str(e)
                time.sleep(2 * (attempt + 1))
            except ValueError as e:  # undecodable JSON body
                last = f"malformed response: {e}"
                time.sleep(2 * (attempt + 1))
        raise BrainInfraError(f"bench agent-turn failed after retries: {last}")


def _extract_text(payload: dict[str, Any]) -> str:
    """Pull plain text out of the bench response.

    Prefers `reply`; falls back to concatenating text blocks in `content` so a
    provider that only fills the raw block list still works.
    """
    reply = (payload.get("reply") or "").strip()
    if reply:
        return reply
    blocks = payload.get("content") or []
    if isinstance(blocks, list):
        text = "\n".join(
            b.get("text", "")
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        ).strip()
        if text:
            return text
    # An empty reply is not a clinical failure — the brain produced nothing.
    raise BrainInfraError(
        f"bench agent-turn returned no text (stop_reason="
        f"{payload.get('stop_reason')!r})"
    )
