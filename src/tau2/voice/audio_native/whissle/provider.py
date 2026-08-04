"""LiveKit room client for the Whissle audio-native provider.

The benchmark starts a Whissle bench-voice session (POST /api/bench/voice/start),
which spawns Whissle's real voice pipeline as a bot in a fresh LiveKit room and
returns the room's join creds. This client joins that room as the *user*
participant and:

  • publishes the user-simulator audio (a mic track Whissle's STT hears),
  • subscribes to the bot's audio track (Whissle's TTS, what the user "hears"),
  • bridges tool calls over the data channel: Whissle runs no tools itself in
    bench mode — it emits ``bench-tool-call`` server-messages; we run them against
    the tau2 environment and reply with ``bench-tool-result`` client-messages,
  • collects the agent transcript Whissle emits (``bench-agent-text``).

Data-channel envelopes (must match pipecat-bot bot/runners.py exactly):
  bot → us:  {"label":"rtvi-ai","type":"server-message","data":{"type":"bench-tool-call","id","name","arguments"}}
             {"label":"rtvi-ai","type":"server-message","data":{"type":"bench-agent-text","text"}}
  us → bot:  {"type":"client-message","data":{"t":"bench-tool-result","d":{"id","result"}}}
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any, Optional

import requests
from loguru import logger

try:
    from livekit import rtc
except Exception as exc:  # pragma: no cover - import guard
    rtc = None
    _RTC_IMPORT_ERROR = exc
else:
    _RTC_IMPORT_ERROR = None

from tau2.voice.audio_native.whissle.config import WhissleConfig

import sys as _sys
def _DBG(*a):
    print('WHISSLE_DBG', *a, file=_sys.stderr, flush=True)


class WhissleRoomProvider:
    """Joins Whissle's bench-voice LiveKit room and exchanges audio + tool calls."""

    def __init__(self, config: WhissleConfig):
        if rtc is None:
            raise RuntimeError(
                f"livekit.rtc is required for the whissle provider but failed to "
                f"import: {_RTC_IMPORT_ERROR}"
            )
        self.config = config
        self.room: Optional["rtc.Room"] = None
        self.session_id: Optional[str] = None  # room name, surfaced in results
        self.conversation_id: Optional[str] = None  # PR #613: persisted voice flow-trace key
        self._audio_source: Optional["rtc.AudioSource"] = None
        self._agent_pcm = bytearray()  # accumulated bot PCM16 @ agent_sample_rate
        self._agent_pcm_total = 0  # monotonic byte counter (turn-quiet detection)
        # Full-session audio capture (for a duplex-training corpus): every PCM16 byte
        # we RECEIVE from the bot and every byte we SEND as the caller, never cleared.
        self._bot_audio = bytearray()   # bot voice @ agent_sample_rate, mono
        self._user_audio = bytearray()  # caller (ElevenLabs) @ user_sample_rate, mono
        self._agent_lock = asyncio.Lock()
        self._tool_calls: deque[dict] = deque()  # incoming bench-tool-call payloads
        self._agent_texts: deque[str] = deque()  # incoming bench-agent-text
        self._consume_tasks: list[asyncio.Task] = []
        self._connected = False
        # QA telemetry: EVERY data-channel message the bot emits, timestamped, so
        # the audit can inspect tools, latency, turn, shadow, hesitation, gist,
        # errors — not just the transcript. Set at connect().
        import time as _time
        self._time = _time
        self._t0: float = _time.monotonic()
        self._events: list[dict] = []

    # -- lifecycle ---------------------------------------------------------------

    async def connect(self, system_prompt: str, tools: list[dict], real: bool = False) -> None:
        """Start a voice session and join its room. real=True runs the agent as
        deployed (own prompt + tools, greeting on) for QA audits; else bench mode."""
        self.config.require()
        if real:
            body = {"agent_id": self.config.agent_id, "real": True}
        else:
            body = {"agent_id": self.config.agent_id, "tools": tools, "system": system_prompt}
        resp = requests.post(
            f"{self.config.base_url}/api/bench/voice/start",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(body),
            timeout=self.config.connect_timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        url, token, room_name = data["url"], data["token"], data["room"]
        self.session_id = room_name
        # PR #613: real-mode voice/start now creates a conversations row and returns its
        # id, so the voice flow step-trace is retrievable via GET /flow/trace.
        self.conversation_id = data.get("conversation_id")
        logger.info("whissle bench-voice session started — room={}", room_name); _DBG("session-started room=", room_name)

        self.room = rtc.Room()
        self.room.on("track_subscribed", self._on_track)
        self.room.on("data_received", self._on_data)
        await self.room.connect(
            url, token, options=rtc.RoomOptions(auto_subscribe=True)
        )

        # Publish the user-simulator mic track.
        self._audio_source = rtc.AudioSource(
            self.config.user_sample_rate, self.config.num_channels
        )
        track = rtc.LocalAudioTrack.create_audio_track("user-sim", self._audio_source)
        pub = await self.room.local_participant.publish_track(
            track,
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
        )
        _DBG("mic-published sid=", getattr(pub, "sid", "?"), "remote-participants=", list(self.room.remote_participants.keys()) if hasattr(self.room, "remote_participants") else "?")
        self._connected = True
        self._frames_sent = 0
        self._t0 = self._time.monotonic()  # telemetry clock zero = room joined
        self._events.clear()

    async def disconnect(self) -> None:
        self._connected = False
        for t in self._consume_tasks:
            t.cancel()
        self._consume_tasks.clear()
        if self.room is not None:
            try:
                await self.room.disconnect()
            except Exception as exc:  # noqa: BLE001
                logger.warning("whissle room disconnect error: {}", exc)
            self.room = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    # -- audio -------------------------------------------------------------------

    async def send_audio(self, pcm16: bytes) -> None:
        """Publish one tick of user PCM16 into the room, as WebRTC-sized frames.

        A tick's audio is ~200ms; LiveKit/WebRTC ingests small frames (10ms), not one
        big blob — publishing a 200ms frame is silently dropped by the receiver (the
        bot's mic-watchdog sees a track but zero audio). So split into 10ms frames."""
        if not self._audio_source or not pcm16:
            return
        sr = self.config.user_sample_rate
        ch = self.config.num_channels
        # Timeline-align the caller track with the (continuous, real-time) bot track:
        # pad silence up to the current wall-clock offset before appending this
        # utterance, so caller.wav and bot.wav OVERLAY for duplex-model training.
        now_off = int((self._time.monotonic() - self._t0) * sr) * 2 * ch
        if now_off > len(self._user_audio):
            self._user_audio.extend(b"\x00" * (now_off - len(self._user_audio)))
        self._user_audio.extend(pcm16)
        frame_bytes = int(sr * 0.01) * 2 * ch  # 10ms of PCM16
        if frame_bytes <= 0:
            return
        for off in range(0, len(pcm16), frame_bytes):
            chunk = pcm16[off:off + frame_bytes]
            samples = len(chunk) // (2 * ch)
            if samples <= 0:
                continue
            frame = rtc.AudioFrame(
                data=chunk, sample_rate=sr, num_channels=ch, samples_per_channel=samples,
            )
            await self._audio_source.capture_frame(frame)
            self._frames_sent = getattr(self, "_frames_sent", 0) + 1
            if self._frames_sent % 200 == 1:
                _DBG("mic-frame#", self._frames_sent, "bytes=", len(chunk))

    async def drain_agent_audio(self) -> bytes:
        """Return + clear the bot PCM16 accumulated since the last drain."""
        async with self._agent_lock:
            out = bytes(self._agent_pcm)
            self._agent_pcm.clear()
        return out

    def _fragmentation(self, data: bytes, sr: int) -> dict:
        """Speech-run stats for a mono PCM16 track. A healthy track has a few LONG
        continuous runs; event-loop starvation shatters it into hundreds of ~60ms
        slivers (dropped frames). The harness gates corpus quality on ``median_run``
        + ``runs`` so a starved capture is flagged (and can be re-run) rather than
        silently poisoning a duplex-training set."""
        import array
        a = array.array("h")
        a.frombytes(data[: len(data) - (len(data) % 2)])
        win = max(1, int(sr * 0.02)); thr = 300
        runs = []; run = 0
        for i in range(0, len(a), win):
            if max((abs(x) for x in a[i:i + win]), default=0) > thr:
                run += 1
            else:
                if run:
                    runs.append(run * 0.02)
                run = 0
        if run:
            runs.append(run * 0.02)
        runs.sort()
        speech = round(sum(runs), 1)
        return {
            "speech_s": speech,
            "runs": len(runs),
            "median_run_s": round(runs[len(runs) // 2], 3) if runs else 0.0,
            "longest_run_s": round(runs[-1], 2) if runs else 0.0,
        }

    def write_wav(self, prefix: str) -> dict:
        """Write the full session audio as a timeline-aligned duplex corpus entry:
        <prefix>.caller.wav (ElevenLabs caller) + <prefix>.bot.wav (agent) + a
        <prefix>.mix.wav stereo convenience file (caller=L, bot=R) for human audit.

        Both mono tracks are anchored at the same t=0 (room join) and **padded to a
        common length** with trailing silence so they OVERLAY sample-for-sample —
        the shape a duplex model needs. Returns paths, durations, and per-track
        fragmentation stats so the harness can gate on capture quality."""
        import wave
        out = {}
        ch = self.config.num_channels
        csr = int(self.config.user_sample_rate)
        bsr = int(self.config.agent_sample_rate)
        caller = bytes(self._user_audio)
        bot = bytes(self._bot_audio)
        # Pad both to a common wall-clock length so they align on overlay. Tracks
        # share t=0; the shorter one just lacks trailing audio -> fill with silence.
        # (csr == bsr in this config, so a byte-length max is a time max; guard anyway.)
        if csr == bsr:
            n = max(len(caller), len(bot))
            caller += b"\x00" * (n - len(caller))
            bot += b"\x00" * (n - len(bot))
        for tag, data, sr in (("caller", caller, csr), ("bot", bot, bsr)):
            path = f"{prefix}.{tag}.wav"
            try:
                with wave.open(path, "wb") as w:
                    w.setnchannels(ch); w.setsampwidth(2); w.setframerate(sr)
                    w.writeframes(data)
                out[tag] = {"path": path, "bytes": len(data),
                            "seconds": round(len(data) / (2 * ch * sr), 2),
                            "sample_rate": sr, **self._fragmentation(data, sr)}
            except Exception as exc:  # noqa: BLE001
                out[tag] = {"error": str(exc)[:120]}
        # Stereo mix (L=caller, R=bot) — lets a human hear the actual two-party call
        # instead of one clunky-sounding side. Only when both mono tracks are single
        # channel at the same rate (always true here); skip silently otherwise.
        if ch == 1 and csr == bsr and "error" not in out.get("caller", {}) and "error" not in out.get("bot", {}):
            try:
                import array
                la = array.array("h"); la.frombytes(caller[: len(caller) - (len(caller) % 2)])
                ra = array.array("h"); ra.frombytes(bot[: len(bot) - (len(bot) % 2)])
                m = min(len(la), len(ra))
                inter = array.array("h", bytes(4 * m))
                inter[0::2] = la[:m]
                inter[1::2] = ra[:m]
                mpath = f"{prefix}.mix.wav"
                with wave.open(mpath, "wb") as w:
                    w.setnchannels(2); w.setsampwidth(2); w.setframerate(csr)
                    w.writeframes(inter.tobytes())
                out["mix"] = {"path": mpath, "seconds": round(m / csr, 2), "sample_rate": csr}
            except Exception as exc:  # noqa: BLE001 — the mix is a convenience, never fatal
                out["mix"] = {"error": str(exc)[:120]}
        return out

    def _on_track(self, track, publication, participant) -> None:  # noqa: ANN001
        _DBG("track-subscribed kind=", getattr(track,"kind",None))
        if getattr(track, "kind", None) == rtc.TrackKind.KIND_AUDIO:
            self._consume_tasks.append(asyncio.ensure_future(self._consume_audio(track)))

    async def _consume_audio(self, track) -> None:  # noqa: ANN001
        stream = rtc.AudioStream(
            track,
            sample_rate=self.config.agent_sample_rate,
            num_channels=self.config.num_channels,
        )
        try:
            _rx = 0
            async for event in stream:
                frame = getattr(event, "frame", event)
                data = frame.data.tobytes() if hasattr(frame.data, "tobytes") else bytes(frame.data)
                async with self._agent_lock:
                    self._agent_pcm.extend(data)
                    self._agent_pcm_total += len(data)
                    self._bot_audio.extend(data)  # capture the full bot track
                _rx += 1
                if _rx % 100 == 1:
                    _DBG("bot-audio-frame#", _rx, "bytes=", len(data))
        except asyncio.CancelledError:  # normal on disconnect
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("whissle audio consume ended: {}", exc)

    # -- data channel ------------------------------------------------------------

    def _on_data(self, *args) -> None:
        """LiveKit data handler. Signature varies by rtc version — the first arg is
        either a DataPacket (has .data) or raw bytes; find the payload defensively."""
        payload: Optional[bytes] = None
        for a in args:
            if isinstance(a, (bytes, bytearray)):
                payload = bytes(a)
                break
            data_attr = getattr(a, "data", None)
            if isinstance(data_attr, (bytes, bytearray)):
                payload = bytes(data_attr)
                break
        if payload is None:
            return
        try:
            msg = json.loads(payload.decode("utf-8"))
        except Exception:  # noqa: BLE001 — not our JSON
            return
        if not isinstance(msg, dict):
            return
        mtype = msg.get("type")
        _inner_obj = msg.get("data") if isinstance(msg.get("data"), dict) else None
        _inner_type = _inner_obj.get("type") if _inner_obj else None
        # QA telemetry: record EVERY message with a relative timestamp. Bulky audio
        # payloads (base64) are dropped so the log stays inspectable.
        _rec = msg.get("data")
        if isinstance(_rec, dict) and isinstance(_rec.get("d"), dict):
            _rec = {**_rec, "d": {k: ("<b64>" if k in ("audio", "base64", "image") else v)
                                  for k, v in _rec["d"].items()}}
        self._events.append({
            "t_ms": round((self._time.monotonic() - self._t0) * 1000),
            "type": mtype,
            "inner": _inner_type,
            "data": _rec if mtype != "metrics" else None,  # metrics kept as count only
        })
        _DBG("data", "type=", mtype, "inner=", _inner_type)
        # The bot broadcasts its own spoken transcript as a standard RTVI
        # `bot-transcription` message ({type, data:{text}}) — use it as the agent
        # transcript (the user sim also hears the audio; this feeds scoring/content).
        if mtype == "bot-transcription":
            text = str((msg.get("data") or {}).get("text") or "").strip()
            if text:
                self._agent_texts.append(text)
                _DBG("says:", text[:200])
            return
        # Some agents (notably the companion) never emit `bot-transcription` — they
        # stream the reply as sentence-aggregated `bot-output`. Read those too, else
        # a talking bot is mis-recorded as silent. Take the sentence granularity
        # (the pre-speech full line) to avoid stitching word fragments.
        if mtype == "bot-output":
            _d = msg.get("data") or {}
            if isinstance(_d, dict) and _d.get("aggregated_by") == "sentence":
                text = str(_d.get("text") or "").strip()
                if text:
                    self._agent_texts.append(text)
                    _DBG("says(bot-output):", text[:200])
            return
        if mtype != "server-message":
            return
        inner = msg.get("data")
        if not isinstance(inner, dict):
            return
        kind = inner.get("type")
        if kind == "bench-tool-call":
            self._tool_calls.append(inner)
            _DBG("tool-call", inner.get("name"), inner.get("arguments"))
        elif kind == "bench-agent-text":
            text = str(inner.get("text") or "").strip()
            if text:
                self._agent_texts.append(text)
                _DBG("says:", text[:200])

    def agent_audio_total(self) -> int:
        """Monotonic total bot PCM bytes received (thread-safe read of an int)."""
        return self._agent_pcm_total

    def events(self) -> list[dict]:
        """The full timestamped data-channel event stream for this session."""
        return list(self._events)

    def drain_tool_calls(self) -> list[dict]:
        calls: list[dict] = []
        while self._tool_calls:
            calls.append(self._tool_calls.popleft())
        return calls

    def drain_agent_texts(self) -> list[str]:
        texts: list[str] = []
        while self._agent_texts:
            texts.append(self._agent_texts.popleft())
        return texts

    async def send_playback_ready(self) -> None:
        """Tell the bot the caller is subscribed + playing — triggers the greeting
        (real-agent mode; mirrors the browser's playback-ready handshake)."""
        if self.room is None:
            return
        raw = json.dumps({"type": "client-message", "data": {"t": "playback-ready"}}).encode("utf-8")
        try:
            await self.room.local_participant.publish_data(raw, reliable=True)
        except TypeError:
            await self.room.local_participant.publish_data(raw)

    async def send_tool_result(self, call_id: str, result: Any) -> None:
        """Reply to a delegated tool call over the data channel (reliable)."""
        if self.room is None:
            return
        envelope = {
            "type": "client-message",
            "data": {"t": "bench-tool-result", "d": {"id": call_id, "result": result}},
        }
        raw = json.dumps(envelope).encode("utf-8")
        _DBG("tool-result", call_id, str(result)[:160])
        try:
            await self.room.local_participant.publish_data(raw, reliable=True)
        except TypeError:
            # Older rtc: positional-only reliability flag.
            await self.room.local_participant.publish_data(raw)
