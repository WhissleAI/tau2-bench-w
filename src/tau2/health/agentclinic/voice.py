# Copyright Sierra
"""VOICE mode — the same diagnostic interview, conducted over real speech.

AgentClinic is a dialogue benchmark, which makes it the one place where a speech
company can show something a text-only lab cannot: the identical clinical episode,
identically scored, but with the patient actually *speaking* and the doctor actually
*listening*. This module drives that, reusing :mod:`tau2.flow.voice_transport` (its
speech-energy end-of-turn detection, data-channel guards and honest per-turn latency
instrumentation) rather than re-implementing a voice loop.

Topology per case::

    PatientAgent (LLM, unchanged)         MeasurementAgent (LLM, unchanged)
            │ text                                 ▲ text
            ▼                                      │
      Whissle TTS  ──audio──►  LiveKit room  ◄──── bench tool-call/result
                                    │
                                    ▼
                         Whissle voice pipeline = THE DOCTOR
                         (its own STT → LLM → TTS)
                                    │ transcript
                                    ▼
                       the SAME scoring path as text mode

Deliberate, documented differences from text mode — a voice number is not a text
number and must never be presented as one:

1. **Protocol is `tools`.** Nobody says "REQUEST TEST colon Chest underscore X dash
   Ray" out loud. The three actions are delegated tools over the data channel
   (``bench-tool-call`` → we run the measurement agent → ``bench-tool-result``), which
   is how the product really works. Markers spoken in prose are still parsed.
2. **The session prompt is fixed at connect time.** ``/api/bench/voice/start`` takes
   the system prompt once, so the doctor's live question budget ("you have asked M so
   far") cannot be re-rendered per turn the way text mode does. It is rendered once
   at M=0.
3. **The patient opens the call with "Hello?"** so the doctor speaks first (upstream's
   ordering). A bench-mode voice agent has no text-injection path; a short caller
   greeting is the only way to hand it the opening turn.
4. **Transcription noise is real and is the point.** The scored transcript is the
   doctor's own RTVI transcript; ASR/TTS error in the patient's speech is part of what
   is being measured.
5. **No image channel.** Vision is text-mode only (the room carries audio + a data
   channel, not case imagery), so ``--mode voice`` requires ``--vision off``.

Sessions that fail for infrastructure reasons — dead data channel, provider outage,
credit exhaustion — raise :class:`tau2.flow.voice_transport.VoiceInfraError` and are
classified ``infra_fail`` by the runner and EXCLUDED, exactly as the flow suite does.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from tau2.flow.voice_transport import (
    VoiceInfraError,
    VoiceTransport,
    dedup_texts,
)
from tau2.health.agentclinic.doctor import DoctorConfig
from tau2.health.agentclinic.protocol import (
    DoctorAction,
    doctor_system_prompt,
    parse_doctor_output,
    tool_schemas,
)

# The caller's opening so the doctor takes the first substantive turn.
VOICE_OPENING = "Hello?"


class ClinicVoiceTransport(VoiceTransport):
    """:class:`VoiceTransport` in BENCH mode: our doctor prompt + delegated tools.

    The parent connects with ``real=True`` (the agent as deployed, own prompt) because
    the flow suite audits the deployed agent. Here we need the AgentClinic doctor
    contract in the pipeline and the three clinic actions delegated back to the
    harness, which is what bench mode is for — so only ``start`` changes.
    """

    def __init__(self, agent_id: str, system_prompt: str, tools: list[dict],
                 **kw: Any) -> None:
        super().__init__(agent_id, **kw)
        self._system_prompt = system_prompt
        self._bench_tools = tools

    def start(self) -> str:
        from tau2.voice.audio_native.whissle.provider import WhissleRoomProvider

        self._bg.start()
        if self.provider is None:  # tests inject a fake provider before start()
            self.provider = WhissleRoomProvider(self.config)
            self._bg.run_coroutine(
                self.provider.connect(system_prompt=self._system_prompt,
                                      tools=self._bench_tools, real=False),
                timeout=self.config.connect_timeout_s + 15)
        self.room = self.provider.session_id
        self.conversation_id = self.provider.conversation_id
        self._bg.run_coroutine(self.provider.send_playback_ready(), timeout=20)
        # Bench mode has no greeting: the agent answers the caller, it does not open.
        self.greeting = ""
        return ""

    # -- one doctor turn ---------------------------------------------------------

    def speak_and_collect(self, text: Optional[str]) -> dict[str, Any]:
        """Speak ``text`` as the patient (or say nothing when ``None``, e.g. after a
        tool result has been delivered) and wait for the doctor's next boundary:
        either a delegated tool call or a finished spoken turn.

        Returns ``{reply, tool_calls, latency_ms, boundary, end_reason,
        bot_speech_bytes}``. Raises :class:`VoiceInfraError` when the doctor's audio
        flows but its transcript surface is dead — an unmeasurable session, not a
        silent doctor."""
        if self.provider is None:
            raise VoiceInfraError("start() must be called before a turn")
        speech_before = self.provider.agent_speech_total()
        if text:
            self._turn_i += 1
            t_send_done, _pub, _f, _full = self._speak_turn(text, False)
        else:
            t_send_done = time.monotonic()

        reply, calls, boundary, first_audio_t = self._collect_until_quiet_or_tool()
        latency_ms = (round((first_audio_t - t_send_done) * 1000)
                      if first_audio_t is not None else None)
        if latency_ms is not None and latency_ms >= 0:
            self.latencies_ms.append(latency_ms)
        bot_speech = max(0, self.provider.agent_speech_total() - speech_before)
        if not reply.strip() and not calls and bot_speech > 0:
            # Audio without transcript = the dead-data-channel signature the transport
            # documents. One handshake retry, then this session cannot be measured.
            if not self._handshake_retried:
                self._handshake_retried = True
                try:
                    self._bg.run_coroutine(self.provider.send_playback_ready(),
                                           timeout=20)
                except Exception:  # noqa: BLE001 — best-effort recovery
                    pass
            else:
                raise VoiceInfraError(
                    "doctor audio flowed but no transcript arrived (data channel "
                    "dead) — after one ready-handshake retry")
        signals, metadata, _flow, tool_events = self._drain_channel()
        return {"reply": reply, "tool_calls": calls, "latency_ms": latency_ms,
                "boundary": boundary, "end_reason": self._last_end_reason,
                "bot_speech_bytes": bot_speech, "signals": signals,
                "user_metadata": metadata, "tool_events": tool_events}

    def _collect_until_quiet_or_tool(self):
        """The parent's end-of-turn detection (speech-energy silence PRIMARY,
        ``bot-stopped-speaking`` as confirmation, no-activity quiet-gap as fallback)
        with one addition: a delegated ``bench-tool-call`` also ends the turn, because
        the doctor is then waiting on us for a test result."""
        assert self.provider is not None
        t_start = time.monotonic()
        deadline = t_start + self._max_turn_s
        frags: list[str] = []
        last_speech = self.provider.agent_speech_total()
        stop_snapshot = self.provider.bot_stopped_total()
        saw_output = False
        saw_speech = False
        last_activity = t_start
        first_audio_t: Optional[float] = None
        boundary = "silent"
        end_reason: Optional[str] = None
        calls: list[dict] = []

        while time.monotonic() < deadline:
            calls = self.provider.drain_tool_calls()
            if calls:
                end_reason = "tool_call"
                boundary = "tool"
                break
            texts = self.provider.drain_agent_texts()
            if texts:
                frags.extend(texts)
                saw_output = True
                boundary = "text"
                last_activity = time.monotonic()
            speech_total = self.provider.agent_speech_total()
            if speech_total > last_speech:
                if first_audio_t is None:
                    first_audio_t = (self.provider.agent_speech_last_t()
                                     or time.monotonic())
                last_speech = speech_total
                saw_output = True
                saw_speech = True
                if boundary == "silent":
                    boundary = "text"
                last_activity = time.monotonic()
            if saw_output:
                now = time.monotonic()
                last_sp_t = self.provider.agent_speech_last_t()
                stop_seen = (self.provider.bot_stopped_total() > stop_snapshot
                             and not self.provider.bot_speaking())
                if stop_seen and (now - last_activity) >= min(
                        self._post_stop_gap_s, self._quiet_gap_s):
                    end_reason = "stop_event"
                    break
                if saw_speech and last_sp_t is not None:
                    need = self._silence_gap_s
                    if self.provider.bot_speaking():
                        need = max(need, self._quiet_gap_s)
                    if (now - last_sp_t) >= need:
                        end_reason = "audio_silence"
                        break
                if (now - last_activity) >= self._quiet_gap_s:
                    end_reason = "quiet_gap"
                    break
            self.provider.wait_activity(0.05)
        else:
            end_reason = "deadline"
            if not saw_output:
                boundary = "timeout"
        if saw_output:
            audio_end = self.provider.agent_speech_last_t() if saw_speech else None
            self._bot_final_t = audio_end if audio_end is not None else last_activity
        self._last_end_reason = end_reason
        return dedup_texts(frags), calls, boundary, first_audio_t

    def send_tool_result(self, call_id: str, result: str) -> None:
        assert self.provider is not None
        self._bg.run_coroutine(self.provider.send_tool_result(call_id, result),
                               timeout=30)


# ── the voice doctor (a DoctorTransport the shared episode loop can drive) ──────

class VoiceDoctor:
    """Same interface as :class:`~tau2.health.agentclinic.doctor.WhissleDoctor`, so
    ``runner.run_case`` drives voice and text with one loop and one scorer."""

    def __init__(self, cfg: DoctorConfig, presentation: Any,
                 transport: Optional[ClinicVoiceTransport] = None) -> None:
        cfg.require()
        self.cfg = cfg
        self.presentation = presentation
        self.infs = 0
        self.turns: list[dict] = []
        self.system_prompt = doctor_system_prompt(
            presentation, max_infs=cfg.max_infs, infs=0,
            bias_prompt=cfg.bias_prompt, img_request=False, protocol="tools")
        self.transport = transport or ClinicVoiceTransport(
            cfg.agent_id, self.system_prompt,
            tool_schemas(img_request=False),
            base=cfg.base, api_key=cfg.api_key)
        self._started = False
        self._pending_call_ids: list[str] = []

    def start(self) -> None:
        if not self._started:
            self.transport.start()
            self._started = True

    def act(self, incoming: Optional[str], *,
            attach_image: bool = False) -> DoctorAction:
        self.start()
        if self.infs >= self.cfg.max_infs:
            return DoctorAction("question", "Maximum inferences reached")
        spoken = VOICE_OPENING if incoming == "" else incoming
        res = self.transport.speak_and_collect(spoken)
        calls = [{"id": c.get("id"), "name": c.get("name"),
                  "arguments": c.get("arguments") or {}}
                 for c in (res.get("tool_calls") or [])]
        self._pending_call_ids = [c["id"] for c in calls if c.get("id")]
        action = parse_doctor_output(res.get("reply") or "", calls)
        self.infs += 1
        self.turns.append({
            "inference": self.infs, "spoken": spoken, "reply": res.get("reply"),
            "kind": action.kind, "payload": action.payload,
            "latency_ms": res.get("latency_ms"), "boundary": res.get("boundary"),
            "end_reason": res.get("end_reason"),
            "bot_speech_bytes": res.get("bot_speech_bytes"),
            "signals": res.get("signals"), "user_metadata": res.get("user_metadata"),
        })
        return action

    def deliver_tool_result(self, action: DoctorAction, result: str) -> None:
        for call_id in self._pending_call_ids:
            self.transport.send_tool_result(call_id, result)
        self._pending_call_ids = []

    def finish(self, prefix: str, *, transcribe: bool = False) -> dict[str, Any]:
        try:
            return self.transport.finish(prefix, transcribe=transcribe)
        except Exception:  # noqa: BLE001 — evidence is best-effort
            return {}

    def stop(self) -> None:
        try:
            self.transport.stop()
        except Exception:  # noqa: BLE001
            pass
