"""Half-duplex VOICE agent for Whissle.

Whissle is a half-duplex CASCADE (STT → LLM+tools → TTS), request/response and
turn-based — exactly like the text `whissle` agent, not a full-duplex audio-native
model (OpenAI Realtime / Gemini Live). So it belongs on tau2's turn-based
`Orchestrator` via `generate_next_message`, NOT the tick-based
`FullDuplexOrchestrator` (which fragments the cascade's turn structure and stops
the LLM from completing tool calls).

Per turn this agent:
  1. synthesizes the user simulator's text to speech (ElevenLabs),
  2. sends that whole utterance to Whissle's real voice pipeline over LiveKit and
     lets Whissle's endpointer detect end-of-turn,
  3. drives Whissle's cascade to completion: if Whissle's LLM calls a tool it is
     delegated over the data channel (bench-tool-call) — the agent returns those
     tool_calls to the orchestrator, which runs them against the environment and
     feeds the result back (next generate_next_message with a ToolMessage), which
     the agent forwards to Whissle (bench-tool-result) so its LLM continues,
  4. when Whissle finishes speaking, returns its spoken transcript (bot-transcription).

This tests Whissle's own STT + LLM + tools + TTS end-to-end, turn by turn — the
faithful model of how Whissle actually runs.

Env (same as the text agent, + a user voice):
  WHISSLE_BASE / WHISSLE_AGENT_ID / WHISSLE_API_KEY
  ELEVENLABS_API_KEY           (user-simulator TTS)
  WHISSLE_USER_VOICE_ID        (an ElevenLabs voice in the account; has a default)
"""
from __future__ import annotations

import os
import re
import time
from typing import List, Optional

import requests
from loguru import logger
from pydantic import BaseModel, ConfigDict

from tau2.agent.base_agent import HalfDuplexAgent, ValidAgentInputMessage
from tau2.data_model.message import (
    AssistantMessage,
    Message,
    MultiToolMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from tau2.environment.tool import Tool
from tau2.voice.audio_native.async_loop import BackgroundAsyncLoop
from tau2.voice.audio_native.whissle.config import WhissleConfig
from tau2.voice.audio_native.whissle.provider import WhissleRoomProvider

_MARKER_RE = re.compile(r"\[\[.*?\]\]")
_DEFAULT_USER_VOICE = "EXAVITQu4vr4xnSDxMaL"  # Sarah — a standard premade voice

AGENT_INSTRUCTION = (
    "You are a customer service agent handling a VOICE CALL. Follow the <policy>. "
    "In each turn you EITHER speak to the customer OR make tool calls — never both. "
    "Complete the customer's request YOURSELF using the tools: as soon as the "
    "customer confirms a change, CALL THE TOOL that performs it — do not merely say "
    "you will. Speech is transcribed, so expect ASR noise in spelled names/emails."
)


def _tool_to_anthropic(t: Tool) -> dict:
    s = t.openai_schema
    fn = s.get("function", s)
    return {
        "name": fn["name"],
        "description": fn.get("description", "") or "",
        "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
    }


class WhissleVoiceState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    connected: bool = False
    turns: int = 0


class WhissleVoiceAgent(HalfDuplexAgent[WhissleVoiceState]):
    # tau2's stop hooks look at this on the returned message.
    STOP_FUNCTION_NAME = "transfer_to_human_agents"

    def __init__(self, tools: List[Tool], domain_policy: str):
        super().__init__(tools=tools, domain_policy=domain_policy)
        self.config = WhissleConfig()
        self.config.require()
        self._schemas = [_tool_to_anthropic(t) for t in tools]
        self._system = (
            f"<instructions>\n{AGENT_INSTRUCTION}\n</instructions>\n"
            f"<policy>\n{domain_policy}\n</policy>"
        )
        self._el_key = os.getenv("ELEVENLABS_API_KEY") or ""
        self._user_voice = os.getenv("WHISSLE_USER_VOICE_ID") or _DEFAULT_USER_VOICE
        # Turn-completion tuning.
        self._max_turn_s = float(os.getenv("WHISSLE_VOICE_MAX_TURN_S", "60"))
        self._quiet_gap_s = float(os.getenv("WHISSLE_VOICE_QUIET_GAP_S", "2.0"))
        self._bg = BackgroundAsyncLoop()
        self.provider: Optional[WhissleRoomProvider] = None

    # -- lifecycle ---------------------------------------------------------------

    def get_init_state(
        self, message_history: Optional[list[Message]] = None
    ) -> WhissleVoiceState:
        # Retail/airline tasks start with no real history; if a first agent greeting
        # or prior turns are present we don't replay them into Whissle (a fresh call),
        # we just start the live session. (These domains carry no pre-history.)
        if message_history:
            real = [m for m in message_history if getattr(m, "content", None)]
            if real:
                logger.warning(
                    "whissle_voice: ignoring {} pre-history message(s) — starting a "
                    "fresh voice call", len(real),
                )
        if not self._el_key:
            raise ValueError("ELEVENLABS_API_KEY is required (user-simulator voice)")
        self._bg.start()
        self.provider = WhissleRoomProvider(self.config)
        self._bg.run_coroutine(
            self.provider.connect(self._system, self._schemas),
            timeout=self.config.connect_timeout_s + 15,
        )
        logger.info("WhissleVoiceAgent connected — room={}", self.provider.session_id)
        return WhissleVoiceState(connected=True, turns=0)

    # -- synthesis ---------------------------------------------------------------

    def _synthesize(self, text: str) -> bytes:
        """User text → PCM16 @ 16kHz via ElevenLabs (raw pcm output)."""
        text = _MARKER_RE.sub("", text).strip()
        if not text:
            return b""
        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/{self._user_voice}"
            f"?output_format=pcm_16000"
        )
        r = requests.post(
            url,
            headers={"xi-api-key": self._el_key, "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_turbo_v2_5"},
            timeout=60,
        )
        r.raise_for_status()
        return r.content  # raw PCM16LE @ 16kHz

    # -- turn driving ------------------------------------------------------------

    def generate_next_message(
        self, message: ValidAgentInputMessage, state: WhissleVoiceState
    ) -> tuple[AssistantMessage, WhissleVoiceState]:
        assert self.provider is not None
        if isinstance(message, UserMessage):
            state.turns += 1
            pcm = self._synthesize(message.content or "")
            if pcm:
                self._bg.run_coroutine(self._send_turn(pcm), timeout=90)
        elif isinstance(message, (ToolMessage, MultiToolMessage)):
            results = (
                message.tool_messages
                if isinstance(message, MultiToolMessage)
                else [message]
            )
            for tm in results:
                self._bg.run_coroutine(
                    self.provider.send_tool_result(tm.id, tm.content or ""), timeout=30
                )
        # Drive Whissle's cascade to its next boundary (a tool call, or done speaking).
        kind, payload = self._await_boundary()
        if kind == "tool":
            am = AssistantMessage(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id=c.get("id"),
                        name=c.get("name"),
                        arguments=c.get("arguments") or {},
                        requestor="assistant",
                    )
                    for c in payload
                ],
            )
        else:
            am = AssistantMessage(
                role="assistant",
                content=(payload or "").strip() or "Sorry, could you repeat that?",
            )
        return am, state

    async def _send_turn(self, pcm: bytes) -> None:
        """Publish the whole user utterance, then a short silence so Whissle's
        endpointer fires end-of-turn."""
        await self.provider.send_audio(pcm)
        await self.provider.send_audio(b"\x00\x00" * int(16000 * 0.4))  # 400ms silence

    def _await_boundary(self) -> tuple[str, object]:
        """Block until Whissle either delegates a tool call or finishes its turn.

        Returns ("tool", [call,...]) or ("text", transcript). Poll the provider's
        thread-safe queues; a turn is 'done' once transcript/audio has arrived and
        then stayed quiet for _quiet_gap_s."""
        assert self.provider is not None
        deadline = time.monotonic() + self._max_turn_s
        transcript_parts: list[str] = []
        last_activity = time.monotonic()
        saw_output = False
        last_audio_total = self.provider.agent_audio_total()
        while time.monotonic() < deadline:
            calls = self.provider.drain_tool_calls()
            if calls:
                return "tool", calls
            texts = self.provider.drain_agent_texts()
            if texts:
                transcript_parts.extend(texts)
                saw_output = True
                last_activity = time.monotonic()
            audio_total = self.provider.agent_audio_total()
            if audio_total > last_audio_total:
                last_audio_total = audio_total
                saw_output = True
                last_activity = time.monotonic()
            if saw_output and (time.monotonic() - last_activity) >= self._quiet_gap_s:
                break
            time.sleep(0.1)
        return "text", " ".join(transcript_parts)

    def stop(self, message, state, tool_results=None):  # noqa: ANN001
        if self.provider is not None:
            try:
                self._bg.run_coroutine(self.provider.disconnect(), timeout=15)
            except Exception as exc:  # noqa: BLE001
                logger.warning("whissle_voice disconnect: {}", exc)
        self._bg.stop()


def create_whissle_voice_agent(tools, domain_policy, **kwargs):
    return WhissleVoiceAgent(tools=tools, domain_policy=domain_policy)
