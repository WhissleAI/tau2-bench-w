# Copyright Sierra
"""Text-to-speech for the SIMULATED CUSTOMER in the half-duplex Whissle path.

This synthesises the *user simulator's* voice — the caller. It is not the agent's
voice: the agent's speech is produced server-side by Whissle's own pipeline, and its
transcript comes back over the LiveKit data channel.

Provider selection
------------------
``WHISSLE_USER_TTS_PROVIDER`` selects the backend; **OpenAI is the default**.
ElevenLabs remains reachable for continuity with older runs but is no longer
required — nothing raises when ``ELEVENLABS_API_KEY`` is unset.

Audio contract
--------------
The room publisher needs **PCM16, mono, 16 kHz**. The providers do not agree on that:

* ElevenLabs ``output_format=pcm_16000`` returns 16 kHz directly.
* OpenAI ``response_format="pcm"`` returns **24 kHz** 16-bit mono.

So the OpenAI path *must* be resampled 24k→16k. Publishing 24 kHz samples into a
16 kHz track does not error — it plays ~1.5× fast and slurred, which the agent's STT
mistranscribes. That failure looks like bad ASR rather than a format bug, so the
conversion is done here, once, rather than being left to the caller.

Secrets
-------
Provider errors carry a response body that is useful for diagnosis and that may echo
request fields. Bodies are truncated and scrubbed by :func:`redact` before they reach
a log line or an exception message, and the API key is never placed in either.
"""

from __future__ import annotations

import array
import os
import re
from dataclasses import dataclass

import requests
from loguru import logger

# The publisher's contract. Both providers are normalised to this.
TARGET_SAMPLE_RATE = 16_000
TARGET_CHANNELS = 1
TARGET_SAMPLE_WIDTH = 2  # bytes; PCM16

OPENAI_PCM_SAMPLE_RATE = 24_000  # fixed by the OpenAI audio/speech `pcm` format
ELEVENLABS_PCM_SAMPLE_RATE = 16_000
DEEPGRAM_PCM_SAMPLE_RATE = 16_000  # we request linear16 @ 16 kHz directly

DEFAULT_OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_OPENAI_TTS_VOICE = "alloy"
DEFAULT_ELEVENLABS_VOICE = "EXAVITQu4vr4xnSDxMaL"  # "Sarah"
DEFAULT_ELEVENLABS_MODEL = "eleven_turbo_v2_5"
DEFAULT_DEEPGRAM_MODEL = "aura-2-thalia-en"  # Deepgram aura-2 voice

DEFAULT_TIMEOUT_S = 60.0
_MAX_BODY_CHARS = 500

# Anything shaped like a provider credential. Matched conservatively and by shape so
# a new provider's key format is still caught: long opaque tokens with a known prefix.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),  # OpenAI
    re.compile(r"sk_[A-Za-z0-9_\-]{16,}"),  # ElevenLabs (current format)
    re.compile(r"wsk_[A-Za-z0-9_\-]{16,}"),  # Whissle
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{8,}", re.I),
    re.compile(r"\b[0-9a-f]{64}\b"),  # legacy 64-hex keys (old ElevenLabs)
]


def redact(text: str) -> str:
    """Replace anything credential-shaped with a marker. Safe on None/empty."""
    if not text:
        return ""
    out = str(text)
    for pat in _SECRET_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    return out


class TTSError(RuntimeError):
    """A provider call failed. The message is already redacted and truncated."""

    def __init__(self, provider: str, status: int | None, body: str):
        self.provider = provider
        self.status = status
        self.body = redact(body)[:_MAX_BODY_CHARS]
        status_s = status if status is not None else "no-response"
        super().__init__(f"{provider} TTS failed (status={status_s}): {self.body}")


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolate mono PCM16 from ``src_rate`` to ``dst_rate``.

    Deliberately dependency-free: ``audioop`` was removed in Python 3.13 and pulling a
    DSP dependency in for one ratio is not worth it. Linear interpolation is adequate
    here — the consumer is a speech recogniser, not a listener.
    """
    if src_rate == dst_rate or not pcm:
        return pcm
    if len(pcm) % 2:
        pcm = pcm[:-1]  # drop a trailing odd byte rather than misalign every sample
    samples = array.array("h")
    samples.frombytes(pcm)
    n_src = len(samples)
    if n_src == 0:
        return b""
    n_dst = max(1, int(n_src * dst_rate / src_rate))
    out = array.array("h", bytes(2 * n_dst))
    ratio = (n_src - 1) / (n_dst - 1) if n_dst > 1 else 0.0
    for i in range(n_dst):
        pos = i * ratio
        left = int(pos)
        right = min(left + 1, n_src - 1)
        frac = pos - left
        out[i] = int(samples[left] + (samples[right] - samples[left]) * frac)
    return out.tobytes()


def _strip_wav(b: bytes) -> bytes:
    """Return the raw PCM samples from a WAV container (Deepgram wraps linear16 in
    RIFF/WAVE); pass through data that is already headerless PCM."""
    if b[:4] != b"RIFF":
        return b
    i = b.find(b"data")
    if i == -1:
        return b[44:]  # standard 44-byte PCM WAV header fallback
    return b[i + 8:]  # skip "data" (4) + chunk-size (4)


@dataclass
class TTSConfig:
    provider: str = "openai"
    openai_model: str = DEFAULT_OPENAI_TTS_MODEL
    openai_voice: str = DEFAULT_OPENAI_TTS_VOICE
    elevenlabs_voice: str = DEFAULT_ELEVENLABS_VOICE
    elevenlabs_model: str = DEFAULT_ELEVENLABS_MODEL
    deepgram_model: str = DEFAULT_DEEPGRAM_MODEL
    timeout_s: float = DEFAULT_TIMEOUT_S

    @classmethod
    def from_env(cls) -> "TTSConfig":
        return cls(
            provider=(os.getenv("WHISSLE_USER_TTS_PROVIDER") or "openai")
            .strip()
            .lower(),
            openai_model=os.getenv("OPENAI_TTS_MODEL") or DEFAULT_OPENAI_TTS_MODEL,
            openai_voice=os.getenv("OPENAI_TTS_VOICE") or DEFAULT_OPENAI_TTS_VOICE,
            # WHISSLE_USER_VOICE_ID kept for continuity with existing runbooks.
            elevenlabs_voice=os.getenv("WHISSLE_USER_VOICE_ID")
            or DEFAULT_ELEVENLABS_VOICE,
            elevenlabs_model=os.getenv("ELEVENLABS_TTS_MODEL")
            or DEFAULT_ELEVENLABS_MODEL,
            deepgram_model=os.getenv("DEEPGRAM_TTS_MODEL") or DEFAULT_DEEPGRAM_MODEL,
            timeout_s=float(
                os.getenv("WHISSLE_USER_TTS_TIMEOUT_S") or DEFAULT_TIMEOUT_S
            ),
        )

    def required_env_var(self) -> str:
        if self.provider == "elevenlabs":
            return "ELEVENLABS_API_KEY"
        if self.provider == "deepgram":
            return "DEEPGRAM_API_KEY"
        return "OPENAI_API_KEY"


class UserSimulatorTTS:
    """Synthesises the simulated customer's turns to PCM16 mono @ 16 kHz."""

    def __init__(
        self, config: TTSConfig | None = None, session: requests.Session | None = None
    ):
        self.config = config or TTSConfig.from_env()
        if self.config.provider not in ("openai", "elevenlabs", "deepgram"):
            raise ValueError(
                f"Unknown WHISSLE_USER_TTS_PROVIDER {self.config.provider!r}; "
                "expected 'openai', 'elevenlabs' or 'deepgram'."
            )
        self._session = session or requests
        self._key = os.getenv(self.config.required_env_var()) or ""

    def require(self) -> None:
        """Fail fast, before a run starts, if the selected provider has no credential."""
        if not self._key:
            var = self.config.required_env_var()
            raise ValueError(
                f"{var} is required for the simulated-customer voice "
                f"(WHISSLE_USER_TTS_PROVIDER={self.config.provider})."
            )

    def synthesize(self, text: str) -> bytes:
        """Return PCM16 mono @ 16 kHz. Empty text yields empty bytes, not a call."""
        text = (text or "").strip()
        if not text:
            return b""
        self.require()
        if self.config.provider == "elevenlabs":
            raw, rate = self._elevenlabs(text), ELEVENLABS_PCM_SAMPLE_RATE
        elif self.config.provider == "deepgram":
            raw, rate = self._deepgram(text), DEEPGRAM_PCM_SAMPLE_RATE
        else:
            raw, rate = self._openai(text), OPENAI_PCM_SAMPLE_RATE
        return resample_pcm16(raw, rate, TARGET_SAMPLE_RATE)

    # -- providers ---------------------------------------------------------------

    def _post(self, provider: str, url: str, *, headers: dict, json: dict) -> bytes:
        try:
            r = self._session.post(
                url, headers=headers, json=json, timeout=self.config.timeout_s
            )
        except requests.Timeout as e:
            raise TTSError(
                provider, None, f"timeout after {self.config.timeout_s}s"
            ) from e
        except requests.RequestException as e:
            # str(e) can embed the request URL; redact before it reaches a log.
            raise TTSError(provider, None, redact(str(e))) from e
        if r.status_code >= 400:
            # The body is the only place the provider explains itself — the previous
            # implementation called raise_for_status() and discarded it, which is why
            # an invalid-key 400 was indistinguishable from a quota 400.
            body = ""
            try:
                body = r.text or ""
            except Exception:  # pragma: no cover - body decode is best-effort
                body = "<unreadable body>"
            logger.error(
                "user-sim TTS {} failed: status={} body={}",
                provider,
                r.status_code,
                redact(body)[:_MAX_BODY_CHARS],
            )
            raise TTSError(provider, r.status_code, body)
        return r.content

    def _openai(self, text: str) -> bytes:
        return self._post(
            "openai",
            "https://api.openai.com/v1/audio/speech",
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.config.openai_model,
                "voice": self.config.openai_voice,
                "input": text,
                "response_format": "pcm",
            },
        )

    def _elevenlabs(self, text: str) -> bytes:
        return self._post(
            "elevenlabs",
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.config.elevenlabs_voice}"
            f"?output_format=pcm_{ELEVENLABS_PCM_SAMPLE_RATE}",
            headers={"xi-api-key": self._key, "Content-Type": "application/json"},
            json={"text": text, "model_id": self.config.elevenlabs_model},
        )

    def _deepgram(self, text: str) -> bytes:
        # Deepgram's /v1/speak returns linear16 @ 16 kHz — but RIFF/WAV-wrapped, while
        # the publisher wants headerless PCM16. Strip the container to the data chunk.
        wav = self._post(
            "deepgram",
            "https://api.deepgram.com/v1/speak"
            f"?model={self.config.deepgram_model}"
            f"&encoding=linear16&sample_rate={DEEPGRAM_PCM_SAMPLE_RATE}",
            headers={
                "Authorization": f"Token {self._key}",
                "Content-Type": "application/json",
            },
            json={"text": text},
        )
        return _strip_wav(wav)
