"""Whissle agents that run the agent's SAVED FLOW, not a bare brain call.

HOW THIS DIFFERS FROM ``whissle_agent`` / ``whissle_voice_agent``
----------------------------------------------------------------
Those two drive Whissle in *bench mode*: they POST tau2's policy as ``system`` and
tau2's tool schemas as ``tools``, and the platform runs neither its own prompt nor
its own flow. That measures the brain. It cannot measure the product.

This module measures the product. It drives the endpoints that instantiate the
real ``FlowRuntime``:

    text   POST /api/agents/{id}/chat/turn        body {message, conversation_id}
    voice  POST /api/bench/voice/start {real:true}  the deployed pipeline

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
from typing import List, Optional

import requests
from loguru import logger
from pydantic import BaseModel, ConfigDict

from tau2.agent.base_agent import HalfDuplexAgent, ValidAgentInputMessage
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
    ) -> None:
        super().__init__(tools=tools, domain_policy=domain_policy)
        self.base = (os.getenv("WHISSLE_BASE") or DEFAULT_BASE).rstrip("/")
        self.agent_id = os.getenv("WHISSLE_AGENT_ID")
        self.api_key = os.getenv("WHISSLE_API_KEY")
        if not self.agent_id or not self.api_key:
            raise ValueError("WHISSLE_AGENT_ID and WHISSLE_API_KEY are required")
        self.bridge = bridge
        self._pending: list[Message] = []
        self.flow_steps: list[dict] = []

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
    """TEXT: one ``/chat/turn`` per user turn, on the agent's saved flow."""

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
        return AssistantMessage(
            role="assistant",
            content=reply or "I'm sorry, could you say that again?",
        ), state

    def _chat_turn(self, message: str, conversation_id: Optional[str]) -> dict:
        body: dict = {"message": message}
        if conversation_id:
            body["conversation_id"] = conversation_id
        last = "unknown"
        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{self.base}/api/agents/{self.agent_id}/chat/turn",
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
        raise RuntimeError(f"chat/turn failed after retries: {last}")


def create_whissle_flow_agent(tools, domain_policy, **kwargs):
    """Factory. ``environment`` arrives from :func:`tau2.runner.build.build_agent`.

    The bridge is built over that exact instance, so the tools Whissle calls and
    the database tau2 scores are the same object.
    """
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
    return WhissleFlowAgent(tools=tools, domain_policy=domain_policy, bridge=bridge)


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
