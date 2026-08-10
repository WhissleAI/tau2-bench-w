# Copyright Sierra
"""Flow Adherence — turning the analyzer's findings into a publishable metric.

WHAT THIS IS FOR
----------------
``analyze.py`` already audits a running flow against its declared spec and emits
typed findings. That is a bug finder: you read the findings. It is not a
measurement, because "we found 11 things" says nothing without knowing how many
turns it looked at, and it cannot be tracked across releases.

This module is the scorer on top of it — the flow analogue of what WER is to a
list of transcription errors.

THE HEADLINE IS A RATE, NOT A COMPOSITE
---------------------------------------
The obvious design is a weighted score: penalise each finding by severity, sum,
normalise, print a number out of 100. It was rejected deliberately.

Every weight in such a formula is an editorial choice — is a tool leak twice as
bad as a missed transition, or five times? — and the resulting figure moves when
somebody re-tunes a constant, with no way for a reader to tell that from the
product changing. A benchmark whose headline can be improved by editing a weight
table is not a benchmark.

So the headline is **clean-session rate**: the share of scored sessions in which
the analyzer found nothing of high severity. There is no weight to tune. A
reader who disagrees with our severity assignment can recompute it from the
per-type table underneath, which is published in full and which is where the
detail actually lives.

DENSITY IS PER 100 TURNS, NOT PER SESSION
-----------------------------------------
A twenty-turn call has more opportunities to violate a spec than a three-turn
one, so per-session counts reward agents that hang up early — which is a
behaviour this suite elsewhere treats as a failure. Per-100-turns removes that
incentive.

WHAT IT REFUSES TO SCORE
------------------------
Sessions that never ran. Same rule, same reason, and the same bug as the one
that put a billing outage on the public page as an agent failure: a session with
no turns measured no flow. `scored_sessions` is the denominator everywhere and
excluded sessions are reported separately, never averaged in as zeroes.

Pure and I/O-free. The runner owns the network.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from tau2.flow.analyze import DEFAULT_SEVERITY, SEVERITY_RANK

# Finding types that describe the MACHINE diverging from its declared spec.
# These are what "adherence" means, and they are the ones the headline counts.
#
# Deliberately excluded: `coverage` (informational — an unfired transition is an
# untested branch, not a violation) and `infra_fail` (the session did not run at
# all, and is excluded from the denominator rather than counted as a violation).
ADHERENCE_TYPES = frozenset(
    {
        "illegal_transition",
        "expression_integrity",
        "tool_leakage",
        "compliance",
        "variable_desync",
        "guard_violation",
        "dead_end",
        "missed_transition",
        "say_fidelity",
        "stuck_loop",
        "premature_termination",
        "stuck_termination",
        "agent_no_close",
        "turn_cap_exceeded",
    }
)

#: Findings excluded from adherence scoring, with the reason, so a reader can
#: see what was left out rather than having to diff two lists.
NON_ADHERENCE_REASONS = {
    "coverage": "an unfired transition is an untested branch, not a violation of the spec",
    "infra_fail": "the session never ran; it leaves the denominator instead of scoring zero",
}


@dataclass
class AdherenceReport:
    """The scored result for one set of sessions."""

    #: Sessions that produced at least one turn and could therefore be scored.
    scored_sessions: int = 0
    #: Sessions dropped because nothing about the flow was measured.
    excluded_sessions: int = 0
    #: Why they were dropped, by bucket.
    exclusion_breakdown: dict[str, int] = field(default_factory=dict)
    #: Total turns across the scored sessions — the density denominator.
    turns: int = 0

    #: Scored sessions with no high-severity adherence finding.
    clean_sessions: int = 0
    #: Scored sessions with no adherence finding of ANY severity.
    spotless_sessions: int = 0

    #: Every adherence finding, counted by type.
    findings_by_type: dict[str, int] = field(default_factory=dict)
    #: ...and by severity.
    findings_by_severity: dict[str, int] = field(default_factory=dict)

    # ── derived ───────────────────────────────────────────────────────────────

    @property
    def clean_session_rate(self) -> Optional[float]:
        """THE HEADLINE. Percent of scored sessions with no high-severity finding."""
        if not self.scored_sessions:
            return None
        return 100.0 * self.clean_sessions / self.scored_sessions

    @property
    def spotless_session_rate(self) -> Optional[float]:
        """The strict bar: not one finding of any severity."""
        if not self.scored_sessions:
            return None
        return 100.0 * self.spotless_sessions / self.scored_sessions

    @property
    def findings_per_100_turns(self) -> Optional[float]:
        """Density. Per turn, not per session — see the module docstring."""
        if not self.turns:
            return None
        return 100.0 * sum(self.findings_by_type.values()) / self.turns

    def density(self, ftype: str) -> Optional[float]:
        """Per-100-turn density for one finding type."""
        if not self.turns:
            return None
        return 100.0 * self.findings_by_type.get(ftype, 0) / self.turns

    @property
    def exclusion_rate(self) -> Optional[float]:
        total = self.scored_sessions + self.excluded_sessions
        if not total:
            return None
        return 100.0 * self.excluded_sessions / total

    def as_dict(self) -> dict[str, Any]:
        return {
            "scored_sessions": self.scored_sessions,
            "excluded_sessions": self.excluded_sessions,
            "exclusion_breakdown": dict(self.exclusion_breakdown),
            "exclusion_rate_pct": self.exclusion_rate,
            "turns": self.turns,
            "clean_sessions": self.clean_sessions,
            "clean_session_rate_pct": self.clean_session_rate,
            "spotless_sessions": self.spotless_sessions,
            "spotless_session_rate_pct": self.spotless_session_rate,
            "findings_total": sum(self.findings_by_type.values()),
            "findings_per_100_turns": self.findings_per_100_turns,
            "findings_by_type": dict(self.findings_by_type),
            "findings_by_severity": dict(self.findings_by_severity),
            "density_by_type": {
                t: self.density(t) for t in sorted(self.findings_by_type)
            },
        }


def _session_turns(session: dict) -> int:
    md = session.get("metadata") or {}
    n = md.get("num_turns")
    if n is None:
        n = len(session.get("turns") or [])
    return int(n or 0)


def exclusion_bucket(session: dict) -> str:
    """"" if the session is scorable, else the bucket it belongs in.

    Deliberately duplicates the rule in the reporting adapter rather than
    importing it: this module is pure and must not depend on the reporting
    layer, and the rule is three lines. `test_flow_adherence.py` asserts the two
    stay in agreement, which is the part that actually matters.
    """
    if not isinstance(session, dict):
        return ""
    md = session.get("metadata") or {}
    err = str(md.get("setup_error") or "")
    if _session_turns(session) and not md.get("infra_fail"):
        return ""
    low = err.lower()
    if "402" in err or "insufficient credit" in low:
        return "credit_exhausted"
    if "voiceinfraerror" in low or "data channel" in low:
        return "voice_transport"
    if "timeout" in low:
        return "timeout"
    if "502" in err or "503" in err or "provider" in low:
        return "provider_failure"
    if err or md.get("infra_fail"):
        return "infra_fail"
    return "unmeasurable"


def adherence_findings(session: dict) -> list[dict]:
    """The findings that count towards adherence, for one session."""
    out = []
    for f in session.get("analyzer_findings") or []:
        if not isinstance(f, dict):
            continue
        if f.get("type") in ADHERENCE_TYPES:
            out.append(f)
    return out


def score_sessions(sessions: Iterable[dict]) -> AdherenceReport:
    """Score a set of sessions into one :class:`AdherenceReport`."""
    rep = AdherenceReport()
    by_type: Counter = Counter()
    by_sev: Counter = Counter()
    excl: Counter = Counter()

    for s in sessions:
        if not isinstance(s, dict):
            continue
        bucket = exclusion_bucket(s)
        if bucket:
            rep.excluded_sessions += 1
            excl[bucket] += 1
            continue

        rep.scored_sessions += 1
        rep.turns += _session_turns(s)

        found = adherence_findings(s)
        highest = 0
        for f in found:
            ftype = str(f.get("type"))
            sev = str(f.get("severity") or DEFAULT_SEVERITY.get(ftype, "medium"))
            by_type[ftype] += 1
            by_sev[sev] += 1
            highest = max(highest, SEVERITY_RANK.get(sev, 0))

        if highest < SEVERITY_RANK["high"]:
            rep.clean_sessions += 1
        if not found:
            rep.spotless_sessions += 1

    rep.findings_by_type = dict(by_type)
    rep.findings_by_severity = dict(by_sev)
    rep.exclusion_breakdown = dict(excl)
    return rep


@dataclass
class ParityReport:
    """The same scenarios, scored in both modalities.

    THE POINT OF THIS TYPE is that it refuses to compare two runs that were not
    the same experiment. `comparable` is false — and `note` says why — when the
    two arms did not cover the same scenario ids, because a text run over ten
    scenarios and a voice run over the six that survived is not a parity
    measurement, it is two measurements with a suggestive gap between them.

    That failure mode is not hypothetical here: the voice arm is the one that
    loses sessions to transport failures, so the surviving voice scenarios are
    systematically the ones that connected. Comparing those against the full
    text set would flatter voice, which is the direction nobody would catch.
    """

    text: AdherenceReport
    voice: AdherenceReport
    shared_scenarios: int = 0
    text_only: list[str] = field(default_factory=list)
    voice_only: list[str] = field(default_factory=list)

    @property
    def comparable(self) -> bool:
        return not self.text_only and not self.voice_only and self.shared_scenarios > 0

    @property
    def note(self) -> str:
        if self.comparable:
            return (
                f"Both arms cover the same {self.shared_scenarios} scenarios, so the "
                "difference is the modality."
            )
        if not self.shared_scenarios:
            return "The two arms share no scenarios. There is no parity figure to state."
        missing = []
        if self.text_only:
            missing.append(f"{len(self.text_only)} scenario(s) only ran in text")

        if self.voice_only:
            missing.append(f"{len(self.voice_only)} scenario(s) only ran in voice")
        return (
            "NOT a parity measurement: " + " and ".join(missing) + ". The arms did not "
            "cover the same set, and the surviving voice scenarios are systematically "
            "the ones that connected, so a difference here would confound modality "
            "with which sessions happened to run."
        )

    @property
    def clean_rate_gap(self) -> Optional[float]:
        """Text minus voice, in percentage points. None when not comparable."""
        if not self.comparable:
            return None
        t, v = self.text.clean_session_rate, self.voice.clean_session_rate
        if t is None or v is None:
            return None
        return t - v

    def as_dict(self) -> dict[str, Any]:
        return {
            "comparable": self.comparable,
            "note": self.note,
            "shared_scenarios": self.shared_scenarios,
            "text_only": sorted(self.text_only),
            "voice_only": sorted(self.voice_only),
            "clean_rate_gap_pp": self.clean_rate_gap,
            "text": self.text.as_dict(),
            "voice": self.voice.as_dict(),
        }


def _task_id(session: dict) -> str:
    return str(session.get("task_id") or "")


def score_parity(
    text_sessions: Iterable[dict], voice_sessions: Iterable[dict]
) -> ParityReport:
    """Score both arms and establish whether they may be compared at all.

    Scenario membership is decided on the SCORED sessions only. A scenario that
    ran in voice and was excluded for a transport failure is not present in the
    voice arm — it produced no measurement — so it must not silently count as
    shared coverage.
    """
    text = list(text_sessions)
    voice = list(voice_sessions)

    t_ids = {_task_id(s) for s in text if isinstance(s, dict) and not exclusion_bucket(s)}
    v_ids = {_task_id(s) for s in voice if isinstance(s, dict) and not exclusion_bucket(s)}
    t_ids.discard("")
    v_ids.discard("")

    return ParityReport(
        text=score_sessions(text),
        voice=score_sessions(voice),
        shared_scenarios=len(t_ids & v_ids),
        text_only=sorted(t_ids - v_ids),
        voice_only=sorted(v_ids - t_ids),
    )
