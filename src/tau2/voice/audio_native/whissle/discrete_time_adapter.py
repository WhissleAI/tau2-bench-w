"""Discrete-time adapter for the Whissle audio-native provider.

Bridges the tick-based DiscreteTimeAdapter interface to Whissle's real voice
pipeline running over LiveKit (WhissleRoomProvider). Each tick:

  1. flush any tool results the orchestrator produced last tick (→ data channel),
  2. publish this tick's user audio (telephony μ-law → PCM16) into the room,
  3. wait out the remainder of the tick while Whissle's STT→LLM→TTS runs,
  4. drain the bot's audio (PCM16 → telephony) into the tick result,
  5. surface any tool calls Whissle delegated + the agent transcript it emitted.

Whissle does its own VAD/endpointing/barge-in server-side, so vad_config is
ignored. Tool execution is delegated to the benchmark (see WhissleRoomProvider).
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Optional

from loguru import logger

from tau2.data_model.audio import AudioFormat
from tau2.data_model.message import ToolCall
from tau2.environment.tool import Tool
from tau2.voice.audio_native.adapter import DiscreteTimeAdapter
from tau2.voice.audio_native.async_loop import BackgroundAsyncLoop
from tau2.voice.audio_native.audio_converter import (
    pcm16_to_telephony,
    telephony_to_pcm16,
)
from tau2.voice.audio_native.tick_result import TickResult
from tau2.voice.audio_native.whissle.config import WhissleConfig
from tau2.voice.audio_native.whissle.provider import WhissleRoomProvider


def _tool_to_anthropic(t: Tool) -> dict:
    """tau2 Tool → Whissle bench schema ({name, description, input_schema})."""
    schema = t.openai_schema
    fn = schema.get("function", schema)
    return {
        "name": fn["name"],
        "description": fn.get("description", "") or "",
        "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
    }


class WhissleAdapter(DiscreteTimeAdapter):
    def __init__(
        self,
        tick_duration_ms: int,
        whissle_config: Optional[WhissleConfig] = None,
        send_audio_instant: bool = True,
        audio_format: Optional[AudioFormat] = None,
    ):
        super().__init__(
            tick_duration_ms=tick_duration_ms,
            audio_format=audio_format,
            send_audio_instant=send_audio_instant,
        )
        self.config = whissle_config or WhissleConfig()
        self.provider: Optional[WhissleRoomProvider] = None
        self._bg = BackgroundAsyncLoop()
        self._user_in_state = None  # telephony → PCM16 resample state
        self._agent_out_state = None  # PCM16 → telephony resample state

    # -- lifecycle ---------------------------------------------------------------

    def connect(self, system_prompt, tools, vad_config=None, modality="audio") -> None:
        self._bg.start()
        schemas = [_tool_to_anthropic(t) for t in tools]
        self.provider = WhissleRoomProvider(self.config)
        self._bg.run_coroutine(
            self.provider.connect(system_prompt, schemas),
            timeout=self.config.connect_timeout_s + 15,
        )
        logger.info("WhissleAdapter connected — room={}", self.provider.session_id)

    def disconnect(self) -> None:
        if self.provider is not None:
            try:
                self._bg.run_coroutine(self.provider.disconnect(), timeout=15)
            except Exception as exc:  # noqa: BLE001
                logger.warning("WhissleAdapter disconnect error: {}", exc)
            self.provider = None
        self._bg.stop()

    @property
    def is_connected(self) -> bool:
        return self.provider is not None and self.provider.is_connected

    # -- tick --------------------------------------------------------------------

    def run_tick(self, user_audio: bytes, tick_number: Optional[int] = None) -> TickResult:
        tick_number = tick_number if tick_number is not None else self._tick_count + 1
        # Budget: the tick itself + slack for a tool round-trip stall.
        timeout = self.tick_duration_ms / 1000 + self.config.tool_timeout_s + 15
        return self._bg.run_coroutine(self._tick(user_audio, tick_number), timeout=timeout)

    async def _tick(self, user_audio: bytes, tick_number: int) -> TickResult:
        result = await self._async_run_tick(user_audio, tick_number)
        # Overlay Whissle's own transcript (base template leaves it empty for us —
        # we don't use the proportional-transcript machinery; Whissle emits final
        # text per assistant turn via bench-agent-text).
        if self.provider is not None:
            texts = self.provider.drain_agent_texts()
            if texts:
                joined = " ".join(texts).strip()
                result.proportional_transcript = (
                    f"{result.proportional_transcript} {joined}".strip()
                    if result.proportional_transcript
                    else joined
                )
        return result

    async def _execute_tick(
        self, user_audio: bytes, tick_number: int, result: TickResult, tick_start: float
    ) -> None:
        assert self.provider is not None

        # 1. user telephony (8kHz μ-law) → PCM16 @ user_sample_rate, publish.
        pcm_user, self._user_in_state = telephony_to_pcm16(
            user_audio, self.config.user_sample_rate, self._user_in_state
        )
        try:
            await self.provider.send_audio(pcm_user)
        except Exception as exc:  # noqa: BLE001 — a send hiccup must not kill the tick
            logger.warning("whissle send_audio failed (tick {}): {}", tick_number, exc)

        # 2. let Whissle run for the remainder of the tick.
        remaining = (self.tick_duration_ms / 1000) - (
            asyncio.get_running_loop().time() - tick_start
        )
        if remaining > 0:
            await asyncio.sleep(remaining)

        # 3. drain bot audio (PCM16 @ agent_sample_rate) → telephony chunk.
        pcm_agent = await self.provider.drain_agent_audio()
        if pcm_agent:
            tel, self._agent_out_state = pcm16_to_telephony(
                pcm_agent, self.config.agent_sample_rate, self._agent_out_state
            )
            if tel:
                result.agent_audio_chunks.append((tel, f"whissle-{tick_number}"))

        # 4. surface delegated tool calls.
        for call in self.provider.drain_tool_calls():
            result.tool_calls.append(
                ToolCall(
                    id=call.get("id"),
                    name=call.get("name"),
                    arguments=call.get("arguments") or {},
                    requestor="assistant",
                )
            )

    async def _flush_pending_tool_results(self) -> None:
        if self.provider is None or not self._pending_tool_results:
            return
        pending = list(self._pending_tool_results)
        self._pending_tool_results.clear()
        for call_id, result, _request_response, _is_error in pending:
            try:
                await self.provider.send_tool_result(call_id, result)
            except Exception as exc:  # noqa: BLE001
                logger.warning("whissle send_tool_result failed ({}): {}", call_id, exc)
