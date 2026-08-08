"""The perception pass — run ONCE per case, shared by every arm.

This is the module that makes the ablation single-variable. Each case is spoken
once (``/api/models/tts``), heard once (``/api/models/transcribe``), and the
resulting ``(asr_text, metadata)`` pair is cached on disk. Every arm is then
prompted from that *same* transcript. ASR quality, audio, voice and decoding are
therefore identical across arms by construction — not by re-running the pipeline
twice and hoping it lands in the same place.

Where the metadata actually comes from
--------------------------------------
``/api/models/transcribe`` transcribes on an external engine and, **in parallel**,
feeds the same audio to whissle-large purely for its metadata head
(``services/whissle_batch_metadata.py``). That is the real Whissle cascade head,
serving from production, and it is the only externally reachable source of it.

It matters that this is the *batch* path. On the live voice path the head is not
running at all: ``bot/services_build.py`` routes production STT to
AssemblyAI/Sarvam/Deepgram, none of which emit a metadata head, and the live
sidecar is gated behind ``WHISSLE_STT_TRANSPORT=grpc``. See :mod:`tau2.ablation.audit`.

The head fails open, and often
------------------------------
``whissle_batch_metadata`` returns ``None`` on any problem — gRPC unreachable,
timeout, decode failure — and the endpoint ships the transcript without the
``metadata`` key rather than failing. Measured on production, the head answers
roughly half the time and the misses are ``WHISSLE_BATCH_META_TIMEOUT_S`` (30 s)
timeouts. A caller cannot tell a "no metadata" response from a "metadata was
never meant to be here" response.

For an ablation that is a trap, so this module treats it as one:
:func:`perceive` **retries until the head answers** and marks a case
``metadata_available=False`` when it never does. A case without metadata is
excluded from the paired comparison — it can never be run as an arm-B that
silently equals arm A.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import requests

DEFAULT_BASE = "https://aws-gateway-backend.whissle.ai/bot"

#: How many times to ask for a transcript before giving up on the metadata head.
#: The head answers ~50% of the time, so 6 attempts leaves a ~1.6% chance of
#: losing a case to bad luck alone.
DEFAULT_METADATA_ATTEMPTS = 6


class PerceptionError(RuntimeError):
    """TTS or ASR could not be reached. The case was never measured."""


@dataclass
class Perception:
    """What the cascade heard for one case. Frozen and reused by every arm."""

    case_id: str
    spoken: str
    asr_text: str = ""
    metadata: Optional[dict[str, Any]] = None
    metadata_available: bool = False
    #: How many transcribe calls it took before the metadata head answered.
    metadata_attempts: int = 0
    #: True on the *first* attempt — the production availability rate a normal
    #: caller of this endpoint would experience.
    metadata_first_attempt: bool = False
    audio_sha: str = ""
    audio_bytes: int = 0
    duration_s: Optional[float] = None
    tts_ms: Optional[int] = None
    asr_ms: Optional[int] = None
    asr_cost_usd: float = 0.0
    tts_cost_usd: float = 0.0
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Perception":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


class Ear:
    """TTS + ASR against the Whissle model API, with an on-disk cache."""

    def __init__(
        self,
        *,
        base: Optional[str] = None,
        api_key: Optional[str] = None,
        cache_dir: Optional[Path] = None,
        voice: Optional[str] = None,
        language: str = "en",
        timeout: float = 180.0,
        metadata_attempts: int = DEFAULT_METADATA_ATTEMPTS,
    ) -> None:
        self.base = (base or os.getenv("WHISSLE_BASE") or DEFAULT_BASE).rstrip("/")
        self.api_key = api_key or os.getenv("WHISSLE_API_KEY") or ""
        if not self.api_key:
            raise PerceptionError("WHISSLE_API_KEY not set — put a wsk_ key in .env.")
        self.cache_dir = Path(cache_dir) if cache_dir else Path(".cache/ablation")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "audio").mkdir(exist_ok=True)
        self.voice = voice
        self.language = language
        self.timeout = timeout
        self.metadata_attempts = max(1, metadata_attempts)
        self._s = requests.Session()
        self._s.headers.update({"Authorization": f"Bearer {self.api_key}"})

    # -- TTS ---------------------------------------------------------------

    def speak(self, text: str) -> tuple[bytes, int]:
        """Synthesize one utterance. Cached on the text hash, so re-running the
        suite does not re-synthesize (and does not introduce new audio, which
        would break comparability with an earlier run)."""
        h = hashlib.sha256(f"{self.voice or ''}|{self.language}|{text}".encode()).hexdigest()[:24]
        path = self.cache_dir / "audio" / f"{h}.mp3"
        if path.exists() and path.stat().st_size > 1000:
            return path.read_bytes(), 0
        body: dict[str, Any] = {"text": text, "language": self.language}
        if self.voice:
            body["voice"] = self.voice
        t0 = time.time()
        last = ""
        for attempt in range(4):
            try:
                r = self._s.post(f"{self.base}/api/models/tts", json=body, timeout=self.timeout)
            except requests.RequestException as e:
                last = str(e)
            else:
                if r.status_code < 300 and len(r.content) > 1000:
                    path.write_bytes(r.content)
                    return r.content, int((time.time() - t0) * 1000)
                if r.status_code < 500:
                    raise PerceptionError(f"tts -> HTTP {r.status_code}: {r.text[:300]}")
                last = f"HTTP {r.status_code}"
            time.sleep(2 * (attempt + 1))
        raise PerceptionError(f"tts failed after retries: {last}")

    # -- ASR + metadata head -----------------------------------------------

    def _transcribe_once(self, audio: bytes) -> tuple[dict[str, Any], int]:
        t0 = time.time()
        r = self._s.post(
            f"{self.base}/api/models/transcribe",
            files={"file": ("case.mp3", audio, "audio/mpeg")},
            data={"language": self.language},
            timeout=self.timeout,
        )
        ms = int((time.time() - t0) * 1000)
        if r.status_code >= 400:
            raise PerceptionError(f"transcribe -> HTTP {r.status_code}: {r.text[:300]}")
        return r.json(), ms

    def perceive(self, case_id: str, spoken: str, *, require_metadata: bool = True) -> Perception:
        """One case, all the way through the cascade. Cached on disk.

        ``require_metadata`` retries the transcription until the metadata head
        answers. The retries change nothing about the audio or the transcript —
        the transcript is deterministic for this audio — so this buys metadata
        availability without contaminating the arm comparison. The *first*
        attempt's availability is recorded separately, because that is the number
        an ordinary caller of the endpoint experiences.
        """
        cache = self.cache_dir / f"{case_id}.json"
        if cache.exists():
            try:
                cached = Perception.from_dict(json.loads(cache.read_text()))
                if cached.spoken == spoken and (cached.metadata_available or not require_metadata):
                    return cached
            except Exception:
                pass

        p = Perception(case_id=case_id, spoken=spoken)
        audio, tts_ms = self.speak(spoken)
        p.audio_sha = hashlib.sha256(audio).hexdigest()[:16]
        p.audio_bytes = len(audio)
        p.tts_ms = tts_ms

        attempts = self.metadata_attempts if require_metadata else 1
        asr_ms_total = 0
        for i in range(attempts):
            doc, ms = self._transcribe_once(audio)
            asr_ms_total += ms
            p.metadata_attempts = i + 1
            # The transcript is a property of the audio; take the first one and
            # keep it, so retrying for metadata cannot also shop for a transcript.
            if not p.asr_text:
                p.asr_text = (doc.get("text") or "").strip()
                p.duration_s = doc.get("duration_seconds")
                p.asr_ms = ms
                try:
                    p.asr_cost_usd = float(doc.get("cost_usd") or 0.0)
                except (TypeError, ValueError):
                    pass
            meta = doc.get("metadata")
            if meta:
                p.metadata = meta
                p.metadata_available = True
                p.metadata_first_attempt = i == 0
                break
            if i < attempts - 1:
                time.sleep(1.0)

        if not p.metadata_available and require_metadata:
            p.warnings.append(
                f"metadata head did not answer in {attempts} attempts — case excluded "
                "from the paired comparison"
            )
        if not p.asr_text:
            p.error = "ASR returned no text"

        cache.write_text(json.dumps(p.to_dict(), indent=1, ensure_ascii=False))
        return p

    # -- run-level preflight -----------------------------------------------

    def preflight(self, probes: int = 6) -> dict[str, Any]:
        """Prove the probability **substrate is populating** before spending a run.

        This is the precondition the whole ablation rests on, and it is not a flag
        check. A flag can be set while the head returns nothing; the head can
        return a ``metadata`` key while the distributions behind it are a single
        point; and either way arm B collapses into arm A and the run produces a
        reproducible zero that reads as "metadata does not help" and means "there
        was no metadata". So the gate is on what actually arrived:

        ``serving``            the ``metadata`` key appeared at all
        ``probs_populated``    ``probs.emotion`` / ``probs.intent`` carry a real
                               distribution, not a single collapsed entry
        ``timeline_populated`` ``hesitation_*_snapshots`` > 1, i.e. the per-interim
                               ``metadata_probs_timeline`` that hesitation and every
                               other predictive consumer is derived from was
                               actually built for this utterance

        Only ``serving`` aborts a run — the other two are reported, because a
        populated-but-degenerate substrate is a finding rather than a fault, and
        suppressing the run would suppress the finding.
        """
        text = ("This is a preflight utterance for the metadata ablation. "
                "My member ID is four eight two nine one seven, and I would like to "
                "move my appointment to March fourteenth.")
        audio, _ = self.speak(text)
        hits, lat = 0, []
        probs_ok, timeline_ok, snapshot_counts, dist_sizes = 0, 0, [], []
        for _ in range(max(1, probes)):
            t0 = time.time()
            doc, _ms = self._transcribe_once(audio)
            lat.append(round(time.time() - t0, 1))
            meta = doc.get("metadata")
            if not meta:
                continue
            hits += 1
            probs = meta.get("probs") or {}
            sizes = {k: len(v or []) for k, v in probs.items()}
            dist_sizes.append(sizes)
            if (sizes.get("emotion", 0) + sizes.get("intent", 0)) > 2:
                probs_ok += 1
            snaps = meta.get("hesitation_emotion_snapshots")
            if isinstance(snaps, (int, float)) and snaps > 1:
                timeline_ok += 1
                snapshot_counts.append(int(snaps))

        return {
            "probes": probes,
            "metadata_hits": hits,
            "availability": round(hits / max(1, probes), 3),
            "latencies_s": lat,
            "serving": hits > 0,
            "probs_populated": probs_ok > 0,
            "probs_populated_rate": round(probs_ok / max(1, hits), 3) if hits else 0.0,
            "distribution_sizes": dist_sizes[:3],
            "timeline_populated": timeline_ok > 0,
            "timeline_populated_rate": round(timeline_ok / max(1, hits), 3) if hits else 0.0,
            "snapshots": snapshot_counts,
            "note": (
                "Availability below 1.0 is the production behaviour of "
                "/api/models/transcribe: the whissle-large metadata head is called in "
                "parallel with the external transcription and FAILS OPEN, so a timeout "
                "simply omits the `metadata` key and the caller cannot tell the "
                "difference between 'no metadata' and 'metadata was never meant to be "
                "here'. The ablation retries per case to obtain it, and excludes any "
                "case where it never arrives."
            ),
        }
