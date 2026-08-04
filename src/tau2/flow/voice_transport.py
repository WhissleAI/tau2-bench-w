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
        self.signals: list[dict] = []   # every signal frame this session (session-level)

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> str:
        """Open the voice session, join the room, and capture the agent's greeting
        (real-mode agents speak first, like a real answered call). Returns the
        greeting transcript (may be "" if the agent doesn't greet)."""
        self._bg.start()
        self.provider = WhissleRoomProvider(self.config)
        self._bg.run_coroutine(
            self.provider.connect(system_prompt="", tools=[], real=True),
            timeout=self.config.connect_timeout_s + 15)
        self.room = self.provider.session_id
        self.conversation_id = self.provider.conversation_id
        # Handshake that tells the bot the caller is subscribed → triggers greeting.
        self._bg.run_coroutine(self.provider.send_playback_ready(), timeout=10)
        self.greeting = self._collect_until_quiet(
            deadline_s=self._greeting_wait_s, quiet_gap_s=self._quiet_gap_s,
            require_output=True)[0]
        return self.greeting

    def turn(self, user_msg: str, conversation_id: Optional[str] = None) -> TurnResult:
        """Speak one user turn and return the agent's spoken reply, shaped as a
        :class:`TurnResult` (voice has no retrievable flow-trace, so ``flow`` is None →
        ``steps``/``current_state`` degrade exactly as the text client does before the
        trace PR). Voice extras land on ``.raw`` (latency_ms, bot_audio_bytes)."""
        if self.provider is None:
            raise VoiceTransportError("start() must be called before turn().")
        pcm = self.tts.synth(user_msg)
        audio_before = self.provider.agent_audio_total()
        t_send_done = self._send_user_audio(pcm)
        reply, boundary, first_audio_t, audio_after = self._await_reply(t_send_done)
        latency_ms = (round((first_audio_t - t_send_done) * 1000)
                      if first_audio_t is not None else None)
        if latency_ms is not None:
            self.latencies_ms.append(latency_ms)
        vres = VoiceTurnResult(
            reply=reply, latency_ms=latency_ms,
            bot_audio_bytes=max(0, audio_after - audio_before), boundary=boundary)
        # Per-turn meta-signals (hesitation / shadow / speculative) emitted on the data
        # channel during THIS turn — the whole point of running over real voice.
        turn_signals = self._drain_signals()
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
                 "signals": turn_signals},
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

    def _drain_signals(self) -> list[dict]:
        """Return the {kind:"signal"} prediction frames that arrived since the last
        drain, advancing the cursor. Each is a whissle-large-derived per-turn signal —
        ``signal`` names the producer (``shadow`` | ``speculative`` | ``hesitation``) and
        the payload carries its fields (predicted tools, eager draft, emotion-timeline
        entropy/instability for hesitation). Fail-open: no provider / no frames → []."""
        if self.provider is None:
            return []
        evs = self.provider.events()
        new = evs[self._sig_cursor:]
        self._sig_cursor = len(evs)
        sigs = [
            e["data"] for e in new
            if e.get("type") == "server-message"
            and isinstance(e.get("data"), dict)
            and e["data"].get("kind") == "signal"
        ]
        self.signals.extend(sigs)
        return sigs

    # -- internals ---------------------------------------------------------------

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
        """Poll the provider's thread-safe queues until the agent has produced output
        and then stayed quiet (no new transcript AND no new audio) for ``quiet_gap_s``.

        Returns (deduped_text, boundary, first_audio_t, audio_total) when
        ``want_first_audio`` else (deduped_text, boundary)."""
        assert self.provider is not None
        deadline = time.monotonic() + deadline_s
        frags: list[str] = []
        last_audio = self.provider.agent_audio_total()
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
            if saw_output and (time.monotonic() - last_activity) >= quiet_gap_s:
                break
            time.sleep(0.1)
        else:
            if not saw_output:
                boundary = "timeout"
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
