"""Configuration for the Whissle audio-native provider.

Unlike the other providers, Whissle's brain (STT → LLM → TTS + hesitation/emotion
signals) runs server-side; the benchmark joins a LiveKit room as the *user*
participant. So this config carries only connection + bench-session parameters —
there is no local model to configure.

All fields default from the environment so a run needs only:

    export WHISSLE_BASE=https://aws-gateway-backend.whissle.ai/bot
    export WHISSLE_AGENT_ID=<a configured agent in your org>
    export WHISSLE_API_KEY=<a wsk_ key for that org>
"""

import os
from dataclasses import dataclass, field


@dataclass
class WhissleConfig:
    # Backend base (the gateway mounts pipecat-bot under /bot).
    base_url: str = field(
        default_factory=lambda: (
            os.getenv("WHISSLE_BASE") or "https://aws-gateway-backend.whissle.ai/bot"
        ).rstrip("/")
    )
    agent_id: str = field(default_factory=lambda: os.getenv("WHISSLE_AGENT_ID", ""))
    api_key: str = field(default_factory=lambda: os.getenv("WHISSLE_API_KEY", ""))

    # PCM16 sample rate we publish (user-simulator audio). Whissle STT resamples,
    # but 16 kHz matches nova-3 / saaras natively.
    user_sample_rate: int = 16000
    # Sample rate we request when subscribing to the bot's audio track (LiveKit
    # resamples the bot TTS to this before we convert it to telephony).
    agent_sample_rate: int = 16000
    num_channels: int = 1

    # Seconds to await a delegated tool's result over the data channel.
    tool_timeout_s: float = 30.0
    # Seconds to await the LiveKit room join + first bot presence.
    connect_timeout_s: float = 30.0

    # Informational label surfaced as the "model" in results.
    model: str = "whissle-voice"

    def require(self) -> None:
        if not self.agent_id or not self.api_key:
            raise ValueError(
                "WHISSLE_AGENT_ID and WHISSLE_API_KEY are required for the whissle "
                "audio-native provider (see WHISSLE_VOICE.md)."
            )
