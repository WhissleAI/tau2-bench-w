"""Aggregation and artifacts, in the shape MedAgentBench's paper reports.

The paper reports, over the 300-task set:
  * overall success rate,
  * a Query / Action split (read-only categories vs. write-capable ones),
  * per-category success rate for task1..task10.

We emit exactly those, plus the two things a published table cannot carry:

  * `n` everywhere — a `--limit` run is a first-class operation here, and a
    success rate without its denominator is not a number anyone can use.
  * an `infra` bucket. Sessions that never ran (brain or EHR unreachable) are
    classified `infra_fail` and excluded from every rate, following the
    taxonomy in `tau2.flow.analyze` — a network outage is not a wrong clinical
    answer. They are reported, never silently dropped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from tau2.flow.analyze import DEFAULT_SEVERITY, Finding
from tau2.health.diagnostics import attach as diag_attach
from tau2.health.medagent.data import (
    ACTION_CATEGORIES,
    ALL_CATEGORIES,
    PUBLISHED_BASELINES,
    QUERY_CATEGORIES,
)
from tau2.health.medagent.episode import Episode
from tau2.health.medagent.grader import GradeResult
from tau2.health.medagent.integrity import IntegrityReport, aggregate as agg_integrity

RESULTS_ROOT = Path("results/whissle/medagentbench")

# `infra_fail` is already in the shared taxonomy; assert rather than redefine so
# this suite cannot drift from the flow suites' meaning of the word.
assert DEFAULT_SEVERITY.get("infra_fail") == "high"


@dataclass
class TaskResult:
    """One graded task."""

    episode: Episode
    grade: Optional[GradeResult]
    integrity: IntegrityReport

    @property
    def task_id(self) -> str:
        return self.episode.case.id

    @property
    def category(self) -> str:
        return self.episode.case.category

    @property
    def infra_fail(self) -> bool:
        return self.episode.infra_fail

    @property
    def correct(self) -> bool:
        return bool(self.grade and self.grade.correct)

    def findings(self) -> list[Finding]:
        """Suite findings, in the shared `analyze.py` vocabulary."""
        out: list[Finding] = []
        if self.infra_fail:
            out.append(
                Finding(
                    "infra_fail",
                    "high",
                    "infrastructure failure — the episode could not be measured: "
                    f"{self.episode.infra_reason}",
                    evidence={
                        "task_id": self.task_id,
                        "attempt": self.episode.attempt,
                        "rounds": len(self.episode.turns),
                    },
                )
            )
            return out
        if self.integrity.said_not_emitted:
            out.append(
                Finding(
                    "compliance",
                    "high",
                    "the agent stated a chart action was carried out but never "
                    "issued the write",
                    evidence={
                        "task_id": self.task_id,
                        "claim": self.integrity.said_evidence,
                    },
                )
            )
        if self.integrity.emitted_not_accepted:
            out.append(
                Finding(
                    "expression_integrity",
                    "high",
                    "the agent emitted a write the real FHIR server rejected",
                    evidence={
                        "task_id": self.task_id,
                        "issues": [
                            i
                            for a in self.integrity.write_attempts
                            for i in a.issues
                        ][:5],
                    },
                )
            )
        if self.episode.status == "agent_invalid_action":
            out.append(
                Finding(
                    "say_fidelity",
                    "medium",
                    "the agent's reply matched none of GET / POST / FINISH",
                    evidence={"task_id": self.task_id},
                )
            )
        if self.episode.status == "task_limit_reached":
            out.append(
                Finding(
                    "turn_cap_exceeded",
                    "medium",
                    "the agent never called FINISH within the round budget",
                    evidence={"task_id": self.task_id},
                )
            )
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.episode.as_dict(),
            "grade": self.grade.as_dict() if self.grade else None,
            "correct": self.correct,
            "integrity": self.integrity.as_dict(),
            "findings": [f.as_dict() for f in self.findings()],
        }


def _rate(correct: int, n: int) -> Optional[float]:
    return round(100.0 * correct / n, 2) if n else None


def _bucket(results: list[TaskResult]) -> dict[str, Any]:
    n = len(results)
    correct = sum(1 for r in results if r.correct)
    return {"n": n, "correct": correct, "success_rate_pct": _rate(correct, n)}


def summarize(
    results: list[TaskResult],
    *,
    mode: str,
    run_meta: dict[str, Any],
) -> dict[str, Any]:
    """Build the report. Scores are computed over measured episodes only."""
    scored = [r for r in results if not r.infra_fail]
    infra = [r for r in results if r.infra_fail]

    per_category = {}
    for cat in ALL_CATEGORIES:
        rows = [r for r in scored if r.category == cat]
        if rows:
            per_category[cat] = _bucket(rows)

    query = [r for r in scored if r.category in QUERY_CATEGORIES]
    action = [r for r in scored if r.category in ACTION_CATEGORIES]

    status_counts: dict[str, int] = {}
    for r in results:
        status_counts[r.episode.status] = status_counts.get(r.episode.status, 0) + 1

    findings = [f for r in results for f in r.findings()]
    finding_counts: dict[str, int] = {}
    for f in findings:
        finding_counts[f.type] = finding_counts.get(f.type, 0) + 1

    return {
        "suite": "medagentbench",
        "mode": mode,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run": run_meta,
        "n_tasks_attempted": len(results),
        "n_scored": len(scored),
        "n_infra_fail": len(infra),
        "infra_fail_task_ids": [r.task_id for r in infra],
        # The paper's headline shape.
        "overall": _bucket(scored),
        "query": _bucket(query),
        "action": _bucket(action),
        "per_category": per_category,
        # Ours.
        "write_integrity": agg_integrity([r.integrity for r in results if not r.infra_fail]),
        "status_counts": status_counts,
        "finding_counts": finding_counts,
        "published_baselines_full_300": PUBLISHED_BASELINES,
        "comparability_note": (
            "Comparable to the published table only when n_scored == 300 and "
            "mode == 'brain-parity'. A --limit run is a subset estimate; always "
            "quote N."
        ),
    }


def write_artifacts(
    results: list[TaskResult],
    summary: dict[str, Any],
    *,
    root: Path = RESULTS_ROOT,
    run_name: Optional[str] = None,
) -> Path:
    """Write per-task artifacts + the summary. Returns the run directory.

    Each task record also carries the shared ``diagnostics`` envelope
    (``tau2.health.diagnostics``) so one reader works across all three health
    benchmarks: tool forensics with resolved arguments and the said-vs-emitted-vs-
    landed write verdict, per-case provenance, and explicit unavailability markers
    for the flow trace and voice signals this transport does not produce."""
    from tau2.health.medagent import diagnostics as case_diag

    stamp = run_name or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = root / f"{summary['mode']}_{stamp}"
    tasks_dir = run_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    run_meta = summary.get("run") or {}
    for r in results:
        record = r.as_dict()
        case_diag_envelope = case_diag.build(
            record, run_meta=run_meta, run_dir=str(run_dir))
        (tasks_dir / f"{r.task_id}.json").write_text(
            json.dumps(diag_attach(record, case_diag_envelope),
                       indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    (run_dir / "SUMMARY.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "SUMMARY.md").write_text(render_markdown(summary), encoding="utf-8")
    return run_dir


def render_markdown(s: dict[str, Any]) -> str:
    """Human-readable summary, paper table first."""
    L: list[str] = []
    run = s.get("run", {})
    L.append(f"# MedAgentBench — Whissle ({s['mode']})")
    L.append("")
    L.append(f"_{s['generated_at']}_")
    L.append("")
    L.append(
        f"**N attempted {s['n_tasks_attempted']} · scored {s['n_scored']} · "
        f"infra_fail {s['n_infra_fail']} (excluded)**"
    )
    L.append("")

    if run:
        L.append("| run | |")
        L.append("|---|---|")
        for k, v in run.items():
            L.append(f"| {k} | `{v}` |")
        L.append("")

    L.append("## Success rate")
    L.append("")
    L.append("| slice | n | correct | SR % |")
    L.append("|---|---:|---:|---:|")
    for label, key in (("Overall", "overall"), ("Query", "query"), ("Action", "action")):
        b = s[key]
        L.append(
            f"| {label} | {b['n']} | {b['correct']} | "
            f"{b['success_rate_pct'] if b['success_rate_pct'] is not None else '—'} |"
        )
    L.append("")

    L.append("### Per category")
    L.append("")
    L.append("| category | kind | n | correct | SR % |")
    L.append("|---|---|---:|---:|---:|")
    for cat, b in s["per_category"].items():
        kind = "action" if cat in ACTION_CATEGORIES else "query"
        L.append(
            f"| {cat} | {kind} | {b['n']} | {b['correct']} | "
            f"{b['success_rate_pct'] if b['success_rate_pct'] is not None else '—'} |"
        )
    L.append("")

    wi = s.get("write_integrity") or {}
    if wi:
        L.append("## Write integrity — said vs. actually wrote")
        L.append("")
        L.append(
            "> MedAgentBench never sends the agent's POST to the EHR: it replies "
            '"POST request accepted and executed successfully" and grades the '
            "payload out of the transcript. The published Action SR therefore "
            "measures the *intent* to write. These rows separate the three events."
        )
        L.append("")
        L.append(f"- write-check mode: `{wi.get('write_check_mode')}`")
        L.append(f"- action episodes: {wi.get('n_action_episodes')}")
        L.append(
            f"- claimed an action: {wi.get('episodes_that_claimed_an_action')} · "
            f"emitted a write: {wi.get('episodes_that_emitted_a_write')}"
        )
        L.append("")
        L.append("| signal | n | rate % | tasks |")
        L.append("|---|---:|---:|---|")
        for key, label in (
            ("said_but_did_not_write", "said it ordered, never wrote"),
            ("wrote_but_did_not_say", "wrote, never said"),
            ("emitted_but_ehr_rejected", "wrote a payload the EHR refused"),
            (
                "emitted_nonconformant_fhir",
                "wrote a payload that fails strict FHIR R4 validation",
            ),
        ):
            row = wi.get(key) or {}
            ids = ", ".join(row.get("task_ids") or []) or "—"
            L.append(
                f"| {label} | {row.get('n', 0)} | "
                f"{row.get('rate_pct') if row.get('rate_pct') is not None else '—'} | {ids} |"
            )
        L.append("")
        L.append(
            f"- writes emitted {wi.get('total_writes_emitted')} · "
            f"accepted by EHR {wi.get('total_writes_accepted_by_ehr')} · "
            f"verified in chart {wi.get('total_writes_verified_in_chart')} · "
            f"non-conformant {wi.get('total_writes_nonconformant')}"
        )
        L.append("")
        L.append(
            "> 'Refused' and 'fails validation' are different questions and they "
            "do disagree: HAPI's create endpoint is more lenient than its "
            "`$validate` operation, so a payload can be stored yet still be "
            "invalid FHIR R4."
        )
        L.append("")

    if s.get("finding_counts"):
        L.append("## Findings")
        L.append("")
        L.append("| type | n |")
        L.append("|---|---:|")
        for k, v in sorted(s["finding_counts"].items(), key=lambda kv: -kv[1]):
            L.append(f"| `{k}` | {v} |")
        L.append("")

    L.append("## Published baselines (full 300-task set)")
    L.append("")
    L.append("| model | overall | query | action |")
    L.append("|---|---:|---:|---:|")
    for name, b in s["published_baselines_full_300"].items():
        L.append(
            f"| {name} | {b.get('overall', '—')} | {b.get('query', '—')} | "
            f"{b.get('action', '—')} |"
        )
    L.append("")
    L.append(f"> {s['comparability_note']}")
    L.append("")
    return "\n".join(L)
