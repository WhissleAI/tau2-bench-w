# Copyright Sierra
"""The archive record's shape — one versioned schema every suite writes.

WHY THIS EXISTS
---------------
Results lived under ``results/whissle/**`` in per-suite shapes that differ, and the
only curated archive was a one-off. A number you cannot re-open six months later is
not a result, it is a memory. This module defines the single record every suite
emits so that a folder handed to someone who was not in the room is interpretable
without them.

THE FOUR FIELDS A RUN CANNOT OMIT
---------------------------------
Three of these exist because we got them wrong before, once each, in a way that
survived into something published:

``modality``          Two runs were published as "Voice" on the public page having
                      been driven entirely over TEXT. The artifacts were ambiguous
                      about it, so the error was invisible until someone checked the
                      transport by hand. :func:`require_modality` therefore rejects a
                      run that does not say — there is no default, and "unknown" is
                      spelled :data:`MODALITY_NOT_RECORDED` and shouted in the
                      manifest rather than quietly omitted.
``served``            ``/api/bench/agent-turn`` returns the model that actually
                      answered (backend #664). Provider failover means the requested
                      model is a *request*, not evidence; a sweep arm labelled
                      "opus5" whose turns were served by haiku is a mislabelled
                      result, not a slow one.
``metadata_head``     Our cascade's distinguishing layer — the whissle-large
                      metadata head — is OFF in production. Every run to date
                      therefore measured the cascade WITHOUT the thing that makes it
                      ours. That belongs on every manifest, read from the
                      environment (:mod:`tau2.archive.env`), never hand-asserted.
``date`` + provenance A run's date must come from the artifact's own recorded
                      timestamp. File mtime is known-wrong here — ``retail_run1.json``
                      carries an mtime of 4 Aug for a run that happened 31 Jul — so
                      :class:`DateProvenance` records WHERE the date came from and
                      refuses to fall back to mtime.

HONESTY RULE — "NOT RECORDED" IS A VALUE, NOT AN OMISSION
---------------------------------------------------------
Everywhere a fact could not be recovered, the record carries
:data:`NOT_RECORDED` and a reason, never ``null``, ``""``, ``0``, or a plausible
guess. This mirrors ``tau2.health.diagnostics``'s absence-is-not-zero rule: a reader
who sees ``"not recorded"`` knows the fact is missing; a reader who sees ``null``
cannot tell missing from measured-as-nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

#: Bump the minor when a field is ADDED (readers of v1 keep working); bump the major
#: when a field changes meaning or disappears (readers must be updated). Follows the
#: precedent set by ``tau2.health.diagnostics/v1``.
SCHEMA = "whissle.benchmark.archive/v1"

#: The canonical spelling of an unrecoverable fact. Never substitute ``None``, an
#: empty string, or a guess — a reader must be able to tell "we did not record this"
#: from "this was measured and the answer was nothing".
NOT_RECORDED = "not recorded"


# ── modality ───────────────────────────────────────────────────────────────────

#: A benchmark driven over HTTP text turns. The overwhelming majority of runs.
MODALITY_TEXT = "text"
#: A benchmark driven over a real audio transport (LiveKit room, telephony).
MODALITY_VOICE = "voice"
#: Text turns plus image inputs (AgentClinic's vision arm).
MODALITY_TEXT_VISION = "text+vision"
#: Audio transport plus image inputs.
MODALITY_VOICE_VISION = "voice+vision"
#: Backfill only, and only where the transport genuinely cannot be recovered from the
#: artifacts. A LIVE run may never carry this — see :func:`require_modality`.
MODALITY_NOT_RECORDED = NOT_RECORDED

MODALITIES = (
    MODALITY_TEXT,
    MODALITY_VOICE,
    MODALITY_TEXT_VISION,
    MODALITY_VOICE_VISION,
)

#: What each modality means, verbatim, in the manifest. A one-word field is only
#: self-describing if the word is defined next to it.
MODALITY_MEANING: dict[str, str] = {
    MODALITY_TEXT: (
        "driven entirely over TEXT — HTTP turns, no audio was synthesised, "
        "transmitted, or recognised at any point. This run says nothing about voice "
        "performance and must not be presented as a voice result."
    ),
    MODALITY_VOICE: (
        "driven over a real audio transport — speech was synthesised, sent over the "
        "wire, and recognised back. Voice-pipeline signals exist for this run."
    ),
    MODALITY_TEXT_VISION: (
        "text turns plus image inputs — no audio at any point, but the agent was "
        "shown images. Not a voice result."
    ),
    MODALITY_VOICE_VISION: (
        "audio transport plus image inputs — both the voice pipeline and the vision "
        "path were exercised."
    ),
    MODALITY_NOT_RECORDED: (
        "NOT RECORDED — the archived artifacts do not state which transport drove "
        "this run, and it was not recoverable. Do not assume voice; do not assume "
        "text. Treat any modality-dependent reading of this run as unsupported."
    ),
}


class ArchiveError(ValueError):
    """A run that cannot be archived honestly. Raised rather than papered over —
    an archive that silently invents a missing field is worse than no archive."""


def require_modality(modality: Optional[str], *, backfill: bool = False) -> str:
    """Validate the one field a run may never omit.

    ``backfill=True`` is the only path that accepts :data:`MODALITY_NOT_RECORDED`,
    and only for historical runs whose transport is genuinely unrecoverable. A live
    suite calling the writer must state the transport it just drove — it is the one
    caller that cannot possibly not know.
    """
    if modality is None or str(modality).strip() == "":
        raise ArchiveError(
            "modality is required and has no default: state how this run was driven "
            f"({', '.join(MODALITIES)}). Two runs were published as 'Voice' having "
            "been driven over text because a field like this was left implicit."
        )
    value = str(modality).strip().lower()
    if value in MODALITIES:
        return value
    if value == MODALITY_NOT_RECORDED:
        if backfill:
            return MODALITY_NOT_RECORDED
        raise ArchiveError(
            f"modality={MODALITY_NOT_RECORDED!r} is accepted only when backfilling a "
            "historical run. A live run knows the transport it just drove."
        )
    raise ArchiveError(
        f"unknown modality {modality!r} — expected one of {MODALITIES} "
        f"(or {MODALITY_NOT_RECORDED!r} when backfilling)"
    )


def is_voice(modality: str) -> bool:
    """Did audio actually move? The question the public page got wrong."""
    return modality in (MODALITY_VOICE, MODALITY_VOICE_VISION)


# ── date provenance ────────────────────────────────────────────────────────────

#: A timestamp read out of the artifact's own contents — the only fully trustworthy
#: source, because the run wrote it.
DATE_FROM_ARTIFACT = "artifact"
#: A timestamp parsed from the run directory's name (``20260808T193252Z-arm``).
#: Trustworthy: the runner stamped it at start.
DATE_FROM_DIRNAME = "dirname"
#: The clock at archive time. Correct for a live run, meaningless for a backfill.
DATE_FROM_NOW = "archive-time clock"
#: No date anywhere. NEVER substitute mtime — see :data:`MTIME_IS_NOT_A_DATE`.
DATE_UNRECOVERABLE = NOT_RECORDED

MTIME_IS_NOT_A_DATE = (
    "File mtime is NOT used as a date source anywhere in this archive. It is known "
    "wrong in this results tree: results/whissle/flow_sim/retail_run1.json carries an "
    "mtime of 4 Aug for a run its own contents date to 31 Jul — a copy, a checkout or "
    "a re-write moved the mtime and left the run date behind. Where no timestamp "
    "exists inside the artifact or in its directory name, the date is recorded as "
    f"{NOT_RECORDED!r} rather than inferred."
)


@dataclass
class DateProvenance:
    """When the run happened, and how we know.

    The ``source`` field is the point: a date is only as good as its provenance, and
    a reader comparing two runs needs to know whether they are comparing two recorded
    timestamps or one timestamp and one guess.
    """

    date: str = NOT_RECORDED
    #: Full timestamp where available (ISO 8601); the date alone otherwise.
    timestamp: str = NOT_RECORDED
    source: str = DATE_UNRECOVERABLE
    #: Exactly where it was read from — a file path and key, or the directory name.
    source_detail: str = NOT_RECORDED

    @property
    def recovered(self) -> bool:
        return self.source != DATE_UNRECOVERABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "timestamp": self.timestamp,
            "source": self.source,
            "source_detail": self.source_detail,
            "recovered": self.recovered,
            "mtime_note": MTIME_IS_NOT_A_DATE,
        }


# ── run-directory naming ───────────────────────────────────────────────────────

#: ``<YYYYMMDD_HHMMSS>_<arm>`` — sorts chronologically as plain text, which is what
#: makes a directory listing a timeline.
DIR_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"

_ARM_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_arm(arm: Optional[str]) -> str:
    """A filesystem-safe arm label. Empty/unknown arms become ``unlabelled`` rather
    than an empty path segment, so a directory name is never ambiguous."""
    cleaned = _ARM_SAFE.sub("-", str(arm or "").strip()).strip("-._")
    return cleaned or "unlabelled"


# ── the sections a manifest carries ────────────────────────────────────────────


@dataclass
class ServedRecord:
    """What ACTUALLY served the run, as distinct from what was asked for.

    ``/api/bench/agent-turn`` returns ``model``, ``usage`` and ``stop_details``
    (backend #664). Provider failover means a run requesting one model can be served
    by another, silently, for some or all of its turns — so ``models`` is a COUNT per
    model, not a single value: a run served 80/20 by two models is a fact about that
    run, and collapsing it to "the model" would erase it.
    """

    #: served model id -> number of turns it answered
    models: dict[str, int] = field(default_factory=dict)
    #: stop_reason -> count. A run full of ``max_tokens`` is truncated, not incorrect.
    stop_reasons: dict[str, int] = field(default_factory=dict)
    #: refusal categories off ``stop_details`` (populated only on refusals)
    stop_details: list[dict[str, Any]] = field(default_factory=list)
    turns: int = 0
    #: where this came from — a live ledger, or the field on an archived artifact
    source: str = NOT_RECORDED

    @property
    def available(self) -> bool:
        return bool(self.models)

    @property
    def distinct(self) -> list[str]:
        return sorted(self.models)

    @property
    def dominant(self) -> str:
        if not self.models:
            return NOT_RECORDED
        return max(self.models.items(), key=lambda kv: (kv[1], kv[0]))[0]

    @property
    def failover_observed(self) -> bool:
        """More than one model answered — the requested model was not the whole story."""
        return len(self.models) > 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": None if self.available else (
                "no per-turn served-model record exists for this run — it predates "
                "backend #664 returning `model`/`usage`/`stop_details`, or the "
                "harness did not capture the field"
            ),
            "models": dict(self.models) if self.available else None,
            "distinct": self.distinct if self.available else None,
            "dominant": self.dominant,
            "failover_observed": self.failover_observed if self.available else None,
            "stop_reasons": dict(self.stop_reasons) if self.available else None,
            "stop_details": self.stop_details or None,
            "turns": self.turns if self.available else None,
            "source": self.source,
        }


@dataclass
class RequestedRecord:
    """What the run ASKED for. Kept beside :class:`ServedRecord` precisely so the two
    can disagree visibly."""

    model: str = NOT_RECORDED
    provider: str = NOT_RECORDED
    effort: str = NOT_RECORDED
    thinking: str = NOT_RECORDED
    speed: str = NOT_RECORDED
    judge_model: str = NOT_RECORDED
    judge_provider: str = NOT_RECORDED
    judge_independent: Optional[bool] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "effort": self.effort,
            "thinking": self.thinking,
            "speed": self.speed,
            "judge_model": self.judge_model,
            "judge_provider": self.judge_provider,
            "judge_independent": self.judge_independent,
            **self.extra,
        }
