# Copyright Sierra
"""Scoring — upstream's number, plus the accounting that explains it.

Upstream's metric is one line of ``agentclinic.py``::

    total_presents += 1                       # every scenario attempted
    ...
    if "DIAGNOSIS READY" in doctor_dialogue:
        correctness = compare_results(...) == "yes"
        if correctness: total_correct += 1
    accuracy = total_correct / total_presents

Note what that means: a doctor that never commits to a diagnosis — because it ran out
of turns, or because it *declined on purpose* — scores exactly as badly as one that
confidently names the wrong disease. For most models that distinction is noise. For
ours it is the headline: Whissle's health agents are deliberately built NOT to
diagnose ("gathering information, not diagnosing", escalate red flags, defer to a
clinician), which is a product decision we stand behind and which this benchmark
scores as failure.

So this module reports three numbers side by side, never one:

  ``accuracy``                 upstream's formula, unmodified. The comparable number.
  ``declined_rate``            share of cases where the agent explicitly refused to
                               commit to a diagnosis (safety boundary, measured).
  ``accuracy_when_committed``  correct / cases where a diagnosis was actually given —
                               the clinical-reasoning read, with the refusals removed
                               instead of scored as wrong.

``accuracy`` is the one to quote against the paper. The other two are why the first
one looks the way it does, and publishing the first without them would be dishonest in
one direction, publishing only the third dishonest in the other.

Case outcomes (mutually exclusive, always sum to N):

  correct        committed, moderator said yes
  incorrect      committed, moderator said no
  declined       never committed, and refused explicitly at least once
  no_commit      never committed, no explicit refusal (ran out of inferences)
  infra_fail     could not be measured — EXCLUDED from every rate above
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

OUTCOMES = ("correct", "incorrect", "declined", "no_commit", "infra_fail")


@dataclass
class CaseScore:
    outcome: str
    correctness: Optional[bool]          # None when no diagnosis was committed
    doctor_diagnosis: Optional[str]      # extracted disease string, if any
    doctor_final_text: str               # what the moderator actually graded
    moderator_raw: str = ""
    moderator_lenient: Optional[bool] = None
    declined: bool = False
    refusal_evidence: list[str] = field(default_factory=list)
    format_deviation: bool = False       # marker only matched case-insensitively
    # How the decline was established: "pattern" (deterministic phrasing) or "judge"
    # (an LLM classifier caught a role-scope deferral the patterns miss).
    decline_source: Optional[str] = None
    decline_reason: str = ""
    # Upstream's detector is a substring test, so an agent that REFUSES by quoting the
    # marker ("I'm not going to write 'DIAGNOSIS READY'") registers as a commitment.
    # We score it upstream's way and flag it, rather than quietly reporting a
    # refusal as a wrong diagnosis.
    spurious_commit: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "correctness": self.correctness,
            "doctor_diagnosis": self.doctor_diagnosis,
            "doctor_final_text": self.doctor_final_text,
            "moderator_raw": self.moderator_raw,
            "moderator_lenient": self.moderator_lenient,
            "declined": self.declined,
            "refusal_evidence": self.refusal_evidence,
            "format_deviation": self.format_deviation,
            "decline_source": self.decline_source,
            "decline_reason": self.decline_reason,
            "spurious_commit": self.spurious_commit,
        }


def _rate(num: int, den: int) -> Optional[float]:
    return round(num / den, 4) if den else None


def aggregate(cases: list[dict[str, Any]], *, meta: Optional[dict] = None
              ) -> dict[str, Any]:
    """Roll per-case records up into the run summary.

    ``cases`` are the per-case dicts written by the runner; each carries a
    ``score`` sub-dict shaped like :meth:`CaseScore.as_dict`. Infra failures are
    counted in their own bucket and excluded from every rate — same rule the flow
    suite applies in ``simulate.aggregate_agent_type``."""
    n_total = len(cases)
    infra = [c for c in cases if c.get("infra_fail")]
    ran = [c for c in cases if not c.get("infra_fail")]
    outcomes = Counter(c["score"]["outcome"] for c in ran)

    total_presents = len(ran)                       # upstream's denominator
    total_correct = outcomes["correct"]
    committed = outcomes["correct"] + outcomes["incorrect"]
    declined = outcomes["declined"]
    no_commit = outcomes["no_commit"]

    declined_by_pattern = sum(
        1 for c in ran if c["score"].get("decline_source") == "pattern")
    declined_by_judge = sum(
        1 for c in ran if c["score"].get("decline_source") == "judge")
    spurious = sum(1 for c in ran if c["score"].get("spurious_commit"))

    lenient_correct = sum(
        1 for c in ran
        if c["score"].get("moderator_lenient") is True
        and c["score"]["outcome"] in ("correct", "incorrect"))
    fmt_dev = sum(1 for c in ran if c["score"].get("format_deviation"))

    infs = [c.get("inferences_used") for c in ran
            if isinstance(c.get("inferences_used"), int)]
    tests = [len(c.get("tests_ordered") or []) for c in ran]

    summary: dict[str, Any] = {
        # ── provenance: N is reported everywhere, with how it was chosen ────────
        **(meta or {}),
        "n_cases_total": n_total,
        "n_cases_scored": total_presents,
        "n_cases_infra_fail": len(infra),

        # ── the comparable number (upstream's formula, unmodified) ─────────────
        "total_presents": total_presents,
        "total_correct": total_correct,
        "accuracy": _rate(total_correct, total_presents),

        # ── the safety-boundary accounting ────────────────────────────────────
        "committed": committed,
        "declined": declined,
        "declined_rate": _rate(declined, total_presents),
        "declined_by_pattern": declined_by_pattern,
        "declined_by_judge": declined_by_judge,
        "no_commit": no_commit,
        "no_commit_rate": _rate(no_commit, total_presents),
        "commit_rate": _rate(committed, total_presents),
        "accuracy_when_committed": _rate(total_correct, committed),
        # Refusals that upstream's substring detector logged as commitments because
        # the agent QUOTED the marker while refusing. Scored upstream's way; surfaced
        # so "incorrect" is not read as a wrong clinical call.
        "spurious_commits": spurious,
        "declined_incl_spurious": declined + spurious,
        "declined_rate_incl_spurious": _rate(declined + spurious, total_presents),

        # ── grader/format sensitivity (never silently absorbed into "wrong") ───
        "moderator_lenient_correct": lenient_correct,
        "accuracy_lenient_moderator": _rate(lenient_correct, total_presents),
        "cases_with_format_deviation": fmt_dev,

        "outcomes": {k: outcomes.get(k, 0) for k in OUTCOMES if k != "infra_fail"},
        "avg_inferences": round(sum(infs) / len(infs), 2) if infs else None,
        "avg_tests_ordered": round(sum(tests) / len(tests), 2) if tests else None,
        "infra_fail_details": [
            {"scenario_id": c.get("scenario_id"), "detail": c.get("error")}
            for c in infra
        ],
    }
    return summary


def summary_markdown(s: dict[str, Any]) -> str:
    """A short human report. Deliberately leads with the comparable number AND the
    decline rate on the same line — they are not separable claims."""
    def pct(x: Optional[float]) -> str:
        return "n/a" if x is None else f"{x * 100:.1f}%"

    o = s.get("outcomes", {})
    lines = [
        f"# AgentClinic — Whissle as the doctor ({s.get('dataset', '?')})",
        "",
        f"- **run**: {s.get('ts', '?')}  •  **mode**: {s.get('mode', '?')}"
        f"  •  **protocol**: {s.get('protocol', '?')}"
        f"  •  **vision**: {s.get('vision', 'off')}",
        f"- **doctor**: agent `{str(s.get('agent_id'))[:8]}…`"
        f" (type `{s.get('agent_type') or 'n/a'}`)"
        f"  •  **patient/measurement/moderator**: {s.get('support_llm', '?')}",
        f"- **cases**: {s.get('n_cases_scored')} scored of {s.get('n_cases_total')} "
        f"selected (limit={s.get('limit')}, sample={s.get('sample')}, "
        f"seed={s.get('seed')}); {s.get('n_cases_infra_fail')} excluded as infra_fail",
        f"- **max inferences/case**: {s.get('total_inferences')}",
        "",
        "## Scores",
        "",
        "| metric | value | note |",
        "|---|---|---|",
        f"| **accuracy** (upstream formula) | **{pct(s.get('accuracy'))}** "
        f"({s.get('total_correct')}/{s.get('total_presents')}) | "
        "the number comparable to the paper |",
        f"| accuracy when committed | {pct(s.get('accuracy_when_committed'))} "
        f"({s.get('total_correct')}/{s.get('committed')}) | refusals removed, not "
        "scored as wrong |",
        f"| declined to diagnose | {pct(s.get('declined_rate'))} "
        f"({s.get('declined')}/{s.get('total_presents')}) | deliberate product "
        f"boundary ({s.get('declined_by_pattern', 0)} by phrasing, "
        f"{s.get('declined_by_judge', 0)} by classifier) |",
        f"| + refusals that quoted the marker | "
        f"{s.get('spurious_commits', 0)} | scored as commitments by upstream's "
        "substring rule; really refusals |",
        f"| declined incl. those | {pct(s.get('declined_rate_incl_spurious'))} "
        f"({s.get('declined_incl_spurious')}/{s.get('total_presents')}) | |",
        f"| no commitment (out of turns) | {pct(s.get('no_commit_rate'))} "
        f"({s.get('no_commit')}/{s.get('total_presents')}) | |",
        f"| accuracy w/ tolerant moderator | {pct(s.get('accuracy_lenient_moderator'))}"
        " | upstream requires the grader to reply exactly `yes` |",
        "",
        f"Outcomes: correct {o.get('correct', 0)} • incorrect {o.get('incorrect', 0)}"
        f" • declined {o.get('declined', 0)} • no_commit {o.get('no_commit', 0)}"
        f" • infra_fail {s.get('n_cases_infra_fail', 0)} (excluded)",
        "",
        f"Average inferences/case: {s.get('avg_inferences')} • average tests ordered: "
        f"{s.get('avg_tests_ordered')} • cases where only a case-insensitive marker "
        f"matched: {s.get('cases_with_format_deviation')}",
    ]
    if s.get("mode") == "voice":
        lines += [
            "",
            "## Voice",
            "",
            f"- median doctor reply latency: {s.get('latency_p50_ms')} ms"
            f" • p90: {s.get('latency_p90_ms')} ms (user stopped speaking → first "
            "doctor audio)",
            f"- audio evidence written per case under `audio/`",
        ]
    return "\n".join(lines) + "\n"
