"""Agent-transcript capture in the Whissle room provider.

`bot-output` is a fallback for agents that never emit `bot-transcription`. The
message sequences below are taken from a real half-duplex retail run against a
Whissle agent that emits BOTH channels — unguarded, that recorded every spoken
turn three times (once from bot-transcription, twice from a self-repeating
bot-output stream), polluting what the user simulator reads and what
communicate-info scoring checks.
"""

import json

import pytest

pytest.importorskip("livekit.rtc", reason="whissle provider needs livekit.rtc")

from tau2.voice.audio_native.whissle.config import WhissleConfig  # noqa: E402
from tau2.voice.audio_native.whissle.provider import WhissleRoomProvider  # noqa: E402


def _provider() -> WhissleRoomProvider:
    return WhissleRoomProvider(WhissleConfig())


def _feed(provider: WhissleRoomProvider, msg: dict) -> None:
    provider._on_data(json.dumps(msg).encode("utf-8"))


def _transcription(text: str) -> dict:
    return {"type": "bot-transcription", "data": {"text": text}}


def _output(text: str) -> dict:
    return {"type": "bot-output", "data": {"aggregated_by": "sentence", "text": text}}


# The turn as it actually arrived: bot-transcription split across two packets,
# then the same words twice more as sentence-aggregated bot-output.
_SENTENCES = [
    "No problem.",
    "I can look you up another way.",
    "Can you please provide your first name,",
    "last name, and zip code?",
]


def test_bot_output_ignored_when_transcription_present():
    p = _provider()
    _feed(p, _transcription("No problem. I can look you up another way. Can you please provide your first name,"))
    _feed(p, _transcription("last name, and zip code?"))
    for _ in range(2):
        for s in _SENTENCES:
            _feed(p, _output(s))

    texts = p.drain_agent_texts()
    assert " ".join(texts) == (
        "No problem. I can look you up another way. "
        "Can you please provide your first name, last name, and zip code?"
    )


def test_bot_output_used_when_no_transcription():
    """Agents that only stream bot-output must still be recorded, not silent."""
    p = _provider()
    for s in _SENTENCES:
        _feed(p, _output(s))

    assert p.drain_agent_texts() == _SENTENCES


def test_repeated_bot_output_stream_is_not_duplicated():
    """A bot-output-only agent that replays the turn is recorded once."""
    p = _provider()
    for _ in range(2):
        for s in _SENTENCES:
            _feed(p, _output(s))

    assert p.drain_agent_texts() == _SENTENCES


def test_line_repeated_beyond_the_window_still_recorded():
    """The dedupe is a recency window, not a permanent mute on a phrase."""
    p = _provider()
    _feed(p, _output("Anything else?"))
    for i in range(40):  # push it out of the 32-entry window
        _feed(p, _output(f"filler sentence {i}."))
    _feed(p, _output("Anything else?"))

    texts = p.drain_agent_texts()
    assert texts.count("Anything else?") == 2


def test_bench_agent_text_still_captured():
    """The documented bench envelope is unaffected by the fallback gating."""
    p = _provider()
    _feed(p, {
        "label": "rtvi-ai",
        "type": "server-message",
        "data": {"type": "bench-agent-text", "text": "Let me check that for you."},
    })

    assert p.drain_agent_texts() == ["Let me check that for you."]


def test_bench_tool_call_still_captured():
    p = _provider()
    _feed(p, {
        "label": "rtvi-ai",
        "type": "server-message",
        "data": {
            "type": "bench-tool-call",
            "id": "bt-1",
            "name": "find_user_id_by_name_zip",
            "arguments": {"first_name": "Yusuf", "last_name": "Rossi", "zip": "19122"},
        },
    })

    calls = p.drain_tool_calls()
    assert [c["name"] for c in calls] == ["find_user_id_by_name_zip"]
    assert calls[0]["arguments"]["zip"] == "19122"
