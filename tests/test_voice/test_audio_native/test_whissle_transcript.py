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


def test_flow_say_after_llm_reply_is_recorded():
    """Flow `say` states — scripted greetings, goodbyes, urgent escalations,
    spoken via TTSSpeakFrame with no LLM involved — surface ONLY as `bot-output`
    (`bot-transcription` derives exclusively from LLM text). The first cut of
    the triplication fix gated bot-output on "ever saw a bot-transcription",
    which recorded EMPTY agent turns for every say after the first LLM reply
    while the audio played the goodbye (headache_enrollment flow bench,
    2026-08-06: hx_happy_full turns 15-18 empty; the dental control's transcript
    died after turn 1). New words on bot-output must be recorded; only a replay
    of already-recorded text is dropped."""
    p = _provider()
    _feed(p, _transcription("Got it — that's everything I need."))
    # bot-output replay of the SAME reply → still dropped (no triplication).
    _feed(p, _output("Got it — that's everything I need."))
    # The flow's closing say: new words, bot-output only → MUST be recorded.
    _feed(p, _output("Thank you so much for sharing all of that. Your profile is saved."))
    _feed(p, _output("Take care, and have a great day. Goodbye."))

    assert p.drain_agent_texts() == [
        "Got it — that's everything I need.",
        "Thank you so much for sharing all of that. Your profile is saved.",
        "Take care, and have a great day. Goodbye.",
    ]


def test_urgent_say_mid_call_is_recorded():
    """The medical red-flag shape: an LLM reply has been transcribed, then the
    flow speaks its urgent-escalation say. The say is what the user simulator
    (and a compliance judge) must see."""
    p = _provider()
    _feed(p, _transcription("I'm listening — take your time. What's going on?"))
    _feed(p, _output("I'm listening — take your time."))     # replay fragment → dropped
    _feed(p, _output("What you're describing may need urgent medical attention. "
                     "Please contact emergency services right away."))

    texts = p.drain_agent_texts()
    assert any("urgent medical attention" in t for t in texts), (
        "the flow's say line arrived only on bot-output and must be recorded"
    )


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
