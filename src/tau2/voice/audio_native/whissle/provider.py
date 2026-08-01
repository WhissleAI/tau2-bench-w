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
        self._audio_source: Optional["rtc.AudioSource"] = None
        self._agent_pcm = bytearray()  # accumulated bot PCM16 @ agent_sample_rate
        self._agent_lock = asyncio.Lock()
        self._tool_calls: deque[dict] = deque()  # incoming bench-tool-call payloads
        self._agent_texts: deque[str] = deque()  # incoming bench-agent-text
        self._consume_tasks: list[asyncio.Task] = []
        self._connected = False

    # -- lifecycle ---------------------------------------------------------------

    async def connect(self, system_prompt: str, tools: list[dict]) -> None:
        """Start a bench-voice session and join its room."""
        self.config.require()
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
        _DBG("data", "type=", mtype, "inner=", (msg.get("data") or {}).get("type") if isinstance(msg.get("data"), dict) else None)
        # The bot broadcasts its own spoken transcript as a standard RTVI
        # `bot-transcription` message ({type, data:{text}}) — use it as the agent
        # transcript (the user sim also hears the audio; this feeds scoring/content).
        if mtype == "bot-transcription":
            text = str((msg.get("data") or {}).get("text") or "").strip()
            if text:
                self._agent_texts.append(text)
                _DBG("says:", text[:200])
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
