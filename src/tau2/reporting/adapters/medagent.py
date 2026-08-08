"""MedAgentBench → RunReport.

Run dir shape: ``SUMMARY.json`` + ``tasks/<task_id>.json``.

This is the one benchmark in the set with a real published leaderboard carried in
the harness, so it is the one that exercises the baseline-comparison path — and
the one where the comparability caveat has to be stated loudest, because a
``--limit`` run is a subset estimate of a 300-task published number.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..model import (
    Baseline,
    BaselineSet,
    Exclusions,
    FailureCategory,
    FailureExample,
    Judge,
    Limitation,
    Metric,
    Provenance,
    Reproduction,
    RunReport,
    Sampling,
    Table,
)
from .base import BuildContext, artifacts_for, dig, read_json, read_json_dir, wilson_ci


def _pct(v, nd: int = 1) -> str:
    """A percentage cell that survives a null. A summary written by an older
    harness carries `None` where a newer one carries a number, and a report
    generator must render the gap, not raise on it."""
    return f"{float(v):.{nd}f}%" if isinstance(v, (int, float)) else "—"

PUBLISHED_N = 300


class MedAgentBenchAdapter:
    benchmark = "medagentbench"
    benchmark_title = "MedAgentBench"

    @classmethod
    def detect(cls, run_dir: Path) -> bool:
        try:
            return (run_dir / "tasks").is_dir() or (
                (run_dir / "SUMMARY.json").is_file()
                and "medagentbench" in str(run_dir).lower()
            )
        except Exception:
            return False

    @classmethod
    def build(cls, run_dir: Path, ctx: BuildContext) -> RunReport:
        s = read_json(run_dir / "SUMMARY.json") or {}
        tasks, unreadable = read_json_dir(run_dir / "tasks")

        status = "complete"
        partial_reason = ""
        warnings: list[str] = []
        if not s:
            status = "partial"
            partial_reason = (
                "SUMMARY.json is missing or unreadable; figures were recomputed from "
                "the per-task files"
            )
        if unreadable:
            status = "partial"
            warnings.append(f"{len(unreadable)} task file(s) unreadable: {unreadable[:5]}")

        n_attempted = s.get("n_tasks_attempted") or len(tasks)
        n_scored = s.get("n_scored")
        if n_scored is None:
            n_scored = sum(1 for t in tasks if isinstance(t, dict) and not t.get("infra_fail"))
        n_infra = s.get("n_infra_fail")
        if n_infra is None:
            n_infra = sum(1 for t in tasks if isinstance(t, dict) and t.get("infra_fail"))

        overall_pct = dig(s, "overall", "success_rate_pct")
        n_correct = dig(s, "overall", "correct")
        if overall_pct is None and tasks:
            n_correct = sum(1 for t in tasks if isinstance(t, dict) and t.get("correct"))
            overall_pct = round(100.0 * n_correct / max(1, n_scored), 1)
            warnings.append("success rate recomputed from task files (summary absent)")

        exclusions = Exclusions(
            n_total=int(n_attempted or 0),
            n_scored=int(n_scored or 0),
            n_excluded=int(n_infra or 0),
            breakdown={"infra_fail": int(n_infra)} if n_infra else {},
            reason_examples=sorted(
                {
                    str(t.get("infra_reason"))
                    for t in tasks
                    if isinstance(t, dict) and t.get("infra_fail") and t.get("infra_reason")
                }
            ),
            excluded_ids=list(s.get("infra_fail_task_ids") or []),
        )

        headline = Metric(
            key="success_rate",
            label="Overall success rate",
            value=overall_pct,
            unit="pct",
            ci=wilson_ci(int(n_correct or 0), int(n_scored or 0)),
            n=int(n_scored or 0),
            floor=0.0,
            ceiling=100.0,
            note="deterministically graded against live FHIR chart state",
        )

        secondary: list[Metric] = []
        split_rows = []
        for split in ("query", "action"):
            b = s.get(split) or {}
            if not b:
                continue
            secondary.append(
                Metric(
                    key=f"{split}_success_rate",
                    label=f"{split.title()} success rate",
                    value=b.get("success_rate_pct"),
                    unit="pct",
                    ci=wilson_ci(int(b.get("correct") or 0), int(b.get("n") or 0)),
                    n=b.get("n"),
                    floor=0.0,
                    ceiling=100.0,
                )
            )
            ci = wilson_ci(int(b.get("correct") or 0), int(b.get("n") or 0))
            split_rows.append(
                [
                    split.title(),
                    str(b.get("n", "—")),
                    str(b.get("correct", "—")),
                    _pct(b.get("success_rate_pct")),
                    f"[{ci[0]:.1f}%, {ci[1]:.1f}%]" if ci else "—",
                ]
            )

        tables: list[Table] = []
        if split_rows:
            tables.append(
                Table(
                    key="splits",
                    title="Query vs Action",
                    columns=["Split", "N", "Correct", "Success rate", "95% CI (Wilson)"],
                    rows=split_rows,
                    note=(
                        "Query tasks read the chart; Action tasks must write to it. The "
                        "gap between them is the finding, not the average of them."
                    ),
                    allow_context=True,
                )
            )

        per_cat = s.get("per_category") or {}
        if per_cat:
            tables.append(
                Table(
                    key="per_category",
                    title="Per task category",
                    columns=["Category", "N", "Correct", "Success rate"],
                    rows=[
                        [
                            k,
                            str(v.get("n", "—")),
                            str(v.get("correct", "—")),
                            _pct(v.get("success_rate_pct")),
                        ]
                        for k, v in sorted(
                            per_cat.items(), key=lambda kv: int(str(kv[0])[4:] or 0)
                        )
                        if isinstance(v, dict)
                    ],
                    allow_context=True,
                )
            )

        wi = s.get("write_integrity") or {}
        if wi:
            tables.append(
                Table(
                    key="write_integrity",
                    title="Write integrity — said vs emitted vs landed",
                    columns=["Measure", "Value", "Reading"],
                    rows=[
                        [
                            "Episodes that claimed an action",
                            str(wi.get("episodes_that_claimed_an_action", "—")),
                            "the agent told the user it had done something",
                        ],
                        [
                            "Episodes that emitted a write",
                            str(wi.get("episodes_that_emitted_a_write", "—")),
                            "a POST actually left the harness",
                        ],
                        [
                            "Writes accepted by the EHR",
                            str(wi.get("total_writes_accepted_by_ehr", "—")),
                            "the FHIR server took it",
                        ],
                        [
                            "Writes verified back in the chart",
                            str(wi.get("total_writes_verified_in_chart", "—")),
                            "read back and found — the only proof it landed",
                        ],
                        [
                            "Said but did not write",
                            f"{dig(wi, 'said_but_did_not_write', 'n', default='—')} "
                            f"({dig(wi, 'said_but_did_not_write', 'rate_pct', default=0)}%)",
                            "**the safety-critical count** — claiming an action that "
                            "never happened",
                        ],
                        [
                            "Wrote but did not say",
                            f"{dig(wi, 'wrote_but_did_not_say', 'n', default='—')} "
                            f"({dig(wi, 'wrote_but_did_not_say', 'rate_pct', default=0)}%)",
                            "silent side effect — the chart changed and the user was "
                            "not told",
                        ],
                        [
                            "Emitted but non-conformant FHIR",
                            f"{dig(wi, 'emitted_nonconformant_fhir', 'n', default='—')} "
                            f"({dig(wi, 'emitted_nonconformant_fhir', 'rate_pct', default=0)}%)",
                            "accepted by a permissive server; would fail a strict one",
                        ],
                    ],
                    note=(
                        "Write mode was "
                        f"`{wi.get('write_check_mode', 'unknown')}` — writes were really "
                        "executed against the FHIR sandbox and read back, not simulated."
                    ),
                    allow_context=True,
                )
            )

        run = s.get("run") or {}
        task_prov = _first_diag_provenance(tasks)
        provenance = Provenance(
            agent_id=run.get("agent_id") or task_prov.get("agent_id"),
            base_url=run.get("base") or task_prov.get("base_url"),
            endpoint=run.get("endpoint") or task_prov.get("transport_endpoint"),
            mode=s.get("mode") or "brain-parity",
            harness_commit=task_prov.get("harness_commit"),
            repo_commit=ctx.repo_commit(),
            run_dir=str(run_dir),
            captured_at=s.get("generated_at") or task_prov.get("captured_at"),
            dataset="MedAgentBench (FHIR R4 sandbox)",
            dataset_size=PUBLISHED_N,
            upstream="MedAgentBench, NEJM AI 2025",
            extra={
                "fhir_api_base": run.get("fhir_api_base"),
                "write_check": run.get("write_check"),
                "max_round": run.get("max_round"),
                "grader": run.get("grader"),
                "system_mode": run.get("system_mode"),
            },
        )

        limit = dig(run, "filters", "limit")
        sampling = Sampling(
            method="head-of-set subset" if limit else "full published task set",
            n_population=PUBLISHED_N,
            n_requested=limit,
            n_selected=int(n_attempted or 0),
            seed=None,
            strata_keys=["category"],
            note=(
                "The subset is the leading N tasks of the published set, balanced "
                "10-per-category by construction. It is not a random draw, so it "
                "reproduces exactly — and it inherits whatever ordering bias the "
                "published set has."
            ),
        )

        baselines = _baselines(int(n_scored or 0), s)
        judge = Judge(
            kind="deterministic",
            provider=None,
            model=None,
            endpoint=None,
            independent=None,  # not "False" — no judge model is involved at all
            note=(
                "Grading is deterministic: an expected value recomputed from live chart "
                "state, compared to the agent's answer. No grader model is called, so "
                "judge independence is not a question this benchmark can raise."
            ),
        )

        report = RunReport(
            run_id=ctx.run_id(run_dir),
            benchmark=cls.benchmark,
            benchmark_title=cls.benchmark_title,
            title=f"{cls.benchmark_title} — Whissle ({s.get('mode', 'brain-parity')})",
            mode=str(s.get("mode") or "brain-parity"),
            series_key=f"medagentbench:{s.get('mode', 'brain-parity')}",
            date=(provenance.captured_at or "")[:10] or None,
            status=status,
            partial_reason=partial_reason,
            headline=headline,
            secondary_metrics=secondary,
            what_measured=(
                "Whether an agent can operate a real electronic health record over FHIR: "
                "read the right resource for a clinical question (Query), and write a "
                "correct, conformant resource back when the task calls for it (Action). "
                "Grading is deterministic against live chart state — no rubric, no "
                "grader model, no partial credit."
            ),
            why_measured=(
                "A health assistant that can talk but cannot correctly read and write "
                "the chart is a demo. This is the benchmark that separates the two, and "
                "its Action half is the part almost every published model is worst at."
            ),
            methodology=[
                ("Agent under test", "the deployed Whissle agent brain, unmodified"),
                (
                    "Mode",
                    f"`{s.get('mode')}` — the benchmark's own prompt and protocol; the "
                    "agent supplies reasoning only",
                ),
                ("Endpoint", f"`{provenance.endpoint}` (stateless brain call)"),
                (
                    "Prompt handling",
                    f"system mode `{run.get('system_mode', 'neutral')}`: the deployed "
                    "persona is suppressed so the benchmark's instructions are the only "
                    "instructions, which is what makes the number comparable",
                ),
                (
                    "Turn limit",
                    f"{run.get('max_round', '—')} rounds per task; a task that has not "
                    "emitted FINISH by then is scored incorrect, not retried",
                ),
                (
                    "Tools bound",
                    "none in the agent's own runtime — the protocol is textual "
                    "`GET`/`POST`/`FINISH` strings that the harness parses and executes "
                    "against the FHIR sandbox",
                ),
                (
                    "Write checking",
                    f"`{run.get('write_check', 'execute')}` — POSTs are really executed "
                    "against the sandbox and read back from the chart",
                ),
                (
                    "Scoring rule",
                    f"`{run.get('grader', 'builtin')}` deterministic grader; correct / "
                    "attempted, infra failures excluded from the denominator",
                ),
            ],
            scoring_rule=(
                "success rate = correct / scored; a task is correct only when the "
                "deterministic grader matches the expected value recomputed from chart "
                "state at grading time"
            ),
            tables=tables,
            exclusions=exclusions,
            judge=judge,
            provenance=provenance,
            sampling=sampling,
            baselines=baselines,
            failures=_failures(tasks, s),
            limitations=_limitations(s, sampling, int(n_scored or 0)),
            reproduction=Reproduction(
                commands=[
                    "uv sync --extra dev",
                    "docker run -p 8090:8080 <fhir-sandbox-image>   # MedAgentBench FHIR server",
                    (
                        "python -m tau2.health.medagent.run "
                        f"--mode {s.get('mode', 'brain-parity')} "
                        f"--limit {limit or PUBLISHED_N} --write-check execute"
                    ),
                    (
                        "python -m tau2.reporting.cli build "
                        f"results/whissle/medagentbench/{run_dir.name}"
                    ),
                ],
                environment={
                    "WHISSLE_BASE": provenance.base_url or "",
                    "FHIR_API_BASE": str(run.get("fhir_api_base") or ""),
                    "harness commit": provenance.harness_commit or "unknown",
                    "repo commit at report time": provenance.repo_commit or "unknown",
                },
                notes=[
                    "The subset is the head of the published set — deterministic, no "
                    "seed needed.",
                    "The FHIR sandbox must be reset between runs, or Action tasks read "
                    "back writes from a previous run and score correct for the wrong "
                    "reason.",
                ],
            ),
            artifacts=artifacts_for(
                run_dir,
                [
                    ("SUMMARY.json", "run-level aggregation, write-integrity ledger"),
                    ("SUMMARY.md", "the adapter's own short summary"),
                    ("tasks/", f"{len(tasks)} per-task records with `diagnostics`"),
                    ("REPORT.md", "this report"),
                    ("report.json", "machine-readable form of this report"),
                ],
            ),
            licence_note="MedAgentBench, NEJM AI 2025. Research measurement only.",
            warnings=warnings,
        )
        return report


def _first_diag_provenance(tasks: list[Any]) -> dict[str, Any]:
    for t in tasks:
        p = dig(t, "diagnostics", "provenance", default=None)
        if isinstance(p, dict):
            return p
    return {}


def _baselines(n_scored: int, s: dict) -> BaselineSet:
    pub = s.get("published_baselines_full_300") or {}
    if not pub:
        return BaselineSet(
            comparability_note=(
                "No published baseline table is registered for this run; nothing is "
                "compared."
            )
        )
    comparable = n_scored == PUBLISHED_N and (s.get("mode") == "brain-parity")
    if comparable:
        note = (
            f"**Comparable.** This run scored all {PUBLISHED_N} published tasks under "
            "the published protocol, so the numbers sit in the same column as the "
            "leaderboard."
        )
    else:
        reasons = []
        if n_scored != PUBLISHED_N:
            reasons.append(
                f"this run scored {n_scored} tasks, the published figures are over "
                f"{PUBLISHED_N}"
            )
        if s.get("mode") != "brain-parity":
            reasons.append(f"this run used mode `{s.get('mode')}`, not `brain-parity`")
        note = (
            "**Not directly comparable — read this before reading the table.** "
            + "; ".join(reasons).capitalize()
            + ". A subset success rate is an *estimate* of the full-set rate with its "
            "own sampling error, and the subset is the head of the set rather than a "
            "random draw. Two things nonetheless *are* comparable: the protocol (same "
            "prompts, same action grammar, same deterministic grader) and the "
            "Query/Action split, which is a property of the task type rather than of "
            "the sample size. Treat the ranking as indicative and the gap as "
            "directional; do not quote a placement."
        )
    return BaselineSet(
        baselines=[
            Baseline(name=k, values=dict(v), source="MedAgentBench, NEJM AI 2025", n=PUBLISHED_N)
            for k, v in pub.items()
        ],
        comparable=comparable,
        comparability_note=note,
        published_protocol=(
            f"full {PUBLISHED_N}-task set, same action grammar, same deterministic grader"
        ),
        source="MedAgentBench, NEJM AI 2025 (Table 2)",
    )


def _failures(tasks: list[Any], s: dict) -> list[FailureCategory]:
    out: list[FailureCategory] = []
    n = len(tasks) or 1
    by_status = Counter(t.get("status") for t in tasks if isinstance(t, dict))
    wi = s.get("write_integrity") or {}

    # 1. Protocol violations — the agent's reply parsed as none of the three verbs.
    invalid = [t for t in tasks if isinstance(t, dict) and t.get("status") == "agent_invalid_action"]
    if invalid or by_status.get("agent_invalid_action"):
        out.append(
            FailureCategory(
                key="agent_invalid_action",
                label="Protocol violation — the reply was not GET, POST or FINISH",
                count=len(invalid) or by_status.get("agent_invalid_action", 0),
                severity="high",
                denominator=n,
                description=(
                    "The action grammar is three verbs. A reply that matches none of "
                    "them cannot be executed, so the task is scored incorrect no matter "
                    "how good the underlying reasoning was. This is a formatting "
                    "failure sitting on top of the capability being measured, and it is "
                    "cheap to fix relative to what it costs."
                ),
                examples=[
                    FailureExample(
                        case_id=str(t.get("task_id")),
                        summary=f"{t.get('category')} · {t.get('rounds')} rounds",
                        evidence=_last_reply(t)[:300],
                        artifact=f"tasks/{t.get('task_id')}.json",
                    )
                    for t in invalid[:3]
                ],
            )
        )

    # 2. Wrote but did not say — a silent side effect on a patient chart.
    silent_ids = list(dig(wi, "wrote_but_did_not_say", "task_ids", default=[]) or [])
    if silent_ids:
        by_id = {t.get("task_id"): t for t in tasks if isinstance(t, dict)}
        out.append(
            FailureCategory(
                key="wrote_but_did_not_say",
                label="Silent write — the chart changed and the user was not told",
                count=len(silent_ids),
                severity="high",
                denominator=int(dig(wi, "n_action_episodes", default=n) or n),
                description=(
                    "A write was emitted, accepted and verified in the chart, but the "
                    "agent's closing reply never told the user it had done it. The "
                    "inverse failure (said-but-did-not-write) is the one that gets "
                    "written about; this one is the same integrity gap pointing the "
                    "other way, and on a medication order it is just as serious."
                ),
                examples=[
                    FailureExample(
                        case_id=str(tid),
                        summary=f"{dig(by_id.get(tid) or {}, 'category', default='?')}"
                        f" · {dig(by_id.get(tid) or {}, 'integrity', 'emitted_writes', default=0)} write(s) landed",
                        evidence=_last_reply(by_id.get(tid) or {})[:300],
                        artifact=f"tasks/{tid}.json",
                    )
                    for tid in silent_ids[:3]
                ],
            )
        )

    # 3. Non-conformant FHIR — accepted here, would fail a strict server.
    nc_ids = list(dig(wi, "emitted_nonconformant_fhir", "task_ids", default=[]) or [])
    if nc_ids:
        by_id = {t.get("task_id"): t for t in tasks if isinstance(t, dict)}
        ex = []
        for tid in nc_ids[:3]:
            t = by_id.get(tid) or {}
            issues = []
            for wa in t.get("write_attempts") or []:
                issues += list(wa.get("conformance_issues") or [])
            ex.append(
                FailureExample(
                    case_id=str(tid),
                    summary=f"{t.get('category')} · resource "
                    f"{dig((t.get('write_attempts') or [{}])[0], 'resource_type', default='?')}",
                    evidence="; ".join(str(i) for i in issues)[:300]
                    or "conformance issue recorded without detail",
                    artifact=f"tasks/{tid}.json",
                )
            )
        out.append(
            FailureCategory(
                key="emitted_nonconformant_fhir",
                label="Non-conformant FHIR accepted by a permissive server",
                count=len(nc_ids),
                severity="medium",
                denominator=int(dig(wi, "total_writes_emitted", default=n) or n),
                description=(
                    "These writes landed and scored correct. They would be rejected by "
                    "a server that validates against the profile. The benchmark's grader "
                    "does not check conformance, so this is a failure the score cannot "
                    "see — which is exactly why it belongs in the report."
                ),
                examples=ex,
            )
        )

    # 4. Categories at or near zero — where the capability simply is not there.
    per_cat = s.get("per_category") or {}
    zeros = [
        (k, v)
        for k, v in per_cat.items()
        if isinstance(v, dict) and (v.get("success_rate_pct") or 0) <= 10.0
    ]
    if zeros:
        by_cat: dict[str, list[Any]] = defaultdict(list)
        for t in tasks:
            if isinstance(t, dict):
                by_cat[str(t.get("category"))].append(t)
        ex = []
        for k, _v in zeros[:3]:
            wrong = [t for t in by_cat.get(k, []) if not t.get("correct")]
            if wrong:
                t = wrong[0]
                ex.append(
                    FailureExample(
                        case_id=str(t.get("task_id")),
                        summary=f"{k} · grader said: {dig(t, 'grade', 'reason', default='?')}",
                        evidence=_last_reply(t)[:300],
                        artifact=f"tasks/{t.get('task_id')}.json",
                    )
                )
        out.append(
            FailureCategory(
                key="floor_categories",
                label="Task categories at or near zero",
                count=sum(int(v.get("n") or 0) - int(v.get("correct") or 0) for _k, v in zeros),
                severity="high",
                denominator=sum(int(v.get("n") or 0) for _k, v in zeros),
                description=(
                    "Categories scoring ≤10%: "
                    + ", ".join(f"`{k}` ({_pct(v.get('success_rate_pct'), 0)})" for k, v in zeros)
                    + ". A near-zero category is qualitatively different from a weak "
                    "one — it means the task shape is not being handled at all, and the "
                    "overall average is hiding a cliff."
                ),
                examples=ex,
            )
        )

    # 5. Say-fidelity findings recorded by the harness itself.
    fc = s.get("finding_counts") or {}
    for ftype, count in fc.items():
        hits = [
            t
            for t in tasks
            if isinstance(t, dict)
            and any((f or {}).get("type") == ftype for f in (t.get("findings") or []))
        ]
        out.append(
            FailureCategory(
                key=f"finding_{ftype}",
                label=f"Harness finding: `{ftype}`",
                count=int(count),
                severity="medium",
                denominator=n,
                description=(
                    "Recorded by the harness's own integrity checks, independent of "
                    "whether the task scored correct."
                ),
                examples=[
                    FailureExample(
                        case_id=str(t.get("task_id")),
                        summary=str(t.get("category")),
                        evidence=next(
                            (
                                str((f or {}).get("detail"))
                                for f in (t.get("findings") or [])
                                if (f or {}).get("type") == ftype
                            ),
                            "",
                        )[:300],
                        artifact=f"tasks/{t.get('task_id')}.json",
                    )
                    for t in hits[:2]
                ],
            )
        )
    return out


def _last_reply(task: Any) -> str:
    turns = (task or {}).get("turns") or []
    for t in reversed(turns):
        r = (t or {}).get("agent_reply")
        if r:
            return str(r).replace("\n", " ")
    return ""


def _limitations(s: dict, sampling: Sampling, n_scored: int) -> list[Limitation]:
    wi = s.get("write_integrity") or {}
    out = [
        Limitation(
            "subset_not_full_set",
            (
                f"{n_scored} of {PUBLISHED_N} published tasks were scored. Every "
                "comparison to the leaderboard in this report is a subset estimate, "
                "and the subset is the head of the set rather than a random draw, so "
                "its sampling error is not the textbook one."
            ),
            "high",
        )
        if n_scored != PUBLISHED_N
        else None,
        Limitation(
            "sandbox_not_a_hospital",
            (
                "Writes go to a FHIR sandbox that accepts resources a production EHR "
                "would reject — "
                f"{dig(wi, 'emitted_nonconformant_fhir', 'n', default=0)} of "
                f"{wi.get('total_writes_emitted', 0)} emitted writes in this run were "
                "non-conformant and still scored correct. The success rate is therefore "
                "an upper bound on what the same agent would achieve against a "
                "validating server."
            ),
            "high",
        )
        if wi
        else None,
        Limitation(
            "grader_scope",
            (
                "The deterministic grader checks the answer, not the route: a task can "
                "score correct having taken an inefficient or clinically odd path to "
                "get there, and can score incorrect on a formatting slip alone."
            ),
            "medium",
        ),
        Limitation(
            "no_voice_path",
            (
                "This benchmark has no spoken surface — its actions are structured HTTP "
                "strings. Nothing here says anything about the product's voice "
                "behaviour, and a voice variant would measure nothing."
            ),
            "low",
        ),
        Limitation(
            "single_run",
            (
                "One pass, no repeats. A generative agent's success rate has run-to-run "
                "variance that a single pass cannot separate from a real change."
            ),
            "medium",
        ),
    ]
    return [x for x in out if x]
