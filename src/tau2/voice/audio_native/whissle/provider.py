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
        logger.info("whissle bench-voice session started — room={}", room_name)

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
        await self.room.local_participant.publish_track(
            track,
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
        )
        self._connected = True

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
        """Publish one tick of user PCM16 into the room."""
        if not self._audio_source or not pcm16:
            return
        samples = len(pcm16) // (2 * self.config.num_channels)
        if samples <= 0:
            return
        frame = rtc.AudioFrame(
            data=pcm16,
            sample_rate=self.config.user_sample_rate,
            num_channels=self.config.num_channels,
            samples_per_channel=samples,
        )
        await self._audio_source.capture_frame(frame)

    async def drain_agent_audio(self) -> bytes:
        """Return + clear the bot PCM16 accumulated since the last drain."""
        async with self._agent_lock:
            out = bytes(self._agent_pcm)
            self._agent_pcm.clear()
        return out

    def _on_track(self, track, publication, participant) -> None:  # noqa: ANN001
        if getattr(track, "kind", None) == rtc.TrackKind.KIND_AUDIO:
            self._consume_tasks.append(asyncio.ensure_future(self._consume_audio(track)))

    async def _consume_audio(self, track) -> None:  # noqa: ANN001
        stream = rtc.AudioStream(
            track,
            sample_rate=self.config.agent_sample_rate,
            num_channels=self.config.num_channels,
        )
        try:
            async for event in stream:
                frame = getattr(event, "frame", event)
                data = frame.data.tobytes() if hasattr(frame.data, "tobytes") else bytes(frame.data)
                async with self._agent_lock:
                    self._agent_pcm.extend(data)
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
        if not isinstance(msg, dict) or msg.get("type") != "server-message":
            return
        inner = msg.get("data")
        if not isinstance(inner, dict):
            return
        kind = inner.get("type")
        if kind == "bench-tool-call":
            self._tool_calls.append(inner)
        elif kind == "bench-agent-text":
            text = str(inner.get("text") or "").strip()
            if text:
                self._agent_texts.append(text)

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
        try:
            await self.room.local_participant.publish_data(raw, reliable=True)
        except TypeError:
            # Older rtc: positional-only reliability flag.
            await self.room.local_participant.publish_data(raw)
