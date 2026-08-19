"""Offline tests for the simulated customer's voice.

No network. Every provider call is a stub, so these run with no credentials set and
cannot spend money.

The behaviours pinned here each correspond to a real failure:

* A live run died with `ELEVENLABS_API_KEY is required` before a Whissle session was
  ever created — the path hard-required a provider that is no longer the design.
* An ElevenLabs 400 was raised through `raise_for_status()`, discarding the body. The
  body said `API key must start with 'sk_'`; without it, an invalid key was
  indistinguishable from a quota problem for the length of a whole failed run.
* OpenAI's `pcm` is 24 kHz while the LiveKit publisher wants 16 kHz. Unresampled it
  does not error — it plays fast, and the agent's STT mistranscribes it, which reads
  as an ASR quality problem rather than a format bug.
"""

from __future__ import annotations

import array

import pytest
import requests

from tau2.agent.user_tts import (
    ELEVENLABS_PCM_SAMPLE_RATE,
    OPENAI_PCM_SAMPLE_RATE,
    TARGET_SAMPLE_RATE,
    TTSConfig,
    TTSError,
    UserSimulatorTTS,
    redact,
    resample_pcm16,
)


def _tone(n_samples: int, rate: int) -> bytes:
    """Deterministic PCM16 mono payload; content is irrelevant, length is not."""
    a = array.array("h", [(i * 137) % 3000 - 1500 for i in range(n_samples)])
    return a.tobytes()


class _StubResponse:
    def __init__(self, status_code=200, content=b"", text=""):
        self.status_code = status_code
        self.content = content
        self.text = text


class _StubSession:
    """Stands in for `requests`; records the call and returns a canned response."""

    def __init__(self, response=None, raise_exc=None):
        self.response = response or _StubResponse()
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(
            {
                "url": url,
                "headers": headers or {},
                "json": json or {},
                "timeout": timeout,
            }
        )
        if self.raise_exc:
            raise self.raise_exc
        return self.response


# ── provider selection ───────────────────────────────────────────────────────


def test_openai_is_the_default_provider(monkeypatch):
    monkeypatch.delenv("WHISSLE_USER_TTS_PROVIDER", raising=False)
    assert TTSConfig.from_env().provider == "openai"


def test_default_provider_requires_openai_key_not_elevenlabs(monkeypatch):
    monkeypatch.delenv("WHISSLE_USER_TTS_PROVIDER", raising=False)
    assert TTSConfig.from_env().required_env_var() == "OPENAI_API_KEY"


def test_elevenlabs_is_opt_in(monkeypatch):
    monkeypatch.setenv("WHISSLE_USER_TTS_PROVIDER", "elevenlabs")
    cfg = TTSConfig.from_env()
    assert cfg.provider == "elevenlabs"
    assert cfg.required_env_var() == "ELEVENLABS_API_KEY"


def test_missing_elevenlabs_key_does_not_block_the_openai_path(monkeypatch):
    """The regression that killed the first voice smoke test."""
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-value-1234567890")
    tts = UserSimulatorTTS(TTSConfig(provider="openai"), session=_StubSession())
    tts.require()  # must not raise


def test_unknown_provider_is_rejected_at_construction():
    with pytest.raises(ValueError, match="Unknown WHISSLE_USER_TTS_PROVIDER"):
        UserSimulatorTTS(TTSConfig(provider="azure"))


def test_missing_key_for_selected_provider_names_the_right_variable(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    tts = UserSimulatorTTS(TTSConfig(provider="openai"), session=_StubSession())
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        tts.require()


# ── request shape ────────────────────────────────────────────────────────────


def test_openai_requests_pcm_from_the_speech_endpoint(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-value-1234567890")
    s = _StubSession(_StubResponse(content=_tone(2400, OPENAI_PCM_SAMPLE_RATE)))
    UserSimulatorTTS(TTSConfig(provider="openai"), session=s).synthesize("hello")
    call = s.calls[0]
    assert call["url"] == "https://api.openai.com/v1/audio/speech"
    assert call["json"]["response_format"] == "pcm"
    assert call["json"]["input"] == "hello"
    assert call["headers"]["Authorization"].startswith("Bearer ")


def test_elevenlabs_requests_16k_pcm(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test_key_value_1234567890")
    s = _StubSession(_StubResponse(content=_tone(1600, ELEVENLABS_PCM_SAMPLE_RATE)))
    UserSimulatorTTS(TTSConfig(provider="elevenlabs"), session=s).synthesize("hello")
    assert f"output_format=pcm_{ELEVENLABS_PCM_SAMPLE_RATE}" in s.calls[0]["url"]


def test_empty_text_makes_no_provider_call(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-value-1234567890")
    s = _StubSession()
    tts = UserSimulatorTTS(TTSConfig(provider="openai"), session=s)
    assert tts.synthesize("   ") == b""
    assert s.calls == []


# ── audio format ─────────────────────────────────────────────────────────────


def test_openai_24k_is_resampled_to_16k(monkeypatch):
    """The bug that would masquerade as bad ASR."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-value-1234567890")
    one_second_at_24k = _tone(OPENAI_PCM_SAMPLE_RATE, OPENAI_PCM_SAMPLE_RATE)
    s = _StubSession(_StubResponse(content=one_second_at_24k))
    pcm = UserSimulatorTTS(TTSConfig(provider="openai"), session=s).synthesize("hi")
    # One second in must stay one second out, at the target rate.
    assert len(pcm) // 2 == pytest.approx(TARGET_SAMPLE_RATE, rel=0.01)
    assert len(pcm) % 2 == 0, "PCM16 output must be sample-aligned"


def test_elevenlabs_16k_is_passed_through_unchanged(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_test_key_value_1234567890")
    payload = _tone(1600, ELEVENLABS_PCM_SAMPLE_RATE)
    s = _StubSession(_StubResponse(content=payload))
    pcm = UserSimulatorTTS(TTSConfig(provider="elevenlabs"), session=s).synthesize("hi")
    assert pcm == payload


@pytest.mark.parametrize("n", [0, 1, 2, 100, 4801])
def test_resample_is_sample_aligned_and_never_raises(n):
    out = resample_pcm16(b"\x01\x02" * n, OPENAI_PCM_SAMPLE_RATE, TARGET_SAMPLE_RATE)
    assert len(out) % 2 == 0


def test_resample_is_identity_at_equal_rates():
    payload = _tone(64, TARGET_SAMPLE_RATE)
    assert resample_pcm16(payload, TARGET_SAMPLE_RATE, TARGET_SAMPLE_RATE) == payload


def test_resample_tolerates_an_odd_trailing_byte():
    out = resample_pcm16(b"\x01\x02\x03", OPENAI_PCM_SAMPLE_RATE, TARGET_SAMPLE_RATE)
    assert len(out) % 2 == 0


# ── errors and timeouts ──────────────────────────────────────────────────────


def test_error_body_is_captured_not_discarded(monkeypatch):
    """The exact failure that cost a whole voice run with no explanation."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "deadbeef" * 8)
    body = '{"detail":{"status":"invalid_api_key_prefix","message":"API key must start with \'sk_\'."}}'
    s = _StubSession(_StubResponse(status_code=400, text=body))
    with pytest.raises(TTSError) as ei:
        UserSimulatorTTS(TTSConfig(provider="elevenlabs"), session=s).synthesize("hi")
    assert ei.value.status == 400
    assert "must start with" in str(ei.value), "the provider's explanation must survive"


def test_timeout_is_reported_as_a_tts_error(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-value-1234567890")
    s = _StubSession(raise_exc=requests.Timeout("timed out"))
    tts = UserSimulatorTTS(TTSConfig(provider="openai", timeout_s=12.5), session=s)
    with pytest.raises(TTSError, match="timeout after 12.5s"):
        tts.synthesize("hi")


def test_timeout_value_is_passed_to_the_request(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-value-1234567890")
    s = _StubSession(_StubResponse(content=_tone(240, OPENAI_PCM_SAMPLE_RATE)))
    UserSimulatorTTS(TTSConfig(provider="openai", timeout_s=7.0), session=s).synthesize(
        "hi"
    )
    assert s.calls[0]["timeout"] == 7.0


def test_connection_error_is_wrapped(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-key-value-1234567890")
    s = _StubSession(raise_exc=requests.ConnectionError("dns failure"))
    with pytest.raises(TTSError):
        UserSimulatorTTS(TTSConfig(provider="openai"), session=s).synthesize("hi")


# ── secret redaction ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789",
        "sk_abcdefghijklmnopqrstuvwxyz0123456789",
        "wsk_live_abcdefghijklmnopqrstuvwxyz012345",
        "deadbeef" * 8,  # legacy 64-hex ElevenLabs key
    ],
)
def test_redact_removes_credential_shaped_strings(secret):
    assert secret not in redact(f"request failed with key={secret} trailing")
    assert "[REDACTED]" in redact(f"key={secret}")


def test_redact_handles_authorization_headers():
    assert "abcdefghijkl" not in redact("Authorization: Bearer abcdefghijkl")


def test_error_message_never_contains_the_key(monkeypatch):
    key = "sk-proj-supersecretvalue0123456789abcdef"
    monkeypatch.setenv("OPENAI_API_KEY", key)
    # A provider that echoes the key back in its error body.
    s = _StubSession(
        _StubResponse(status_code=401, text=f'{{"error":"bad key {key}"}}')
    )
    with pytest.raises(TTSError) as ei:
        UserSimulatorTTS(TTSConfig(provider="openai"), session=s).synthesize("hi")
    assert key not in str(ei.value)
    assert key not in ei.value.body


def test_error_body_is_truncated():
    err = TTSError("openai", 500, "x" * 5000)
    assert len(err.body) <= 500


def test_redact_is_safe_on_empty_input():
    assert redact("") == ""
    assert redact(None) == ""
