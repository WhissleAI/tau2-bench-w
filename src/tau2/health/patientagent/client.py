"""Low-level HTTP client for the Whissle bench turn endpoint.

One turn of the real Whissle platform agent:

    POST {WHISSLE_BASE}/api/bench/agent-turn
      {"agent_id", "system", "messages"[anthropic-style], "tools"[], "model"?}
    -> {"reply": str, "content": [blocks], "tool_calls": [...], "stop_reason": str}

This mirrors ``tau2/agent/whissle_agent.py`` (same endpoint, same auth, same retry
shape) but splits the transport out from the agent so the PatientAgentBench adapter
can reuse it from a *different* process/venv: this module imports nothing beyond the
stdlib and ``requests``, so it drops cleanly into the PatientAgentBench environment.

Error taxonomy matters for scoring. A conversation that dies because our endpoint
5xx'd or timed out is an INFRASTRUCTURE failure, not an agent quality signal, and
must be excluded from the published means rather than scored as a bad conversation
(see ``scoring.py``). So transport faults raise ``WhissleInfraError`` while config
and request faults raise ``WhissleRequestError`` and fail the run loudly.
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests

DEFAULT_BASE = "https://aws-gateway-backend.whissle.ai/bot"
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_MAX_ATTEMPTS = 3


class WhissleError(RuntimeError):
    """Base class for Whissle bench-endpoint failures."""


class WhissleRequestError(WhissleError):
    """A 4xx / malformed-response fault. Deterministic — retrying will not help.

    These are our bugs or our misconfiguration (bad agent id, bad tool schema, bad
    key). They fail the run loudly rather than being silently excluded, because
    excluding them would hide a broken adapter behind a smaller N.
    """


class WhissleAuthError(WhissleRequestError):
    """401/403 — missing or wrong ``WHISSLE_API_KEY`` for this agent's org."""


class WhissleInfraError(WhissleError):
    """A transport fault that survived retries (5xx, timeout, connection reset).

    Sessions that die this way are classified ``infra_fail`` and EXCLUDED from
    scores. Never let this be counted as a low-quality conversation.
    """


@dataclass
class WhissleConfig:
    """Connection settings, resolved from the environment by default."""

    base: str = field(default_factory=lambda: (os.getenv("WHISSLE_BASE") or DEFAULT_BASE))
    agent_id: str = field(default_factory=lambda: os.getenv("WHISSLE_AGENT_ID", ""))
    api_key: str = field(default_factory=lambda: os.getenv("WHISSLE_API_KEY", ""))
    model: Optional[str] = field(default_factory=lambda: os.getenv("WHISSLE_MODEL") or None)
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_attempts: int = DEFAULT_MAX_ATTEMPTS

    def __post_init__(self) -> None:
        self.base = (self.base or DEFAULT_BASE).rstrip("/")

    def require(self) -> "WhissleConfig":
        missing = [
            name
            for name, value in (("WHISSLE_AGENT_ID", self.agent_id), ("WHISSLE_API_KEY", self.api_key))
            if not value
        ]
        if missing:
            raise WhissleRequestError(
                f"{' and '.join(missing)} must be set. WHISSLE_AGENT_ID is any agent "
                "in the key's org (fetch one with GET {base}/api/agents); in "
                "harness-tools mode the agent's own prompt and tools are overridden "
                "by the benchmark, so the agent only selects the org and model."
            )
        return self


@dataclass
class TurnResponse:
    """One decoded agent turn. ``text`` and ``tool_calls`` may BOTH be populated —
    the endpoint emits a preamble alongside parallel tool calls."""

    text: str
    tool_calls: list[dict[str, Any]]
    stop_reason: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)


class WhissleBenchClient:
    """Thin, retrying client for ``/api/bench/agent-turn``."""

    def __init__(self, config: Optional[WhissleConfig] = None, session: Optional[Any] = None) -> None:
        self.config = (config or WhissleConfig()).require()
        # Injectable for tests; a real Session also gives us connection reuse.
        self._session = session if session is not None else requests.Session()

    @property
    def url(self) -> str:
        return f"{self.config.base}/api/bench/agent-turn"

    def turn(
        self,
        messages: list[dict[str, Any]],
        *,
        system: Optional[str] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        model: Optional[str] = None,
    ) -> TurnResponse:
        """Run one agent turn. Raises ``WhissleInfraError`` if the transport never
        recovered, ``WhissleRequestError`` for deterministic faults."""
        body: dict[str, Any] = {"agent_id": self.config.agent_id, "messages": messages}
        if system:
            body["system"] = system
        # An explicit empty list is meaningful: "this agent gets NO harness tools"
        # (native mode uses the agent's own registered tools), so only omit on None.
        if tools is not None:
            body["tools"] = tools
        chosen_model = model or self.config.model
        if chosen_model:
            body["model"] = chosen_model

        payload = self._post_with_retries(body)
        return self._decode(payload)

    # -- transport ---------------------------------------------------------------

    def _post_with_retries(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        data = json.dumps(body)
        last_error = "unknown"

        for attempt in range(self.config.max_attempts):
            try:
                response = self._session.post(
                    self.url, headers=headers, data=data, timeout=self.config.timeout_s
                )
            except requests.exceptions.RequestException as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                self._backoff(attempt)
                continue

            status = response.status_code
            if status in (401, 403):
                raise WhissleAuthError(
                    f"{status} from {self.url} — WHISSLE_API_KEY is not valid for "
                    f"agent {self.config.agent_id[:8]}…'s org"
                )
            if status == 404:
                raise WhissleRequestError(
                    f"404 from {self.url} — agent {self.config.agent_id[:8]}… is not "
                    "in this key's org, or the bench endpoint is not deployed"
                )
            # 429 and 5xx are transient: back off and retry.
            if status == 429 or status >= 500:
                last_error = f"HTTP {status}: {response.text[:200]}"
                self._backoff(attempt, retry_after=response.headers.get("Retry-After"))
                continue
            if status >= 400:
                raise WhissleRequestError(f"HTTP {status} from {self.url}: {response.text[:300]}")

            try:
                payload = response.json()
            except ValueError as exc:
                # A 200 with an unparseable body is a broken gateway, not a bad
                # request — treat it as transient and retry.
                last_error = f"non-JSON 200 response: {exc}"
                self._backoff(attempt)
                continue
            if not isinstance(payload, dict):
                raise WhissleRequestError(f"expected a JSON object, got {type(payload).__name__}")
            return payload

        raise WhissleInfraError(
            f"bench agent-turn failed after {self.config.max_attempts} attempts: {last_error}"
        )

    def _backoff(self, attempt: int, retry_after: Optional[str] = None) -> None:
        """Exponential backoff with jitter, honouring Retry-After when sane."""
        if retry_after:
            try:
                time.sleep(min(float(retry_after), 30.0))
                return
            except (TypeError, ValueError):
                pass
        delay = min(2.0 * (2**attempt), 20.0)
        time.sleep(delay + random.uniform(0, 0.5))

    # -- decoding ----------------------------------------------------------------

    @staticmethod
    def _decode(payload: dict[str, Any]) -> TurnResponse:
        """Normalize a turn payload.

        ``tool_calls`` is the endpoint's flattened view; ``content`` is the raw
        Anthropic block list. We prefer ``tool_calls`` and fall back to scanning
        ``content`` for ``tool_use`` blocks so a payload carrying only blocks still
        drives the ReAct loop.
        """
        text = (payload.get("reply") or "").strip()
        blocks = payload.get("content") or []

        raw_calls = payload.get("tool_calls")
        if not raw_calls and isinstance(blocks, list):
            raw_calls = [
                {"id": b.get("id"), "name": b.get("name"), "arguments": b.get("input") or {}}
                for b in blocks
                if isinstance(b, dict) and b.get("type") == "tool_use"
            ]

        tool_calls: list[dict[str, Any]] = []
        for index, call in enumerate(raw_calls or []):
            if not isinstance(call, dict) or not call.get("name"):
                continue
            # ``arguments`` is the endpoint's field; ``input`` appears on raw blocks.
            args = call.get("arguments")
            if args is None:
                args = call.get("input")
            if isinstance(args, str):
                # Defensive: some providers stringify tool arguments.
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {"_raw": args}
            tool_calls.append(
                {
                    "id": call.get("id") or f"call_{index}",
                    "name": call["name"],
                    "arguments": args if isinstance(args, dict) else {},
                }
            )

        # If reply was empty, recover any text blocks so we never drop a spoken turn.
        if not text and isinstance(blocks, list):
            text = " ".join(
                b.get("text", "")
                for b in blocks
                if isinstance(b, dict) and b.get("type") == "text"
            ).strip()

        return TurnResponse(
            text=text,
            tool_calls=tool_calls,
            stop_reason=payload.get("stop_reason"),
            raw=payload,
        )
