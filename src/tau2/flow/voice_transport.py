# Copyright Sierra
"""VOICE transport for the flow-sim harness — drive a Whissle agent over an ACTUAL
audio path (STT → flow-brain → TTS over LiveKit), not the deterministic text channel.

The flow-sim suite (``flow/simulate.py``) normally drives an agent's in-call state
machine over ``POST /api/agents/{id}/chat/turn`` — text in, text out, zero audio
nondeterminism. That validates the FlowRuntime logic but exercises **none** of the
voice pipeline: STT, endpointing/turn-taking, TTS, or barge-in. This module swaps
ONLY the transport so the SAME user-simulator + judges run against Whissle's real
spoken pipeline.

How it works (mirrors ``agent/whissle_voice_agent.py``'s turn loop, but as a plain
turn-driver the flow runner can call in place of ``FlowClient.turn``):

    POST {WHISSLE_BASE}/api/bench/voice/start  {"agent_id", "real": true}
        → LiveKit {url, token, room}; the backend spawns the agent's REAL voice
          pipeline (its own prompt + flow + tools + greeting) as a bot in that room.
    WhissleRoomProvider joins the room as the USER participant and:
      • publishes each simulated-user turn as audio  → the agent's STT hears it,
      • subscribes to the agent's TTS audio           → captured as a duplex WAV,
      • reads the agent's own spoken transcript off the LiveKit data channel
        (standard RTVI ``bot-transcription`` / ``bot-output``) — no re-ASR needed.

``real=true`` matters: it runs the agent as DEPLOYED, so ``flow_active(agent)`` builds
the FlowController into the pipeline and the state machine actually runs (verified
against the backend: the voice pipeline instantiates the identical ``FlowRuntime`` the
text runner does — services/flow/controller.py).

Per-turn latency (user-stopped-speaking → first agent audio) is measured locally and
returned, a real spoken-turn latency number the text channel cannot produce.

Honest scope — the flow STEP-TRACE gap
--------------------------------------
The voice pipeline runs the flow but does **not persist** its step-trace: it never
wires a ``persist_fn`` and creates no ``conversations`` row, and ``GET /flow/trace``
reads only ``conversations.flow_state`` (text-runner-only). So a voice session yields
a faithful TRANSCRIPT (→ task-success / communicate-info judges + audio evidence) but
no retrievable ``steps`` for the deterministic state-trace analyzer. See
WHISSLE_VOICE_TESTING.md for the exact one-line backend change that closes it. The
runner degrades honestly (a typed ``voice_trace_unavailable`` finding), and the moment
the backend persists the voice trace this transport needs no change — the existing
analyzer runs on it unmodified.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import requests
from dotenv import load_dotenv

from tau2.flow.client import TurnResult
from tau2.voice.audio_native.async_loop import BackgroundAsyncLoop
from tau2.voice.audio_native.whissle.config import WhissleConfig
from tau2.voice.audio_native.whissle.provider import WhissleRoomProvider

load_dotenv()

DEFAULT_BASE = "https://aws-gateway-backend.whissle.ai/bot"
_MARKER_RE = re.compile(r"\[\[.*?\]\]")


class VoiceTransportError(RuntimeError):
    pass


class VoiceInfraError(VoiceTransportError):
    """Transport/infrastructure failure — the session could not produce a valid
    measurement (dead data channel while audio flowed, provider outage, credit
    exhaustion). The runner classifies these as ``infra_fail`` (retried once, then
    bucketed OUT of the flow metrics), never as a flow finding."""


# ── user-side TTS (Whissle's own à-la-carte model API) ──────────────────────────

class WhissleTTS:
    """Synthesize the simulated user's utterances with Whissle's OWN TTS endpoint
    (``POST /api/models/tts``), so the harness stays self-contained on a single
    ``wsk_`` key — no ElevenLabs/OpenAI voice key required.

    We request ``output_format=pcm_16000`` → raw PCM16LE mono @ 16 kHz (``audio/L16``),
    exactly what ``WhissleRoomProvider.send_audio`` publishes; no decode step."""

    def __init__(self, base: Optional[str] = None, api_key: Optional[str] = None,
                 voice: Optional[str] = None, timeout: float = 60.0) -> None:
        self.base = (base or os.getenv("WHISSLE_BASE") or DEFAULT_BASE).rstrip("/")
        self.api_key = api_key or os.getenv("WHISSLE_API_KEY") or ""
        if not self.api_key:
            raise VoiceTransportError("WHISSLE_API_KEY not set — put a wsk_ key in .env.")
        self.voice = voice or os.getenv("WHISSLE_USER_VOICE_ID") or None
        self.timeout = timeout
        self._s = requests.Session()
        self._s.headers.update({"Authorization": f"Bearer {self.api_key}"})
        self.total_cost_usd = 0.0
        self.calls = 0

    def synth(self, text: str) -> bytes:
        """User text → PCM16LE @ 16 kHz. Empty/whitespace returns b"" (skip)."""
        text = _MARKER_RE.sub("", text or "").strip()
        if not text:
            return b""
        body: dict[str, Any] = {"text": text, "output_format": "pcm_16000"}
        if self.voice:
            body["voice"] = self.voice
        r = self._s.post(f"{self.base}/api/models/tts", json=body, timeout=self.timeout)
        if r.status_code >= 300:
            raise VoiceTransportError(
                f"models/tts -> HTTP {r.status_code}: {r.text[:200]}")
        self.calls += 1
        try:
            self.total_cost_usd += float(r.headers.get("X-Cost-USD") or 0.0)
        except (TypeError, ValueError):
            pass
        return r.content


# ── transcript dedup ────────────────────────────────────────────────────────────

def _norm_key(s: str) -> str:
    return re.sub(r"[\s\W]+", "", s).lower()


def dedup_texts(fragments: list[str]) -> str:
    """Collapse the interim+final duplication the RTVI transcript surface emits.

    The bot's transcript arrives over the data channel as overlapping
    ``bot-transcription`` + sentence-aggregated ``bot-output`` messages, so the same
    line shows up two or three times (verified live). Keep first occurrence of each
    normalized fragment, and drop a fragment already contained in what we've kept
    (an interim prefix of a later final)."""
    kept: list[str] = []
    seen: set[str] = set()
    joined_key = ""
    for frag in fragments:
        frag = (frag or "").strip()
        if not frag:
            continue
        k = _norm_key(frag)
        if not k or k in seen:
            continue
        if k in joined_key:  # this fragment is a substring of already-kept text
            continue
        seen.add(k)
        kept.append(frag)
        joined_key += k
    return " ".join(kept).strip()


# ── hesitant-speech synthesis (exercise the hesitation predictor) ────────────────

_HESITANT_PREFIXES = ["Um, ", "Uh, ", "Well, ", "Hmm, ", "So, like, ", "Let me think, "]
_HESITANT_INFIXES = [" um ", " uh ", " you know ", " like ", " I mean ", " sort of "]


def _hesitant_prob(raw: str) -> float:
    """Env → probability a turn is spoken haltingly. '1'/'true'/'yes' → 1.0, a bare
    number → that fraction (clamped 0..1), anything else / '0' → 0.0."""
    s = (raw or "").strip().lower()
    if s in ("1", "true", "yes", "on"):
        return 1.0
    try:
        return max(0.0, min(1.0, float(s)))
    except ValueError:
        return 0.0


def _hesitate_text(text: str, seed: int) -> str:
    """Insert disfluencies so the TTS renders halting speech (a leading filler + one
    mid-utterance filler + an ellipsis pause). Deterministic in ``seed`` (the turn
    index) so a run is reproducible without a global RNG."""
    text = (text or "").strip()
    if not text:
        return text
    pre = _HESITANT_PREFIXES[seed % len(_HESITANT_PREFIXES)]
    words = text.split(" ")
    if len(words) > 4:
        j = 2 + (seed % max(1, len(words) - 3))
        inf = _HESITANT_INFIXES[seed % len(_HESITANT_INFIXES)]
        words[j] = words[j] + inf + "..."
    return pre + " ".join(words)


def _inject_pauses(pcm: bytes, sample_rate: int, gap_ms: int = 450, n: int = 2) -> bytes:
    """Splice ``n`` silence gaps into a PCM16LE mono utterance at roughly even points,
    so the acoustic emotion timeline the whissle-large head reads actually wobbles
    (clean TTS is flat → the hesitation predictor never fires). No-op on tiny clips."""
    if not pcm or len(pcm) < sample_rate:  # <0.25s @16k*2B — too short to split
        return pcm
    gap = b"\x00\x00" * int(sample_rate * gap_ms / 1000)
    frames = len(pcm) // 2
    step = frames // (n + 1)
    out = bytearray()
    for k in range(n + 1):
        a = k * step * 2
        b = len(pcm) if k == n else (k + 1) * step * 2
        out += pcm[a:b]
        if k < n:
            out += gap
    return bytes(out)


# ── sentence split (TTS pipelining) ─────────────────────────────────────────────

_SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")
_MIN_SENT_CHARS = 25


def _split_sentences(text: str) -> list[str]:
    """Split an utterance into sentences for pipelined TTS: the first sentence is
    synthesized and PUBLISHED while the rest synthesize in parallel, so the sim's
    time-to-first-audio is one short TTS call, not the whole utterance's. Tiny
    fragments are merged forward so we never fire a TTS call for "Okay."-sized
    slivers (call overhead would exceed the win)."""
    text = (text or "").strip()
    if not text:
        return []
    parts = [p for p in _SENT_SPLIT_RE.split(text) if p.strip()]
    merged: list[str] = []
    for p in parts:
        if merged and (len(merged[-1]) < _MIN_SENT_CHARS or len(p) < _MIN_SENT_CHARS):
            merged[-1] = f"{merged[-1]} {p}"
        else:
            merged.append(p)
    return merged or [text]


# ── one voice turn ──────────────────────────────────────────────────────────────

@dataclass
class VoiceTurnResult:
    reply: str
    latency_ms: Optional[int]        # user-stopped → first agent audio, this turn
    bot_audio_bytes: int             # agent PCM received during this turn
    boundary: str                    # "text" | "timeout" | "silent"
    raw_fragments: list[str] = field(default_factory=list)


# ── the transport ───────────────────────────────────────────────────────────────

class VoiceTransport:
    """Drives one flow-sim session over the real voice pipeline. Lifecycle:

        vt = VoiceTransport(agent_id)
        greeting = vt.start()           # joins room, captures the agent's greeting
        r = vt.turn("Hi, I'd like...")  # one spoken user turn → agent reply text
        ...
        evidence = vt.finish("/path/prefix")  # write duplex WAVs (+ optional re-ASR)
        vt.stop()

    ``turn`` returns a :class:`TurnResult` (the same shape ``FlowClient.turn`` returns)
    so ``flow/simulate.py`` can call it in place of the text client with no other
    change; the voice-only extras (latency, audio bytes) ride along on ``.raw``."""

    def __init__(self, agent_id: str, *, base: Optional[str] = None,
                 api_key: Optional[str] = None, tts: Optional[WhissleTTS] = None,
                 quiet_gap_s: Optional[float] = None,
                 max_turn_s: Optional[float] = None,
                 greeting_wait_s: float = 18.0) -> None:
        self.config = WhissleConfig(
            base_url=(base or os.getenv("WHISSLE_BASE") or DEFAULT_BASE),
            agent_id=agent_id,
            api_key=(api_key or os.getenv("WHISSLE_API_KEY") or ""),
        )
        self.config.require()
        self.tts = tts or WhissleTTS(base=self.config.base_url,
                                     api_key=self.config.api_key)
        self._quiet_gap_s = float(
            quiet_gap_s if quiet_gap_s is not None
            else os.getenv("WHISSLE_VOICE_QUIET_GAP_S", "2.0"))
        # Residual gap AFTER the bot's own bot-stopped-speaking event: the bot has
        # declared its turn over, so we only wait this long for a trailing transcript
        # fragment, not the full quiet-gap. This is the event-driven fast path that
        # takes the sim's reply latency from ~2s of dead air to human range; the
        # quiet-gap remains the fallback when no speaking-state events arrive.
        self._post_stop_gap_s = float(os.getenv("WHISSLE_VOICE_POST_STOP_GAP_S", "0.35"))
        self._max_turn_s = float(
            max_turn_s if max_turn_s is not None
            else os.getenv("WHISSLE_VOICE_MAX_TURN_S", "45"))
        self._greeting_wait_s = greeting_wait_s
        self._bg = BackgroundAsyncLoop()
        self.provider: Optional[WhissleRoomProvider] = None
        self.room: Optional[str] = None
        self.conversation_id: Optional[str] = None  # PR #613: persisted voice trace key
        self.greeting: str = ""
        self.latencies_ms: list[int] = []
        # Cursor into provider.events() so each turn drains only the NEW signal frames.
        # SIGNAL_EMIT=1 pushes {kind:"signal"} server-messages (shadow / speculative /
        # hesitation predictions from the whissle-large metadata head) on the same data
        # channel; the provider records every frame, so we filter+attach the per-turn
        # ones to each TurnResult.raw — the meta-signal layer, captured over real voice.
        self._sig_cursor = 0
        self.signals: list[dict] = []   # every {kind:"signal"} frame this session
        self.metadata: list[dict] = []  # every {t:"user-metadata"} frame this session
        # WHISSLE_VOICE_HESITANT=1 makes the simulated user speak haltingly (filler
        # words + mid-utterance silence gaps) so the whissle-large emotion timeline
        # wobbles and the hesitation predictor actually fires — clean TTS never does.
        # A number 0<p<=1 makes only that fraction of turns hesitant (varies by turn).
        self._hesitant_p = _hesitant_prob(os.getenv("WHISSLE_VOICE_HESITANT", "0"))
        self._turn_i = 0
        # Sim-reply latency instrumentation (the user-facing turn-taking metric):
        # monotonic time of the bot's LAST activity in its finished turn (set by
        # _collect_until_quiet), the sim's LLM time for the upcoming reply (noted by
        # the driver via note_llm_ms), and the per-turn breakdown records.
        self._bot_final_t: Optional[float] = None
        self._pending_llm_ms: Optional[int] = None
        self.sim_reply: list[dict] = []
        # Transcript-death robustness: one handshake retry per session, and a streak
        # counter of consecutive turns where bot AUDIO flowed but no transcript
        # event arrived (the dead-data-channel signature).
        self._handshake_retried = False
        self._dead_streak = 0

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> str:
        """Open the voice session, join the room, and capture the agent's greeting
        (real-mode agents speak first, like a real answered call). Returns the
        greeting transcript (may be "" if the agent doesn't greet)."""
        self._bg.start()
        if self.provider is None:  # tests may inject a fake provider before start()
            self.provider = WhissleRoomProvider(self.config)
            self._bg.run_coroutine(
                self.provider.connect(system_prompt="", tools=[], real=True),
                timeout=self.config.connect_timeout_s + 15)
        self.room = self.provider.session_id
        self.conversation_id = self.provider.conversation_id
        # Handshake that tells the bot the caller is subscribed → triggers greeting.
        # (send_playback_ready now waits for the room mesh to settle first, so the
        # packet can't outrun our participant registration on the bot — the
        # participant=None crash that silently ate this handshake.)
        self._bg.run_coroutine(self.provider.send_playback_ready(), timeout=20)
        self.greeting = self._collect_until_quiet(
            deadline_s=self._greeting_wait_s, quiet_gap_s=self._quiet_gap_s,
            require_output=True)[0]
        # Robustness: bot AUDIO flowed but not one transcript event → the data
        # channel is dead on one side (crashed handler / lost subscription). Retry
        # the ready handshake ONCE; if the transcript surface stays dark while audio
        # flows, this session cannot be measured — raise a typed infra error so the
        # runner classifies it infra_fail (and retries the whole session) instead of
        # polluting the flow metrics as a stuck_termination.
        if not self.greeting and self.provider.agent_audio_total() > 0:
            self._handshake_retried = True
            self._bg.run_coroutine(self.provider.send_playback_ready(), timeout=20)
            self.greeting = self._collect_until_quiet(
                deadline_s=min(8.0, self._greeting_wait_s),
                quiet_gap_s=self._quiet_gap_s, require_output=True)[0]
            if not self.greeting:
                raise VoiceInfraError(
                    "bot audio is flowing but no transcript events arrived "
                    "(data channel dead) — after one ready-handshake retry")
        return self.greeting

    def turn(self, user_msg: str, conversation_id: Optional[str] = None) -> TurnResult:
        """Speak one user turn and return the agent's spoken reply, shaped as a
        :class:`TurnResult` (voice has no retrievable flow-trace, so ``flow`` is None →
        ``steps``/``current_state`` degrade exactly as the text client does before the
        trace PR). Voice extras land on ``.raw`` (latency_ms, bot_audio_bytes)."""
        if self.provider is None:
            raise VoiceTransportError("start() must be called before turn().")
        self._turn_i += 1
        # Halting delivery on the chosen fraction of turns (deterministic spread, no
        # global RNG) so the hesitation predictor has a wobbling timeline to fire on.
        hesitant = self._hesitant_p > 0 and (
            self._hesitant_p >= 1.0
            or ((self._turn_i * 997) % 1000) / 1000.0 < self._hesitant_p
        )
        spoken = _hesitate_text(user_msg, self._turn_i) if hesitant else user_msg
        audio_before = self.provider.agent_audio_total()
        bot_final_prev = self._bot_final_t  # last activity of the bot's finished turn
        t_send_done, t_pub_start, tts_first_ms, tts_full_ms = self._speak_turn(
            spoken, hesitant)
        # Sim-reply latency (the user-complaint metric): bot-turn-final → the sim
        # STARTS publishing its reply audio, broken into wait / LLM / TTS. The LLM
        # component is noted by the driver (note_llm_ms) between transport calls.
        sim_reply: Optional[dict] = None
        if bot_final_prev is not None:
            total_ms = round((t_pub_start - bot_final_prev) * 1000)
            llm_ms = self._pending_llm_ms
            wait_ms = max(0, total_ms - (llm_ms or 0) - tts_first_ms)
            sim_reply = {"total_ms": total_ms, "wait_ms": wait_ms,
                         "llm_ms": llm_ms, "tts_ms": tts_first_ms,
                         "tts_full_ms": tts_full_ms}
            self.sim_reply.append(sim_reply)
        self._pending_llm_ms = None
        reply, boundary, first_audio_t, audio_after = self._await_reply(t_send_done)
        latency_ms = (round((first_audio_t - t_send_done) * 1000)
                      if first_audio_t is not None else None)
        if latency_ms is not None:
            self.latencies_ms.append(latency_ms)
        vres = VoiceTurnResult(
            reply=reply, latency_ms=latency_ms,
            bot_audio_bytes=max(0, audio_after - audio_before), boundary=boundary)
        # Transcript-death detection: audio flowed this turn but not one transcript
        # event. Retry the ready handshake once per session (it re-exercises the
        # data channel in both directions); a persisting streak is surfaced to the
        # runner as transcript_dead so it can stop driving a dead session and
        # classify it infra_fail instead of chalking up empty agent turns.
        if not (reply or "").strip() and vres.bot_audio_bytes > 0:
            self._dead_streak += 1
            if not self._handshake_retried:
                self._handshake_retried = True
                try:
                    self._bg.run_coroutine(self.provider.send_playback_ready(),
                                           timeout=20)
                except Exception:  # noqa: BLE001 — best-effort recovery
                    pass
        else:
            self._dead_streak = 0
        # Per-turn meta-signals (hesitation / shadow / speculative) + raw whissle-large
        # metadata (emotion / intent / age / gender per interim+final) emitted on the
        # data channel during THIS turn — the whole point of running over real voice.
        turn_signals, turn_metadata = self._drain_channel()
        return TurnResult(
            reply=reply,
            # PR #613: the persisted-trace key is the conversations id returned by
            # voice/start (not the LiveKit room). Thread it so simulate.py's end-of-
            # session get_trace(agent_id, conv_id) retrieves the real voice step-trace.
            conversation_id=self.conversation_id or conversation_id or self.room or "",
            tools_used=[],           # real-mode voice runs tools internally (no delegation)
            tool_events=[],
            flow=None,               # per-turn trace not surfaced; full trace via GET /flow/trace
            raw={"ended": False, "voice": True, "room": self.room,
                 "conversation_id": self.conversation_id,
                 "latency_ms": latency_ms, "bot_audio_bytes": vres.bot_audio_bytes,
                 "boundary": boundary, "raw_fragments": vres.raw_fragments,
                 "signals": turn_signals, "user_metadata": turn_metadata,
                 "hesitant_input": hesitant,
                 "sim_reply": sim_reply,
                 "transcript_dead": self._dead_streak >= 2},
        )

    def finish(self, prefix: str, *, transcribe: bool = False) -> dict[str, Any]:
        """Write the full duplex capture (``<prefix>.caller.wav`` / ``.bot.wav`` /
        ``.mix.wav``) as real voice evidence; optionally re-transcribe the captured
        agent audio through ``/api/models/transcribe`` as an independent (non-RTVI)
        check of what was actually spoken."""
        if self.provider is None:
            return {}
        out: dict[str, Any] = self.provider.write_wav(prefix)
        out["latencies_ms"] = list(self.latencies_ms)
        out["sim_reply_ms"] = list(self.sim_reply)
        if transcribe and isinstance(out.get("bot"), dict) and out["bot"].get("path"):
            out["bot_reasr"] = self._transcribe_wav(out["bot"]["path"])
        return out

    def stop(self) -> None:
        if self.provider is not None:
            try:
                self._bg.run_coroutine(self.provider.disconnect(), timeout=15)
            except Exception:  # noqa: BLE001
                pass
            self.provider = None
        self._bg.stop()

    def events(self) -> list[dict]:
        """The full timestamped data-channel event stream (QA telemetry)."""
        return self.provider.events() if self.provider else []

    def _drain_channel(self) -> tuple[list[dict], list[dict]]:
        """Drain the data-channel frames that arrived since the last call, advancing
        the single cursor, and partition them into ``(signals, metadata)``:

          * signals  — ``{kind:"signal"}`` prediction frames (shadow / speculative /
            hesitation), each naming its producer + fields (predicted tools, eager
            draft, emotion-timeline entropy/instability for hesitation).
          * metadata — ``{t:"user-metadata"}`` frames the backend pushes on every
            interim/final (MetadataPushProcessor): the whissle-large acoustic head's
            live ``{emotion, intent, age, gender, probs}``. This is the per-interim +
            per-turn metadata the bench needs, produced in parallel with transcription.

        One cursor for both so a frame is never counted twice / dropped. Fail-open:
        no provider / no frames → ([], [])."""
        if self.provider is None:
            return [], []
        evs = self.provider.events()
        new = evs[self._sig_cursor:]
        self._sig_cursor = len(evs)
        sigs, meta = [], []
        for e in new:
            if e.get("type") != "server-message":
                continue
            d = e.get("data")
            if not isinstance(d, dict):
                continue
            if d.get("kind") == "signal":
                sigs.append(d)
            elif d.get("t") == "user-metadata":
                meta.append(d)
        self.signals.extend(sigs)
        self.metadata.extend(meta)
        return sigs, meta

    def note_llm_ms(self, ms: Optional[int]) -> None:
        """Driver hook: record how long the sim's LLM took to produce the NEXT
        user utterance, so the next turn's sim-reply breakdown attributes it."""
        self._pending_llm_ms = ms

    # -- internals ---------------------------------------------------------------

    def _speak_turn(self, spoken: str, hesitant: bool
                    ) -> tuple[float, float, int, int]:
        """Synthesize + publish one user turn. Returns
        ``(t_send_done, t_pub_start, tts_first_ms, tts_full_ms)`` (monotonic).

        Default path is PIPELINED: the first sentence is synthesized and starts
        publishing immediately; each later sentence synthesizes WHILE the previous
        one's audio drains into the room (send_audio paces near real-time), so the
        sim starts speaking after one short TTS call instead of after the whole
        utterance's synthesis. Hesitant mode keeps the whole-utterance path (its
        silence gaps are spliced across the complete clip) — intentionally off the
        default path."""
        t_tts0 = time.monotonic()
        if hesitant or not spoken.strip():
            pcm = self.tts.synth(spoken)
            if hesitant:
                pcm = _inject_pauses(pcm, 16000)  # user TTS is pcm_16000
            tts_first_ms = tts_full_ms = round((time.monotonic() - t_tts0) * 1000)
            t_pub_start = time.monotonic()
            return self._send_user_audio(pcm), t_pub_start, tts_first_ms, tts_full_ms
        sents = _split_sentences(spoken)
        first_pcm = self.tts.synth(sents[0])
        tts_first_ms = round((time.monotonic() - t_tts0) * 1000)
        t_pub_start = time.monotonic()
        fut = self._bg.submit(self.provider.send_audio(first_pcm))
        for s in sents[1:]:
            nxt = self.tts.synth(s)   # overlaps the previous chunk's publish drain
            fut.result(timeout=90)
            fut = self._bg.submit(self.provider.send_audio(nxt))
        fut.result(timeout=90)
        tts_full_ms = round((time.monotonic() - t_tts0) * 1000)
        # Trailing 0.5s silence so the agent's endpointer fires end-of-turn.
        self._bg.run_coroutine(
            self.provider.send_audio(b"\x00\x00" * int(16000 * 0.5)), timeout=30)
        return time.monotonic(), t_pub_start, tts_first_ms, tts_full_ms

    def _send_user_audio(self, pcm: bytes) -> float:
        """Publish the whole utterance + a trailing 0.5 s of silence so the agent's
        endpointer fires end-of-turn. Returns the monotonic time send completed."""
        if pcm:
            self._bg.run_coroutine(self.provider.send_audio(pcm), timeout=90)
        # 0.5 s silence @ 16 kHz PCM16 mono, regardless of whether pcm was empty.
        self._bg.run_coroutine(
            self.provider.send_audio(b"\x00\x00" * int(16000 * 0.5)), timeout=30)
        return time.monotonic()

    def _await_reply(self, t_send_done: float) -> tuple[str, str, Optional[float], int]:
        """Block until the agent finishes its turn: collect transcript fragments, and
        detect first-audio (for latency) and turn-quiet (end of speaking)."""
        text, boundary, first_audio_t, audio_total = self._collect_until_quiet(
            deadline_s=self._max_turn_s, quiet_gap_s=self._quiet_gap_s,
            require_output=True, t_ref=t_send_done, want_first_audio=True)
        return text, boundary, first_audio_t, audio_total

    def _collect_until_quiet(self, *, deadline_s: float, quiet_gap_s: float,
                             require_output: bool, t_ref: Optional[float] = None,
                             want_first_audio: bool = False):
        """Wait (event-driven) until the agent has produced output and then stayed
        quiet. Two end-of-turn signals, fastest wins:

          * the bot's own RTVI ``bot-stopped-speaking`` event → only a short
            ``post_stop_gap`` for trailing transcript fragments (the human-speed
            path: the bot said it is done, so reply like a person would);
          * no new transcript AND no new audio for ``quiet_gap_s`` (fallback when
            the deploy emits no speaking-state events).

        Blocks on the provider's activity event rather than a fixed poll sleep.
        Records the bot's last-activity time in ``self._bot_final_t`` (the anchor
        for the next turn's sim-reply latency). Returns
        (deduped_text, boundary, first_audio_t, audio_total) when
        ``want_first_audio`` else (deduped_text, boundary)."""
        assert self.provider is not None
        deadline = time.monotonic() + deadline_s
        frags: list[str] = []
        last_audio = self.provider.agent_audio_total()
        stop_snapshot = self.provider.bot_stopped_total()
        saw_output = False
        last_activity = time.monotonic()
        first_audio_t: Optional[float] = None
        boundary = "silent"
        while time.monotonic() < deadline:
            texts = self.provider.drain_agent_texts()
            if texts:
                frags.extend(texts)
                saw_output = True
                boundary = "text"
                last_activity = time.monotonic()
            audio_total = self.provider.agent_audio_total()
            if audio_total > last_audio:
                if first_audio_t is None:
                    first_audio_t = time.monotonic()
                last_audio = audio_total
                saw_output = True
                if boundary == "silent":
                    boundary = "text"
                last_activity = time.monotonic()
            if saw_output:
                gap = quiet_gap_s
                if (self.provider.bot_stopped_total() > stop_snapshot
                        and not self.provider.bot_speaking()):
                    gap = min(self._post_stop_gap_s, quiet_gap_s)
                if (time.monotonic() - last_activity) >= gap:
                    break
            self.provider.wait_activity(0.05)
        else:
            if not saw_output:
                boundary = "timeout"
        if saw_output:
            self._bot_final_t = last_activity
        text = dedup_texts(frags)
        if want_first_audio:
            return text, boundary, first_audio_t, self.provider.agent_audio_total()
        return text, boundary

    def _transcribe_wav(self, path: str) -> dict[str, Any]:
        """Independent re-ASR of the captured agent audio via /api/models/transcribe.
        This is a cross-check on the RTVI transcript, NOT the scored transcript."""
        try:
            with open(path, "rb") as fh:
                r = requests.post(
                    f"{self.config.base_url}/api/models/transcribe",
                    headers={"Authorization": f"Bearer {self.config.api_key}"},
                    files={"file": ("bot.wav", fh, "audio/wav")},
                    timeout=180)
            if r.status_code >= 300:
                return {"error": f"HTTP {r.status_code}: {r.text[:160]}"}
            d = r.json()
            return {"text": d.get("text", ""),
                    "duration_seconds": d.get("duration_seconds")}
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)[:160]}
