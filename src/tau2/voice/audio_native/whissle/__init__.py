"""Whissle audio-native provider — benchmark Whissle's real voice pipeline.

Whissle's STT→LLM→TTS pipeline (plus hesitation/emotion signals) runs server-side
over LiveKit; the benchmark joins the room as the user participant and delegates
tool execution back over the data channel. See WHISSLE_VOICE.md.
"""

from tau2.voice.audio_native.whissle.config import WhissleConfig
from tau2.voice.audio_native.whissle.discrete_time_adapter import WhissleAdapter

__all__ = ["WhissleConfig", "WhissleAdapter"]
