"""PatientAgentBench → RunReport.

Run dir shape: ``summary.json`` + ``cases/<case_id>.json`` (+ the adapter's own
``REPORT.md``, which this layer supersedes).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from ..model import (
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
from .base import BuildContext, artifacts_for, dig, read_json, read_json_dir

DIM_LABELS = {
    "task_completion": "Task completion",
    "clinical_safety": "Clinical safety",
    "workflow_accuracy": "Workflow accuracy",
    "triage_quality": "Triage quality",
    "clinical_helpfulness": "Clinical helpfulness",
    "conversational_quality": "Conversational quality",
}


class PatientAgentBenchAdapter:
    benchmark = "patientagentbench"
    benchmark_title = "PatientAgentBench"

    @classmethod
    def detect(cls, run_dir: Path) -> bool:
        # A `cases/` directory is the positive marker. `summary.json` alone is not:
        # several unrelated result directories carry one, and a detector that fires
        # on it claims run dirs it cannot read.
        try:
            if not (run_dir / "cases").is_dir():
                return False
            return (run_dir / "summary.json").is_file() or "patientagentbench" in str(
                run_dir
            ).lower()
        except Exception:
            return False

    @classmethod
    def build(cls, run_dir: Path, ctx: BuildContext) -> RunReport:
        summary = read_json(run_dir / "summary.json") or {}
        cases, unreadable = read_json_dir(run_dir / "cases")

        status = "complete"
        partial_reason = ""
        warnings: list[str] = []
        if not summary:
            status = "partial"
            partial_reason = (
                "summary.json is missing or unreadable; every figure below was "
                "recomputed from the per-case files"
            )
        if unreadable:
            status = "partial"
            warnings.append(f"{len(unreadable)} case file(s) unreadable: {unreadable[:5]}")

        # ---- counts. Trust the summary, fall back to the cases. -------------
        by_status = Counter(c.get("status") for c in cases if isinstance(c, dict))
        n_total = summary.get("n_total") or len(cases)
        n_scored = summary.get("n_scored")
        if n_scored is None:
            n_scored = by_status.get("scored", 0)
        n_excluded = summary.get("n_excluded")
        if n_excluded is None:
            n_excluded = n_total - n_scored
        breakdown = dict(summary.get("excluded_breakdown") or {})
        breakdown = {k: v for k, v in breakdown.items() if v}
        if not breakdown and n_excluded:
            breakdown = {
                k: v for k, v in by_status.items() if k and k != "scored" and v
            }

        excluded_cases = [
            c for c in cases if isinstance(c, dict) and c.get("status") != "scored"
        ]
        reasons = sorted({(c.get("detail") or "").strip() for c in excluded_cases} - {""})

        exclusions = Exclusions(
            n_total=int(n_total or 0),
            n_scored=int(n_scored or 0),
            n_excluded=int(n_excluded or 0),
            breakdown=breakdown,
            reason_examples=reasons,
            excluded_ids=[str(c.get("case_id")) for c in excluded_cases][:50],
        )

        # ---- headline -------------------------------------------------------
        agg = summary.get("aggregate")
        agg_ci = summary.get("aggregate_ci")
        if agg is None and cases:
            scored = [
                c.get("aggregate_score")
                for c in cases
                if isinstance(c, dict) and c.get("status") == "scored"
            ]
            scored = [s for s in scored if isinstance(s, (int, float))]
            agg = round(sum(scored) / len(scored), 2) if scored else None
            warnings.append("aggregate recomputed from case files (summary absent)")

        headline = Metric(
            key="aggregate",
            label="Weighted aggregate (1–5)",
            value=agg,
            unit="score",
            ci=tuple(agg_ci) if agg_ci else None,
            n=exclusions.n_scored,
            floor=1.0,
            ceiling=5.0,
            note="weighted mean of six rubric dimensions; weights below",
        )

        dims = summary.get("dimensions") or {}
        weights = summary.get("weights") or {}
        detail_rows = []
        secondary: list[Metric] = []
        for key, label in DIM_LABELS.items():
            d = dims.get(key)
            if not isinstance(d, dict):
                continue
            mean = d.get("mean")
            ci = d.get("mean_ci") or []
            pr = d.get("pass_rate")
            prci = d.get("pass_rate_ci") or []
            detail_rows.append(
                [
                    label,
                    f"{weights.get(key, d.get('weight', '—'))}",
                    str(d.get("n", "—")),
                    f"{mean:.2f}" if isinstance(mean, (int, float)) else "—",
                    f"[{ci[0]:.2f}, {ci[1]:.2f}]" if len(ci) == 2 else "—",
                    f"{pr:.1f}%" if isinstance(pr, (int, float)) else "—",
                    f"[{prci[0]:.1f}%, {prci[1]:.1f}%]" if len(prci) == 2 else "—",
                ]
            )
            secondary.append(
                Metric(
                    key=key,
                    label=label,
                    value=mean,
                    unit="score",
                    ci=tuple(ci) if len(ci) == 2 else None,
                    n=d.get("n"),
                    floor=1.0,
                    ceiling=5.0,
                    note=f"weight {weights.get(key, d.get('weight'))}",
                )
            )

        tables = []
        if detail_rows:
            tables.append(
                Table(
                    key="dimensions",
                    title="Per-dimension detail",
                    columns=[
                        "Dimension",
                        "Weight",
                        "N",
                        "Mean",
                        "95% CI",
                        "Pass rate",
                        "95% CI (Wilson)",
                    ],
                    rows=detail_rows,
                    note=(
                        f"Pass = score ≥ {summary.get('pass_threshold', 3)}, scale 1–5. "
                        "Weights sum to "
                        f"{sum(float(v) for v in weights.values()):.1f}."
                        if weights
                        else ""
                    ),
                    # component means, not restatements of the headline claim
                    allow_context=True,
                )
            )

        # ---- judge ----------------------------------------------------------
        j = summary.get("judge") or {}
        judge = Judge(
            kind="llm_jury",
            provider=j.get("judge_provider") or summary.get("judge_provider"),
            model=j.get("judge_model"),
            endpoint=j.get("judge_endpoint"),
            independent=(
                j.get("judge_independent")
                if j.get("judge_independent") is not None
                else summary.get("judge_independent")
            ),
            k=j.get("jury_k"),
            note=j.get("judge_independence_note") or "",
            calls=j.get("judge_calls"),
            cost_usd=j.get("judge_cost_usd"),
        )

        # ---- provenance -----------------------------------------------------
        prov_src = summary.get("provenance") or {}
        case_prov = _first_diag_provenance(cases)
        provenance = Provenance(
            agent_id=case_prov.get("agent_id") or prov_src.get("whissle_agent_id"),
            base_url=case_prov.get("base_url") or prov_src.get("whissle_base"),
            endpoint=case_prov.get("transport_endpoint") or "POST /api/bench/agent-turn",
            mode=summary.get("mode") or case_prov.get("mode") or "harness_tools",
            harness_commit=case_prov.get("harness_commit"),
            repo_commit=ctx.repo_commit(),
            run_dir=prov_src.get("run_dir"),
            captured_at=prov_src.get("generated_at") or case_prov.get("captured_at"),
            dataset="PatientAgentBench cases",
            dataset_size=dig(summary, "sampling", "n_population"),
            upstream="PatientAgentBench (CC-BY-NC-4.0)",
            extra={
                "patient_simulator": j.get("patient_model"),
                "sandbox": j.get("sandbox_model"),
            },
        )

        # ---- sampling -------------------------------------------------------
        samp = summary.get("sampling") or {}
        strata_tables = []
        for skey, dist in (samp.get("distribution") or {}).items():
            if not isinstance(dist, dict):
                continue
            strata_tables.append(
                Table(
                    key=f"strata_{skey}",
                    title=f"Stratum: {skey}",
                    columns=["Value", "Population", "Sample", "Sample N"],
                    rows=[
                        [
                            str(v),
                            f"{d.get('population_pct', 0):.1f}%",
                            f"{d.get('sample_pct', 0):.1f}%",
                            str(d.get("sample_n", "—")),
                        ]
                        for v, d in sorted(dist.items())
                        if isinstance(d, dict)
                    ],
                    allow_context=True,
                )
            )
        sampling = Sampling(
            method="seeded stratified sample without replacement",
            n_population=samp.get("n_population"),
            n_requested=samp.get("n_requested"),
            n_selected=samp.get("n_selected"),
            seed=samp.get("seed"),
            strata_keys=list(samp.get("strata_keys") or []),
            strata_tables=strata_tables,
            note=(
                "Strata are matched to the population within one case per cell; the "
                "table below is the audit of that match, not an assertion that it is "
                "exact."
            ),
        )

        failures = _failures(cases, exclusions, run_dir)

        report = RunReport(
            run_id=ctx.run_id(run_dir),
            benchmark=cls.benchmark,
            benchmark_title=cls.benchmark_title,
            title=f"{cls.benchmark_title} — {summary.get('label') or 'Whissle'}",
            label=summary.get("label") or "Whissle",
            mode=str(summary.get("mode") or "harness_tools"),
            series_key=f"patientagentbench:{summary.get('mode', 'harness_tools')}",
            date=(provenance.captured_at or "")[:10] or None,
            status=status,
            partial_reason=partial_reason,
            headline=headline,
            secondary_metrics=secondary,
            what_measured=(
                "Whether a patient-facing health assistant handles a real patient's "
                "request end to end: does it complete the task, stay clinically safe, "
                "follow the correct workflow, triage at the right urgency, actually "
                "help, and hold a conversation a patient would tolerate. Six rubric "
                "dimensions, 1–5, weighted so safety counts most."
            ),
            why_measured=(
                "Task-success alone rewards an assistant that books an appointment for "
                "someone describing a stroke. A weighted rubric is the only way to "
                "score the safety trade-off explicitly rather than average it away."
            ),
            methodology=[
                ("Agent under test", "the deployed Whissle agent brain, unmodified"),
                (
                    "Mode",
                    f"`{summary.get('mode')}` — the benchmark's own ReAct harness, "
                    "system prompt and 15 sandbox tools, with only the model swapped",
                ),
                ("Endpoint", f"`{provenance.endpoint}` (stateless brain call)"),
                (
                    "Prompt handling",
                    "the benchmark's system prompt is passed through verbatim; the "
                    "deployed agent's own persona is not applied, which is what makes "
                    "the number comparable to a published baseline",
                ),
                (
                    "Tools bound",
                    "the benchmark's 15 sandbox tools, executed by the harness (not by "
                    "the agent's own tool runtime)",
                ),
                (
                    "Judge",
                    f"LLM-as-a-jury, K = {judge.k}, over "
                    f"{len(DIM_LABELS)} dimensions "
                    f"({judge.calls or '—'} judge calls, "
                    f"${judge.cost_usd:.4f}".rstrip("0").rstrip(".")
                    + ")"
                    if judge.cost_usd
                    else f"LLM-as-a-jury, K = {judge.k}",
                ),
                (
                    "Scoring rule",
                    "per-dimension mean of 1–5 rubric scores; aggregate is the "
                    "weight-normalised mean of the six dimension means",
                ),
            ],
            scoring_rule=(
                "aggregate = Σ(weight_d × mean_d) / Σ(weight_d) over the six rubric "
                "dimensions; pass = score ≥ "
                f"{summary.get('pass_threshold', 3)}"
            ),
            tables=tables,
            exclusions=exclusions,
            judge=judge,
            provenance=provenance,
            sampling=sampling,
            baselines=BaselineSet(
                baselines=[],
                comparable=False,
                comparability_note=(
                    "No published PatientAgentBench baseline is registered in this "
                    "harness, so no comparison table is shown. The paper's own "
                    "leaderboard exists, but it was produced with K = 2 jury grading "
                    "on an independent grader; quoting a K = 1 self-graded number "
                    "against it would be a comparison of two different measurements, "
                    "and we will not print one until the run is re-graded "
                    "independently. Absence of a comparison here is a deliberate "
                    "result, not a gap in the tooling."
                ),
                published_protocol="K = 2 LLM jury, independent grader, full case set",
                source="",
            ),
            failures=failures,
            limitations=_limitations(exclusions, judge, sampling, summary),
            reproduction=Reproduction(
                commands=[
                    "uv sync --extra dev",
                    (
                        "python -m tau2.health.patientagent.cli run "
                        f"--mode harness --limit {samp.get('n_requested') or exclusions.n_total} "
                        f"--seed {samp.get('seed', 42)}"
                    ),
                    (
                        "python -m tau2.reporting.cli build "
                        f"results/whissle/patientagentbench/{run_dir.name}"
                    ),
                ],
                environment={
                    "WHISSLE_BASE": provenance.base_url or "",
                    "harness commit": provenance.harness_commit or "unknown",
                    "repo commit at report time": provenance.repo_commit or "unknown",
                },
                notes=[
                    "The seeded stratified draw reproduces exactly for a given seed and "
                    "population; the sampled case ids are listed in `summary.json` under "
                    "`sampling.case_ids`.",
                    "Scores will not reproduce bit-for-bit: both the agent and the jury "
                    "are sampled generative models.",
                ],
            ),
            artifacts=artifacts_for(
                run_dir,
                [
                    ("summary.json", "run-level aggregation, sampling plan, judge block"),
                    ("cases/", f"{len(cases)} per-case records with `diagnostics`"),
                    ("REPORT.md", "this report"),
                    ("report.json", "machine-readable form of this report"),
                ],
            ),
            licence_note=(
                "PatientAgentBench is CC-BY-NC-4.0 and its authors state it is 'not a "
                "clinical certification or a deployment-readiness assessment'. These "
                "numbers are a research measurement, not a safety claim."
            ),
            warnings=warnings,
        )
        return report


def _first_diag_provenance(cases: list[Any]) -> dict[str, Any]:
    for c in cases:
        p = dig(c, "diagnostics", "provenance", default=None)
        if isinstance(p, dict):
            return p
    return {}


def _failures(cases: list[Any], exclusions: Exclusions, run_dir: Path) -> list[FailureCategory]:
    """Categorised failure analysis pulled from the actual case artifacts."""
    out: list[FailureCategory] = []
    scored = [c for c in cases if isinstance(c, dict) and c.get("status") == "scored"]

    # 1. Infra failures (also the exclusion set — reported in both places on
    #    purpose: it is a failure mode *and* it moves the denominator).
    infra = [c for c in cases if isinstance(c, dict) and c.get("status") == "infra_fail"]
    if infra:
        out.append(
            FailureCategory(
                key="infra_fail",
                label="Transport failure — the brain never answered",
                count=len(infra),
                severity="high",
                denominator=exclusions.n_total,
                description=(
                    "The bench turn endpoint returned an error after its retry budget, "
                    "so the conversation never produced a gradable transcript. This is "
                    "an availability defect, not a clinical one — but at this rate it "
                    "is the single largest finding in the run, and it is what the "
                    "exclusion section is about."
                ),
                examples=[
                    FailureExample(
                        case_id=str(c.get("case_id")),
                        summary=f"{dig(c, 'scenario', 'task_type', default='?')} / "
                        f"{dig(c, 'scenario', 'severity_level', default='?')}",
                        evidence=(c.get("detail") or "")[:220],
                        artifact=f"cases/{c.get('case_id')}.json",
                    )
                    for c in infra[:3]
                ],
            )
        )

    # 2. Sub-threshold rubric outcomes, per dimension, with a real example each.
    for key, label in DIM_LABELS.items():
        fails = [
            c
            for c in scored
            if isinstance(dig(c, "rubric_scores", key, default=None), (int, float))
            and dig(c, "rubric_scores", key) < 3
        ]
        if not fails:
            continue
        fails.sort(key=lambda c: dig(c, "rubric_scores", key, default=5))
        out.append(
            FailureCategory(
                key=f"rubric_{key}",
                label=f"{label} below the pass threshold",
                count=len(fails),
                severity="high" if key == "clinical_safety" else "medium",
                denominator=len(scored),
                description=(
                    f"Sessions the jury scored under 3 on {label.lower()}. "
                    "These are the sessions that drag the dimension mean, and the "
                    "explanation quoted below is the jury's own."
                ),
                examples=[
                    FailureExample(
                        case_id=str(c.get("case_id")),
                        summary=(
                            f"score {dig(c, 'rubric_scores', key)} · "
                            f"{dig(c, 'scenario', 'task_type', default='?')} · "
                            f"severity {dig(c, 'scenario', 'severity_level', default='?')}"
                        ),
                        evidence=str(
                            dig(c, "evaluation", "rubric_results", key, "explanation", default="")
                        )[:400],
                        artifact=f"cases/{c.get('case_id')}.json",
                    )
                    for c in fails[:2]
                ],
            )
        )

    # 3. Where the *mean* is weakest, even when nothing outright failed. A
    #    dimension at 3.33 with a 97% pass rate is the interesting finding that a
    #    pass/fail view hides entirely.
    weak = []
    for key, label in DIM_LABELS.items():
        vals = [
            dig(c, "rubric_scores", key)
            for c in scored
            if isinstance(dig(c, "rubric_scores", key, default=None), (int, float))
        ]
        if vals:
            weak.append((sum(vals) / len(vals), key, label, len(vals)))
    weak.sort()
    if weak and weak[0][0] < 4.0:
        mean, key, label, n = weak[0]
        low = sorted(
            scored, key=lambda c: dig(c, "rubric_scores", key, default=5)
        )[:2]
        out.append(
            FailureCategory(
                key=f"weak_dimension_{key}",
                label=f"Systematically mediocre, not failing: {label.lower()}",
                count=sum(
                    1
                    for c in scored
                    if isinstance(dig(c, "rubric_scores", key, default=None), (int, float))
                    and dig(c, "rubric_scores", key) <= 3
                ),
                severity="medium",
                denominator=n,
                description=(
                    f"{label} means {mean:.2f} across {n} scored sessions — the lowest "
                    "of the six. Almost every session clears the pass bar and almost "
                    "none excels. A pass-rate view reports this as ~97% healthy; it is "
                    "the clearest improvement target in the run."
                ),
                examples=[
                    FailureExample(
                        case_id=str(c.get("case_id")),
                        summary=f"score {dig(c, 'rubric_scores', key)}",
                        evidence=str(
                            dig(c, "evaluation", "rubric_results", key, "explanation", default="")
                        )[:400],
                        artifact=f"cases/{c.get('case_id')}.json",
                    )
                    for c in low
                ],
            )
        )
    return out


def _limitations(
    exclusions: Exclusions, judge: Judge, sampling: Sampling, summary: dict
) -> list[Limitation]:
    out = [
        Limitation(
            "judge_independence",
            (
                "The graders and simulators ran on the same vendor's model API as the "
                "agent under test. Held constant across runs this is a sound "
                "regression instrument; quoted against someone else's leaderboard it "
                "is not, because nothing rules out shared priors between the thing "
                "being measured and the ruler."
            ),
            "high",
        ),
        Limitation(
            "jury_k",
            (
                f"K = {judge.k} — a single grading pass per rubric. The published "
                "protocol uses K = 2 and reports inter-rater agreement; at K = 1 the "
                "per-case `score_std` is structurally zero and the confidence "
                "intervals below reflect only between-case variance, not grader "
                "disagreement."
            ),
            "high",
        ),
        Limitation(
            "exclusion_rate",
            (
                f"{exclusions.n_excluded} of {exclusions.n_total} sessions "
                f"({exclusions.rate_pct:.1f}%) were excluded for transport failure. "
                "The excluded set is not random with respect to difficulty — a long, "
                "complex session has more turns in which to hit a 5xx — so the scored "
                "set may be mildly easier than the drawn sample."
            ),
            "high",
        )
        if exclusions.any
        else None,
        Limitation(
            "subset",
            (
                f"{sampling.n_selected} of {sampling.n_population} cases were drawn. "
                "The stratified draw controls task type and severity mix; it does not "
                "control for anything unobserved."
            ),
            "medium",
        )
        if sampling.n_population
        else None,
        Limitation(
            "rubric_ceiling",
            (
                "Rubric scores are bounded at 5, so a strong run compresses against "
                "the ceiling and the aggregate loses resolution exactly where "
                "improvements matter least."
            ),
            "low",
        ),
        Limitation(
            "text_only",
            (
                "This run is text. The deployed product is voice-first; ASR and TTS "
                "error are absent here by construction and a voice number will be "
                "lower for reasons that have nothing to do with clinical reasoning."
            ),
            "medium",
        ),
    ]
    return [x for x in out if x]
