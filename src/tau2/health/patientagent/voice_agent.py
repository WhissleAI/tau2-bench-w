"""VOICE mode — run PatientAgentBench scenarios through Whissle's real speech stack.

Every published PatientAgentBench baseline is text. This drives the identical
scenarios, the identical patient simulator and the identical jury over an ACTUAL
audio path: the simulated patient's turn is synthesized to speech, published into a
LiveKit room, heard by the deployed agent's own STT, answered by its own brain and
spoken back by its own TTS, and the resulting transcript is what gets scored.

That makes the text-vs-voice delta a measurement of the speech pipeline itself,
which is a number only an outfit that owns its ASR can produce.

Three honesty constraints are wired in rather than left to the write-up:

1. **Voice is necessarily AGENT-TOOLS mode.** The voice pipeline runs the agent as
   deployed (``real=true``) with its own prompt, flow and tools; there is no way to
   inject the benchmark's 15 sandbox tools into a live call. So a voice run's only
   valid comparator is the TEXT ``agent_tools`` run — never the harness-tools number
   and never the paper's leaderboard. ``scoring.compare_runs`` refuses to present a
   cross-mode delta without a warning, and this class declares its mode so the
   report cannot mislabel it.

2. **Infra failures are excluded, not scored.** A dead data channel or a failed room
   join produces ``VoiceInfraError`` -> ``infra_fail`` -> dropped from the means. A
   voice run that silently scored these as bad conversations would understate the
   pipeline exactly where it was not measured.

3. **The greeting is kept in the transcript.** Real calls open with the agent
   speaking, before the patient says anything. That speech is real, gets graded, and
   is left in the transcript rather than trimmed to flatter the comparison.
"""

from __future__ import annotations

import os
import weakref
from typing import Any, Optional, Sequence

from tau2.health.patientagent.agents import AGENT_TOOLS_MODE
from tau2.health.patientagent.scoring import INFRA_MARKER

DEFAULT_ARTIFACT_DIR = "results/whissle/patientagentbench/voice_audio"


def build_voice_agent_class() -> type:
    """Construct the voice agent against the lazily imported PAB base class."""
    from patient_agent_bench.assistant_agent.base import BaseAssistantAgent
    from patient_agent_bench.assistant_agent.default_agent import AssistantAgentError

    class WhissleVoiceAgent(BaseAssistantAgent):  # type: ignore[misc,valid-type]
        """One live voice session per conversation.

        ``create_assistant_agent_from_spec`` is called inside the benchmark's
        per-conversation function, so one instance == one call, which is what lets a
        stateful audio session live on the agent object.
        """

        NAME = "whissle-voice"
        WHISSLE_MODE = AGENT_TOOLS_MODE

        def __init__(
            self,
            model_config: Any,
            current_datetime: str,
            tools: Optional[Sequence[Any]] = None,
            prompt_name: Optional[str] = None,
            system_prompt: Optional[str] = None,
            role_arn: Optional[str] = None,
        ) -> None:
            if not current_datetime:
                raise ValueError("current_datetime is required")
            self.model_config = model_config
            self.current_datetime = current_datetime
            self.tools = []  # never bound: a live call uses the agent's own tools
            self.agent_id = os.getenv("WHISSLE_AGENT_ID", "")
            self.artifact_dir = os.getenv("PAB_VOICE_ARTIFACT_DIR", DEFAULT_ARTIFACT_DIR)
            self.transcribe_evidence = os.getenv("PAB_VOICE_REASR", "0") == "1"

            self._transport: Any = None
            self._greeting: str = ""
            self._started = False
            self._closed = False
            self.session_meta: dict[str, Any] = {}
            self._finalizer: Optional[weakref.finalize] = None

        # -- session lifecycle ---------------------------------------------------

        def _start(self) -> None:
            """Join the room and capture the greeting. Imported lazily because the
            voice transport pulls LiveKit, which the text path must not require."""
            from tau2.flow.voice_transport import VoiceInfraError, VoiceTransport

            self._voice_infra_error = VoiceInfraError
            transport = VoiceTransport(self.agent_id)
            try:
                self._greeting = transport.start() or ""
            except VoiceInfraError as exc:
                transport.stop()
                raise AssistantAgentError(f"{INFRA_MARKER} voice start failed: {exc}") from exc
            except Exception as exc:
                transport.stop()
                raise AssistantAgentError(f"{INFRA_MARKER} voice start failed: {exc}") from exc

            self._transport = transport
            self._started = True
            self.session_meta = {
                "room": getattr(transport, "room", None),
                "conversation_id": getattr(transport, "conversation_id", None),
                "greeting": self._greeting,
            }
            # Never leak a live room if the harness abandons this conversation.
            self._finalizer = weakref.finalize(self, _stop_transport, transport)

        def close(self) -> dict[str, Any]:
            """Write the duplex audio evidence and tear the session down. Safe to
            call twice; the second call is a no-op."""
            if self._closed or self._transport is None:
                return self.session_meta
            self._closed = True
            transport = self._transport
            try:
                room = getattr(transport, "room", None) or "session"
                os.makedirs(self.artifact_dir, exist_ok=True)
                prefix = os.path.join(self.artifact_dir, str(room))
                self.session_meta["evidence"] = transport.finish(
                    prefix, transcribe=self.transcribe_evidence
                )
                self.session_meta["latencies_ms"] = list(getattr(transport, "latencies_ms", []))
            except Exception as exc:  # evidence is best-effort, never fail the run
                self.session_meta["evidence_error"] = str(exc)
            finally:
                _stop_transport(transport)
                if self._finalizer is not None:
                    self._finalizer.detach()
                self._transport = None
            return self.session_meta

        # -- the benchmark's per-turn contract -----------------------------------

        def invoke(self, messages: list[Any], user_profile: str) -> dict[str, Any]:
            from langchain_core.messages import AIMessage, HumanMessage

            if not self._started:
                self._start()

            spoken = ""
            for message in reversed(messages):
                if isinstance(message, HumanMessage):
                    spoken = _text_of(message.content)
                    break
            if not spoken:
                raise AssistantAgentError("no user turn to speak")

            try:
                result = self._transport.turn(spoken)
            except Exception as exc:
                infra = getattr(self, "_voice_infra_error", None)
                if infra is not None and isinstance(exc, infra):
                    self.close()
                    raise AssistantAgentError(f"{INFRA_MARKER} voice turn failed: {exc}") from exc
                self.close()
                raise AssistantAgentError(f"{INFRA_MARKER} voice transport error: {exc}") from exc

            reply = (getattr(result, "reply", "") or "").strip()
            raw = dict(getattr(result, "raw", {}) or {})

            new_messages: list[Any] = []
            # The agent really did speak first; keep it in the graded transcript.
            if self._greeting and not self._greeting_emitted:
                new_messages.append(
                    AIMessage(
                        content=self._greeting,
                        response_metadata={"channel": "voice", "kind": "greeting"},
                    )
                )
                self._greeting_emitted = True

            if not reply:
                # Audio flowed but nothing was transcribed: the turn is unmeasurable.
                self.close()
                raise AssistantAgentError(
                    f"{INFRA_MARKER} voice turn produced no transcript "
                    f"(boundary={raw.get('boundary')})"
                )

            self._turn_no += 1
            new_messages.append(
                AIMessage(
                    content=reply,
                    response_metadata={
                        "channel": "voice",
                        "turn": self._turn_no,
                        "latency_ms": raw.get("latency_ms"),
                        "bot_audio_bytes": raw.get("bot_audio_bytes"),
                        "boundary": raw.get("boundary"),
                        "room": self.session_meta.get("room"),
                        "conversation_id": self.session_meta.get("conversation_id"),
                        # The per-turn VOICE signals + whissle-large metadata frames
                        # the transport drained off the data channel this turn
                        # (hesitation / shadow / speculative predictions, and the
                        # acoustic emotion/intent head). These exist ONLY here — the
                        # text channel emits none — and without carrying them on the
                        # message they never reach a persisted artifact, which is
                        # exactly how the voice arm ended up scored but unexplainable.
                        "signals": raw.get("signals") or [],
                        "user_metadata": raw.get("user_metadata") or [],
                        "hesitant_input": bool(raw.get("hesitant_input")),
                        "current_state": getattr(result, "current_state", None),
                        "flow_steps": getattr(result, "steps", None),
                    },
                )
            )
            return {"messages": list(messages) + new_messages}

        _turn_no = 0

        _greeting_emitted = False

        def get_tools(self) -> list[Any]:
            return self.tools

    return WhissleVoiceAgent


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return "" if content is None else str(content)


def _stop_transport(transport: Any) -> None:
    try:
        transport.stop()
    except Exception:  # noqa: BLE001 - teardown must never mask the real error
        pass
