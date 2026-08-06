# Copyright Sierra
"""Thin HTTP client for driving a Whissle agent's conversation FLOW over the text
channel — the deterministic surface for exercising the in-call state machine.

This is the flow-suite analogue of ``voice/transcription/transcribe.py``: it talks
to the SAME product surface a customer hits, never a raw provider. Auth + base URL
come from the environment (``WHISSLE_API_KEY`` + ``WHISSLE_BASE``), matching
``run_transcribe.sh`` / ``run_flow.sh``.

Endpoints used (all under ``$WHISSLE_BASE``):
  POST   /api/agents                          create a throwaway agent
  GET    /api/agents/{id}[?include=draft]       read an agent (incl. its ``flow``)
  PATCH  /api/agents/{id}[?target=draft]       author a flow (``{"flow": {...}}``)
  POST   /api/agents/{id}/publish              promote the draft overlay to live
  POST   /api/agents/{id}/draft/discard        drop the draft overlay
  POST   /api/agents/{id}/flow/validate        dry-run flow validation (no save)
  POST   /api/agents/{id}/chat/turn            drive one text turn
  GET    /api/agents/{id}/flow/trace           full accumulated step trace
  DELETE /api/agents/{id}[?confirm=true]        teardown (never leave agents behind)

The text channel is deliberate: it drives the exact same ``FlowRuntime`` state
machine the voice pipeline runs (services/flow/text_runner.py), with zero audio
nondeterminism — so a state-sequence assertion is meaningful and repeatable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_BASE = "https://aws-gateway-backend.whissle.ai/bot"


class FlowClientError(RuntimeError):
    """A non-2xx from the backend, carrying status + body for the run log."""

    def __init__(self, action: str, status: int, body: str) -> None:
        super().__init__(f"{action} -> HTTP {status}: {body[:300]}")
        self.action = action
        self.status = status
        self.body = body


@dataclass
class TurnResult:
    """One ``chat/turn`` response, normalized for the runner's assertions."""

    reply: str
    conversation_id: str
    tools_used: list[str]
    tool_events: list[dict[str, Any]] = field(default_factory=list)
    # The flow step-trace lands with the parallel backend PR "flow-step-trace".
    # Until it is deployed, ``flow`` is None and every trace-dependent assertion
    # degrades to SKIPPED-pending-trace (the runner keys off ``flow_present``).
    flow: Optional[dict[str, Any]] = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def flow_present(self) -> bool:
        return isinstance(self.flow, dict)

    @property
    def steps(self) -> list[dict[str, Any]]:
        return list(self.flow.get("steps") or []) if self.flow_present else []

    @property
    def current_state(self) -> Optional[str]:
        return self.flow.get("current_state") if self.flow_present else None


class FlowClient:
    """A session against one Whissle org, keyed by a ``wsk_`` secret key."""

    def __init__(self, base: Optional[str] = None, api_key: Optional[str] = None,
                 timeout: float = 120.0) -> None:
        self.base = (base or os.getenv("WHISSLE_BASE") or DEFAULT_BASE).rstrip("/")
        self.api_key = api_key or os.getenv("WHISSLE_API_KEY") or ""
        if not self.api_key:
            raise RuntimeError(
                "WHISSLE_API_KEY not set — put a wsk_ key in .env (see run_flow.sh)."
            )
        self.timeout = timeout
        self._s = requests.Session()
        self._s.headers.update({"Authorization": f"Bearer {self.api_key}"})

    # ── low level ────────────────────────────────────────────────────────────

    def _req(self, method: str, path: str, *, json: Any = None,
             action: str = "") -> requests.Response:
        r = self._s.request(
            method, f"{self.base}{path}", json=json, timeout=self.timeout,
        )
        if r.status_code >= 300:
            raise FlowClientError(action or f"{method} {path}", r.status_code, r.text)
        return r

    # ── agent lifecycle ──────────────────────────────────────────────────────

    def whoami(self) -> dict[str, Any]:
        return self._req("GET", "/api/whoami", action="whoami").json()

    def create_agent(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Create a throwaway agent. ``spec`` needs at least name + system_prompt;
        ``agent_type: text_assistant`` is the right pick for text-channel driving."""
        body = {"agent_type": "text_assistant", **spec}
        return self._req("POST", "/api/agents", json=body, action="create_agent").json()

    def create_typed_agent(
        self, name: str, agent_type: str, system_prompt: str,
    ) -> dict[str, Any]:
        """Create a throwaway agent of a specific seeded ``agent_type`` and supply
        **no flow** — the backend auto-attaches that type's default
        ``prompts/agent_types/<type>/flow.json`` on creation. Used by the
        default-flow coverage suite to prove the loader auto-attach works live.

        ``system_prompt`` is a required field on the create endpoint even though the
        type ships its own prompt; it is not a flow and does not defeat the point."""
        body = {"name": name, "agent_type": agent_type, "system_prompt": system_prompt}
        return self._req(
            "POST", "/api/agents", json=body, action="create_typed_agent",
        ).json()

    def get_agent(self, agent_id: str,
                  include: Optional[str] = None) -> dict[str, Any]:
        """Read an agent, including its (possibly auto-attached) ``flow``.
        ``include="draft"`` also returns the pending edit overlay the studio
        stages before publish: ``{...live, draft: {...}|null, has_draft: bool}``."""
        path = f"/api/agents/{agent_id}"
        if include:
            path += f"?include={include}"
        return self._req("GET", path, action="get_agent").json()

    def set_flow(self, agent_id: str, flow: dict[str, Any], *,
                 target: str = "live") -> dict[str, Any]:
        """Author a flow onto an agent — the EXACT call the flow-designer UI makes
        (``PATCH /api/agents/{id}`` with ``{"flow": ...}``). ``target="live"``
        (default) writes the columns the call pipeline reads; ``target="draft"``
        stages the edit in the pending overlay that NO live call sees until
        :meth:`publish`. A malformed/unsafe flow is a 422 on the live path (the
        backend validates at write time), surfaced as a FlowClientError."""
        return self._req(
            "PATCH", f"/api/agents/{agent_id}?target={target}",
            json={"flow": flow}, action=f"set_flow[{target}]",
        ).json()

    def publish(self, agent_id: str) -> dict[str, Any]:
        """Promote the staged draft overlay onto the LIVE agent — the studio's
        Publish button (``POST /api/agents/{id}/publish``). This is the moment an
        author's edits reach real calls."""
        return self._req(
            "POST", f"/api/agents/{agent_id}/publish", action="publish",
        ).json()

    def discard_draft(self, agent_id: str) -> dict[str, Any]:
        """Throw the pending draft away (``POST /api/agents/{id}/draft/discard``);
        idempotent, live agent untouched."""
        return self._req(
            "POST", f"/api/agents/{agent_id}/draft/discard", action="discard_draft",
        ).json()

    def validate_flow(self, agent_id: str, flow: dict[str, Any]) -> dict[str, Any]:
        """Dry-run validation of a candidate flow (``POST /flow/validate``) — the
        same validator a live PATCH / publish runs, nothing saved. Returns
        ``{ok, error?, warnings, compliance}``."""
        return self._req(
            "POST", f"/api/agents/{agent_id}/flow/validate", json={"flow": flow},
            action="validate_flow",
        ).json()

    def delete_agent(self, agent_id: str, *, confirm: bool = False) -> None:
        """Teardown. ``confirm=True`` also consents to deleting the agent's KB
        documents (the backend requires it when the agent owns KB docs)."""
        path = f"/api/agents/{agent_id}"
        if confirm:
            path += "?confirm=true"
        self._req("DELETE", path, action="delete_agent")

    # ── driving a conversation ───────────────────────────────────────────────

    def turn(self, agent_id: str, message: str,
             conversation_id: Optional[str] = None) -> TurnResult:
        body: dict[str, Any] = {"message": message}
        if conversation_id:
            body["conversation_id"] = conversation_id
        d = self._req(
            "POST", f"/api/agents/{agent_id}/chat/turn", json=body, action="turn",
        ).json()
        return TurnResult(
            reply=d.get("reply") or "",
            conversation_id=d.get("conversation_id") or conversation_id or "",
            tools_used=list(d.get("tools_used") or []),
            tool_events=list(d.get("tool_events") or []),
            flow=d.get("flow") if isinstance(d.get("flow"), dict) else None,
            raw=d,
        )

    def get_trace(self, agent_id: str,
                  conversation_id: str) -> Optional[dict[str, Any]]:
        """The full accumulated ``{steps:[...]}`` trace, or None until the
        step-trace PR is deployed (the endpoint 404s before then)."""
        try:
            r = self._req(
                "GET",
                f"/api/agents/{agent_id}/flow/trace?conversation_id={conversation_id}",
                action="get_trace",
            )
        except FlowClientError as e:
            if e.status == 404:
                return None
            raise
        return r.json()
