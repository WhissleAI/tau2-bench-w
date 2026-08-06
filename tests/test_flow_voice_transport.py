# Copyright Sierra
"""Offline tests for the flow-sim voice transport: sim reply latency (event-driven
end-of-turn + pipelined TTS), transcript-death robustness, and the infra_fail
classification. Everything runs against a scripted fake provider + fake TTS —
no network, no LiveKit, no live voice sessions."""
from __future__ import annotations

import threading
import time

import pytest

from tau2.flow import simulate as flow_simulate
from tau2.flow.analyze import DEFAULT_SEVERITY
from tau2.flow.usersim import ModelError
from tau2.flow.voice_transport import (
    VoiceInfraError,
    VoiceTransport,
    _split_sentences,
)


# ── fakes ───────────────────────────────────────────────────────────────────────

class FakeTTS:
    """Deterministic user-side TTS: fixed synth delay per call, 1s of PCM out."""

    def __init__(self, delay_s: float = 0.15):
        self.delay_s = delay_s
        self.calls = 0
        self.total_cost_usd = 0.0

    def synth(self, text: str) -> bytes:
        if not (text or "").strip():
            return b""
        self.calls += 1
        time.sleep(self.delay_s)
        return b"\x01\x00" * 16000  # 1s @ 16kHz PCM16 mono


class FakeProvider:
    """Scripted room provider: the test drives the bot's turns; implements exactly
    the surface VoiceTransport uses."""

    def __init__(self, *, emits_stop_events: bool = True, emits_text: bool = True,
                 bot_reply_delay_s: float = 0.2):
        self.emits_stop_events = emits_stop_events
        self.emits_text = emits_text
        self.bot_reply_delay_s = bot_reply_delay_s
        self.session_id = "fake-room"
        self.conversation_id = "fake-conv"
        self._texts: list[str] = []
        self._audio_total = 0
        self._stopped = 0
        self._speaking = False
        self._activity = threading.Event()
        self._lock = threading.Lock()
        self.playback_ready_sends = 0
        self._reply_text = "I can help with that."
        self._reply_pending = False

    # -- surface used by VoiceTransport ----------------------------------------
    def agent_audio_total(self) -> int:
        return self._audio_total

    def drain_agent_texts(self) -> list[str]:
        with self._lock:
            out, self._texts = self._texts, []
        return out

    def bot_stopped_total(self) -> int:
        return self._stopped

    def bot_speaking(self) -> bool:
        return self._speaking

    def wait_activity(self, timeout: float) -> bool:
        fired = self._activity.wait(timeout)
        self._activity.clear()
        return fired

    def events(self) -> list[dict]:
        return []

    async def send_audio(self, pcm: bytes) -> None:
        # Non-silence audio = the sim spoke → schedule the bot's reply once.
        if pcm and any(pcm) and not self._reply_pending:
            self._reply_pending = True
            self.speak_bot_turn_later(self.bot_reply_delay_s)

    async def send_playback_ready(self) -> None:
        self.playback_ready_sends += 1

    async def disconnect(self) -> None:
        pass

    def write_wav(self, prefix: str) -> dict:
        return {}

    # -- test scripting ----------------------------------------------------------
    def speak_bot_turn_later(self, delay_s: float, text: str | None = None) -> None:
        def _go():
            time.sleep(delay_s)
            self.speak_bot_turn(text)
        threading.Thread(target=_go, daemon=True).start()

    def speak_bot_turn(self, text: str | None = None) -> None:
        with self._lock:
            self._speaking = True
            self._audio_total += 32000
            if self.emits_text:
                self._texts.append(text or self._reply_text)
            self._speaking = False
            if self.emits_stop_events:
                self._stopped += 1
            self._reply_pending = False
        self._activity.set()


def make_vt(provider: FakeProvider, *, quiet_gap_s: float = 2.0,
            post_stop_gap_s: float = 0.35) -> VoiceTransport:
    vt = VoiceTransport(
        "fake-agent", api_key="wsk_test", tts=FakeTTS(),
        quiet_gap_s=quiet_gap_s, max_turn_s=10.0)
    vt._post_stop_gap_s = post_stop_gap_s
    vt._hesitant_p = 0.0  # deterministic regardless of ambient env
    vt.provider = provider
    vt._bg.start()
    return vt


# ── sentence splitting ──────────────────────────────────────────────────────────

def test_split_sentences_basic():
    out = _split_sentences(
        "I would like to book an appointment for next Tuesday please. "
        "Also, can you tell me whether parking is available at the clinic?")
    assert len(out) == 2


def test_split_sentences_merges_tiny_fragments():
    # "Okay." must not become its own TTS call — merged with a neighbor.
    out = _split_sentences("Okay. Thanks. That works for me, see you Tuesday then.")
    assert len(out) <= 2
    assert all(len(s) >= 6 for s in out)


def test_split_sentences_empty():
    assert _split_sentences("") == []
    assert _split_sentences("   ") == []


# ── event-driven end-of-turn ────────────────────────────────────────────────────

def test_bot_stop_event_beats_quiet_gap():
    """With bot-stopped-speaking events, the collector exits after the short
    post-stop gap — not the 2s quiet-gap."""
    p = FakeProvider(emits_stop_events=True)
    vt = make_vt(p, quiet_gap_s=2.0, post_stop_gap_s=0.3)
    p.speak_bot_turn_later(0.1)
    t0 = time.monotonic()
    text, boundary = vt._collect_until_quiet(
        deadline_s=8.0, quiet_gap_s=vt._quiet_gap_s, require_output=True)
    elapsed = time.monotonic() - t0
    assert text == "I can help with that."
    assert elapsed < 1.2, f"event-driven exit took {elapsed:.2f}s (quiet-gap path?)"
    assert vt._bot_final_t is not None
    vt._bg.stop()


def test_quiet_gap_fallback_without_stop_events():
    """No speaking-state events → the quiet-gap fallback still governs (never a
    premature barge-in on deploys that do not emit them)."""
    p = FakeProvider(emits_stop_events=False)
    vt = make_vt(p, quiet_gap_s=0.8, post_stop_gap_s=0.1)
    p.speak_bot_turn_later(0.1)
    t0 = time.monotonic()
    text, _ = vt._collect_until_quiet(
        deadline_s=8.0, quiet_gap_s=vt._quiet_gap_s, require_output=True)
    elapsed = time.monotonic() - t0
    assert text
    assert elapsed >= 0.8, "fallback must respect the quiet gap"
    vt._bg.stop()


# ── sim reply latency instrumentation ───────────────────────────────────────────

def test_turn_records_sim_reply_breakdown():
    p = FakeProvider()
    vt = make_vt(p)
    vt._bot_final_t = time.monotonic()  # as if the bot just finished its turn
    vt.note_llm_ms(300)
    res = vt.turn("Yes, that works for me — Tuesday morning would be perfect.")
    sim = res.raw["sim_reply"]
    assert sim is not None
    assert sim["llm_ms"] == 300
    assert sim["tts_ms"] >= 100                  # one FakeTTS call ≈ 150ms
    assert sim["total_ms"] < 1500
    assert sim["wait_ms"] >= 0
    assert vt.sim_reply == [sim]
    assert res.reply == "I can help with that."
    vt._bg.stop()


def test_pipelined_tts_first_audio_is_one_sentence():
    """Two long sentences: time-to-first-audio covers ONE synth call, while the
    full utterance still costs two."""
    p = FakeProvider()
    vt = make_vt(p)
    vt._bot_final_t = time.monotonic()
    res = vt.turn(
        "I would like to reschedule my appointment to Thursday afternoon instead. "
        "Could you also confirm whether the earlier consultation notes were saved?")
    sim = res.raw["sim_reply"]
    assert vt.tts.calls == 2
    assert sim["tts_ms"] < 280, f"first-audio TTS {sim['tts_ms']}ms — not pipelined"
    assert sim["tts_full_ms"] >= 280
    vt._bg.stop()


def test_sim_reply_latency_before_after():
    """The user's complaint, quantified offline: p50/p95 of bot-turn-final → sim
    reply audio start, OLD configuration (2s quiet-gap, no stop events — the
    pre-fix behavior) vs NEW (event-driven stop + pipelined TTS)."""
    def run_turns(emits_stop: bool, quiet_gap: float, n: int = 6) -> list[int]:
        p = FakeProvider(emits_stop_events=emits_stop)
        vt = make_vt(p, quiet_gap_s=quiet_gap, post_stop_gap_s=0.3)
        vt._bot_final_t = None
        totals: list[int] = []
        p.speak_bot_turn_later(0.05)  # greeting
        vt._collect_until_quiet(deadline_s=8.0, quiet_gap_s=quiet_gap,
                                require_output=True)
        for i in range(n):
            vt.note_llm_ms(250)
            time.sleep(0.25)  # the sim LLM "thinking" between transport calls
            res = vt.turn("Sure, that works. Please book it for Tuesday at nine.")
            if res.raw["sim_reply"]:
                totals.append(res.raw["sim_reply"]["total_ms"])
        vt._bg.stop()
        return totals

    old = run_turns(emits_stop=False, quiet_gap=2.0)
    new = run_turns(emits_stop=True, quiet_gap=2.0)
    p50_old = sorted(old)[len(old) // 2]
    p50_new = sorted(new)[len(new) // 2]
    print(f"\nsim-reply latency p50 old={p50_old}ms new={p50_new}ms "
          f"(p95 old={max(old)}ms new={max(new)}ms)")
    assert p50_old >= 2000, "old path should carry the full quiet-gap"
    assert p50_new < 1300, f"new path p50 {p50_new}ms — still delayed"
    assert p50_new < p50_old - 1200


# ── transcript-death robustness ─────────────────────────────────────────────────

def test_transcript_death_retries_handshake_and_flags():
    p = FakeProvider(emits_text=False)  # audio flows, transcript surface dead
    vt = make_vt(p, quiet_gap_s=0.3)
    vt._bot_final_t = time.monotonic()
    r1 = vt.turn("Hello?")
    assert r1.reply == ""
    assert p.playback_ready_sends == 1          # one handshake retry, once only
    assert r1.raw["transcript_dead"] is False   # not yet — one dead turn
    r2 = vt.turn("Are you there?")
    assert p.playback_ready_sends == 1
    assert r2.raw["transcript_dead"] is True    # streak of 2 → give up upstream
    vt._bg.stop()


def test_start_raises_infra_when_audio_but_no_transcript():
    p = FakeProvider(emits_text=False)
    vt = make_vt(p, quiet_gap_s=0.2)
    vt._greeting_wait_s = 1.0
    p.speak_bot_turn_later(0.05)
    p.speak_bot_turn_later(0.6)  # audio keeps flowing during the retry window too
    with pytest.raises(VoiceInfraError):
        vt.start()
    assert p.playback_ready_sends == 2  # original + one retry
    vt._bg.stop()


# ── infra classification helpers ────────────────────────────────────────────────

def test_infra_error_classification():
    import requests

    assert flow_simulate._is_infra_error(VoiceInfraError("dead channel"))
    assert flow_simulate._is_infra_error(ModelError("models/chat -> HTTP 402: ..."))
    assert flow_simulate._is_infra_error(requests.ConnectionError("boom"))
    assert flow_simulate._is_infra_error(TimeoutError())
    assert not flow_simulate._is_infra_error(ValueError("a real bug"))


def test_infra_fail_finding_registered():
    assert DEFAULT_SEVERITY.get("infra_fail") == "high"


def test_pctl():
    assert flow_simulate._pctl([], 0.5) is None
    assert flow_simulate._pctl([5], 0.95) == 5
    assert flow_simulate._pctl([1, 2, 3, 4, 100], 0.5) == 3
    assert flow_simulate._pctl([1, 2, 3, 4, 100], 0.95) == 100
