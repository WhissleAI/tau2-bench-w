"""Whissle agents that run the SAVED AGENT product path, not a bare brain call.

HOW THIS DIFFERS FROM ``whissle_agent`` / ``whissle_voice_agent``
----------------------------------------------------------------
Those two drive Whissle in *bench mode*: they POST tau2's policy as ``system`` and
tau2's tool schemas as ``tools``, and the platform runs neither its own prompt nor
its own flow. That measures the brain. It cannot measure the product.

This module measures the product. It drives endpoints that load the persisted
agent configuration:

    text   POST /api/agents/{id}/chat/turn          saved prompt + KB + tools
    voice  POST /api/bench/voice/start {real:true}  deployed voice pipeline

The text endpoint is only described as a saved *flow* when its response carries
flow trace evidence. The checked local backend loads the saved prompt, native KB
and attached tools on this route, but does not instantiate ``FlowRuntime``.

Neither accepts per-request tools — verified against the live backend, where an
injected ``tools`` array was accepted by the HTTP layer and then dropped, with the
trace showing ``tools_gated allowed:[search_knowledge_base]``. So the tools reach
the flow the supported way instead: registered as Whissle **custom HTTP tools**
attached to the agent, pointing at :mod:`tau2.bridge.tool_bridge`.

THE CONSEQUENCE FOR SCORING
---------------------------
Whissle executes tools by calling the bridge, so by the time a turn's reply comes
back the environment has *already* mutated. If this agent returned those calls as
``tool_calls``, the orchestrator would route them to the environment and run them a
second time — a second support case, a broken DB hash. So it never does. It returns
the reply only, and hands the already-executed records to the orchestrator through
:meth:`drain_trajectory_records`, which splices them in ahead of the reply.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import List, Optional

import requests
from loguru import logger
from pydantic import BaseModel, ConfigDict

from tau2.agent.base_agent import HalfDuplexAgent, ValidAgentInputMessage
from tau2.bridge.server import ToolBridgeServer
from tau2.bridge.tool_bridge import ToolBridge, build_trajectory_records
from tau2.data_model.message import (
    AssistantMessage,
    Message,
    MultiToolMessage,
    ToolMessage,
    UserMessage,
)
from tau2.environment.tool import Tool

DEFAULT_BASE = "https://aws-gateway-backend.whissle.ai/bot"


class WhissleFlowState(BaseModel):
    """Whissle owns the conversation; tau2 only needs its id to continue it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    conversation_id: Optional[str] = None
    turn: int = 0


class _WhissleFlowBase(HalfDuplexAgent[WhissleFlowState]):
    """Shared plumbing: the bridge handoff and the trajectory contract."""

    def __init__(
        self,
        tools: List[Tool],
        domain_policy: str,
        bridge: Optional[ToolBridge] = None,
        bridge_server: Optional[ToolBridgeServer] = None,
    ) -> None:
        super().__init__(tools=tools, domain_policy=domain_policy)
        self.base = (os.getenv("WHISSLE_BASE") or DEFAULT_BASE).rstrip("/")
        self.agent_id = os.getenv("WHISSLE_AGENT_ID")
        self.api_key = os.getenv("WHISSLE_API_KEY")
        if not self.agent_id or not self.api_key:
            raise ValueError("WHISSLE_AGENT_ID and WHISSLE_API_KEY are required")
        self.bridge = bridge
        self.bridge_server = bridge_server
        self.session_mode = os.getenv("WHISSLE_NATIVE_SESSION_MODE", "studio")
        if self.session_mode not in {"studio", "embed", "isolated_studio"}:
            raise ValueError(
                "WHISSLE_NATIVE_SESSION_MODE must be studio, embed, or isolated_studio"
            )
        self.embed_token: Optional[str] = None
        self.embed_session_id: Optional[str] = None
        if self.session_mode in {"embed", "isolated_studio"}:
            self.embed_token = self._mint_embed_token()
            self.embed_session_id = f"tau2-{uuid.uuid4()}"
        self._last_endpoint_path = f"/api/agents/{self.agent_id}/chat/turn"
        self._pending: list[Message] = []
        self.flow_steps: list[dict] = []

    def _mint_embed_token(self) -> str:
        """Mint a short-lived server-trusted token for one isolated trial."""
        resp = requests.post(
            f"{self.base}/api/embed/session-token",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={"api_key": self.api_key, "agent_id": self.agent_id},
            timeout=30,
        )
        resp.raise_for_status()
        token = resp.json().get("token")
        if not token:
            raise RuntimeError("Whissle embed token response did not contain a token")
        return token

    def _endpoint_path(self, conversation_id: Optional[str] = None) -> str:
        if self.session_mode == "embed" or (
            self.session_mode == "isolated_studio" and not conversation_id
        ):
            return "/api/embed/chat/turn"
        return f"/api/agents/{self.agent_id}/chat/turn"

    # ── the trajectory contract ──────────────────────────────────────────────

    def drain_trajectory_records(self) -> list[Message]:
        """Messages the orchestrator must insert BEFORE this turn's reply.

        Returns the (tool-call, tool-result) pairs the bridge executed during the
        turn that just completed, in bridge-execution order. Empty when the flow
        called no tools. The orchestrator calls this via ``hasattr``, so agents
        that execute their own tools are unaffected.
        """
        pending, self._pending = self._pending, []
        return pending

    def _collect_bridge_records(self) -> None:
        if self.bridge is None:
            return
        fresh = self.bridge.drain()
        if fresh:
            logger.info(
                "flow turn executed {} bridge tool call(s): {}",
                len(fresh),
                ", ".join(r.tool_name for r in fresh),
            )
            self._pending.extend(build_trajectory_records(fresh))

    def get_init_state(
        self, message_history: Optional[list[Message]] = None
    ) -> WhissleFlowState:
        return WhissleFlowState()

    def stop(self, message=None, state=None) -> None:
        """Stop the local HTTP bridge even when a simulation fails."""
        if self.bridge_server is not None:
            server, self.bridge_server = self.bridge_server, None
            server.stop()

    @staticmethod
    def _incoming_text(message: ValidAgentInputMessage) -> Optional[str]:
        """The user's words for this turn, or None if the turn carried no speech.

        Tool results arriving here are informational only: on this path the tools
        already ran inside Whissle, so there is nothing to forward and nothing to
        wait for.
        """
        if isinstance(message, UserMessage):
            return message.content or ""
        if isinstance(message, (ToolMessage, MultiToolMessage)):
            return None
        return getattr(message, "content", None)


class WhissleFlowAgent(_WhissleFlowBase):
    """TEXT: one ``/chat/turn`` per user turn on the saved Whissle agent."""

    def generate_next_message(
        self, message: ValidAgentInputMessage, state: WhissleFlowState
    ) -> tuple[AssistantMessage, WhissleFlowState]:
        text = self._incoming_text(message)
        if text is None:
            # Nothing to say and nothing to send; keep the turn structure valid.
            self._collect_bridge_records()
            return AssistantMessage(role="assistant", content=""), state

        data = self._chat_turn(text, state.conversation_id)
        state.conversation_id = data.get("conversation_id") or state.conversation_id
        state.turn += 1

        flow = data.get("flow") or {}
        steps = flow.get("steps") or []
        if steps:
            self.flow_steps.extend(steps)

        # Order matters: drain the bridge first so the tools this turn triggered
        # are queued ahead of the reply that describes them.
        self._collect_bridge_records()

        reply = (data.get("reply") or "").strip()
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
        served_model = data.get("served_model") or data.get("model")
        return AssistantMessage(
            role="assistant",
            content=reply or "I'm sorry, could you say that again?",
            usage=usage,
            raw_data={
                "endpoint": self._last_endpoint_path,
                "agent_surface": "native_saved_agent",
                "saved_agent_id": self.agent_id,
                "platform_session_id": state.conversation_id,
                "conversation_id": state.conversation_id,
                "served_model": served_model,
                "served_models": data.get("served_models")
                or ([served_model] if served_model else []),
                "stop_reasons": data.get("stop_reasons") or [],
                "flow_trace_present": bool(steps),
                "flow_steps": steps,
            },
        ), state

    def _chat_turn(self, message: str, conversation_id: Optional[str]) -> dict:
        endpoint_path = self._endpoint_path(conversation_id)
        self._last_endpoint_path = endpoint_path
        if endpoint_path == "/api/embed/chat/turn":
            body: dict = {
                "token": self.embed_token,
                "message": message,
                "session_id": self.embed_session_id,
            }
        else:
            body = {"message": message}
            if conversation_id:
                body["conversation_id"] = conversation_id
        last = "unknown"
        attempts = 1 if os.getenv("WHISSLE_NATIVE_BENCHMARK") == "1" else 3
        for attempt in range(attempts):
            try:
                resp = requests.post(
                    f"{self.base}{endpoint_path}",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                    timeout=180,
                )
                if resp.status_code >= 500:
                    last = f"{resp.status_code} {resp.text[:160]}"
                    time.sleep(2 * (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as exc:
                last = str(exc)
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"chat/turn failed after {attempts} attempt(s): {last}")


def create_whissle_flow_agent(tools, domain_policy, **kwargs):
    """Factory. ``environment`` arrives from :func:`tau2.runner.build.build_agent`.

    The bridge is built over that exact instance, so the tools Whissle calls and
    the database tau2 scores are the same object.
    """
    from tau2.bridge.tool_bridge import BRIDGE_TOKEN_ENV

    environment = kwargs.get("environment")
    bridge = None
    bridge_server = None
    if environment is not None:
        token = os.getenv(BRIDGE_TOKEN_ENV)
        if token:
            bridge = ToolBridge(environment=environment, token=token)
            if os.getenv("WHISSLE_NATIVE_BENCHMARK") == "1":
                if not os.getenv("TAU_BRIDGE_PUBLIC_URL"):
                    raise ValueError(
                        "TAU_BRIDGE_PUBLIC_URL is required for a native Whissle run"
                    )
                port = int(os.getenv("TAU_BRIDGE_PORT", "8765"))
                bridge_server = ToolBridgeServer(bridge, port=port).start()
        else:
            message = (
                f"{BRIDGE_TOKEN_ENV} is unset — Whissle's tool calls cannot reach "
                "the scored Tau environment"
            )
            if os.getenv("WHISSLE_NATIVE_BENCHMARK") == "1":
                raise ValueError(message)
            logger.warning(message)
    return WhissleFlowAgent(
        tools=tools,
        domain_policy=domain_policy,
        bridge=bridge,
        bridge_server=bridge_server,
    )


# ── voice ───────────────────────────────────────────────────────────────────


def _build_flow_voice_agent_class():
    """Define the voice adapter lazily; voice deps are an optional extra."""
    from tau2.agent.whissle_voice_agent import WhissleVoiceAgent

    class WhissleFlowVoiceAgent(WhissleVoiceAgent):
        """VOICE: the deployed pipeline — STT, saved flow, attached tools, TTS.

        ``real=True`` is the whole difference from :class:`WhissleVoiceAgent`. In
        bench mode the backend takes tau2's prompt and tool schemas and runs no
        flow; in real mode it builds the FlowController into the pipeline and the
        agent uses its OWN prompt, greeting, and attached tools — which is what
        makes this a measurement of the product rather than of the brain.

        Because the attached tools are the custom HTTP tools, Whissle calls the
        bridge directly over the internet. No ``bench-tool-call`` frames arrive,
        and this agent never returns ``tool_calls``.
        """

        def __init__(self, tools, domain_policy, bridge=None):
            super().__init__(tools=tools, domain_policy=domain_policy)
            self.bridge = bridge
            self._pending: list[Message] = []

        def get_init_state(self, message_history=None):
            from tau2.voice.audio_native.whissle.provider import WhissleRoomProvider

            self._tts.require()
            self._bg.start()
            self.provider = WhissleRoomProvider(self.config)
            # real=True → the deployed agent: own prompt + flow + attached tools.
            self._bg.run_coroutine(
                self.provider.connect(self._system, self._schemas, real=True),
                timeout=self.config.connect_timeout_s + 15,
            )
            logger.info(
                "WhissleFlowVoiceAgent connected (real=True, saved flow) — room={}",
                self.provider.session_id,
            )
            from tau2.agent.whissle_voice_agent import WhissleVoiceState

            return WhissleVoiceState(connected=True, turns=0)

        drain_trajectory_records = _WhissleFlowBase.drain_trajectory_records
        _collect_bridge_records = _WhissleFlowBase._collect_bridge_records

        def generate_next_message(self, message, state):
            am, state = super().generate_next_message(message, state)
            if am.is_tool_call():
                # Should not happen in real mode: the flow's tools are attached
                # HTTP tools it calls itself. Surfacing it loudly beats silently
                # handing the orchestrator calls that would double-execute.
                logger.warning(
                    "real-mode voice returned {} bench tool frame(s); the agent's "
                    "tools should be attached custom HTTP tools calling the bridge",
                    len(am.tool_calls),
                )
            self._collect_bridge_records()
            return am, state

    return WhissleFlowVoiceAgent


def create_whissle_flow_voice_agent(tools, domain_policy, **kwargs):
    """Factory for the saved-flow VOICE agent."""
    from tau2.bridge.tool_bridge import BRIDGE_TOKEN_ENV

    environment = kwargs.get("environment")
    bridge = None
    if environment is not None:
        token = os.getenv(BRIDGE_TOKEN_ENV)
        if token:
            bridge = ToolBridge(environment=environment, token=token)
        else:
            logger.warning(
                "{} is unset — no bridge attached; Whissle's tool calls will not "
                "reach tau2 and nothing will be scored.",
                BRIDGE_TOKEN_ENV,
            )
    cls = _build_flow_voice_agent_class()
    return cls(tools=tools, domain_policy=domain_policy, bridge=bridge)
