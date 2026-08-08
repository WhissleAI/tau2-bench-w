"""AgentClinic → RunReport.

Run dir shape: ``RUN.json`` (written at start) + ``cases/<id>.json`` (written as
each case finishes) + ``SUMMARY.json`` (written at the end) + ``transcripts/``.

Because ``SUMMARY.json`` only appears when the run finishes, this adapter always
recomputes the aggregation from the case files and uses ``SUMMARY.json`` purely as
a cross-check. That is what lets it render a run that is still executing — which
is not a nicety: a 100-case clinical run takes long enough that "show me where it
is" is the common question.
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
from .base import BuildContext, artifacts_for, dig, read_json, read_json_dir, wilson_ci


class AgentClinicAdapter:
    benchmark = "agentclinic"
    benchmark_title = "AgentClinic"

    @classmethod
    def detect(cls, run_dir: Path) -> bool:
        try:
            return (run_dir / "RUN.json").is_file() or (
                (run_dir / "cases").is_dir() and "agentclinic" in str(run_dir).lower()
            )
        except Exception:
            return False

    @classmethod
    def build(cls, run_dir: Path, ctx: BuildContext) -> RunReport:
        run = read_json(run_dir / "RUN.json") or {}
        summary = read_json(run_dir / "SUMMARY.json") or {}
        cases, unreadable = read_json_dir(run_dir / "cases")
        meta = {**run, **summary}  # SUMMARY wins where both exist

        warnings: list[str] = []
        status = "complete"
        partial_reason = ""

        planned = meta.get("limit") or len(meta.get("selected_ids") or []) or len(cases)
        if not summary:
            status = "partial"
            partial_reason = (
                f"SUMMARY.json has not been written — the run is still executing or was "
                f"interrupted. {len(cases)} of {planned} planned cases are on disk and "
                "everything below is computed from those. The figures will move."
            )
        elif len(cases) < int(planned or 0):
            status = "partial"
            partial_reason = (
                f"only {len(cases)} of {planned} planned case files are present"
            )
        if unreadable:
            status = "partial"
            warnings.append(f"{len(unreadable)} case file(s) unreadable: {unreadable[:5]}")

        scored = [c for c in cases if isinstance(c, dict) and not c.get("infra_fail")]
        infra = [c for c in cases if isinstance(c, dict) and c.get("infra_fail")]
        outcomes = Counter(dig(c, "score", "outcome", default="unknown") for c in scored)
        n_correct = sum(1 for c in scored if dig(c, "score", "correctness"))
        n_scored = len(scored)
        acc = round(100.0 * n_correct / n_scored, 1) if n_scored else None

        exclusions = Exclusions(
            n_total=len(cases),
            n_scored=n_scored,
            n_excluded=len(infra),
            breakdown={"infra_fail": len(infra)} if infra else {},
            reason_examples=sorted(
                {str(c.get("error")) for c in infra if c.get("error")}
            ),
            excluded_ids=[str(c.get("scenario_id")) for c in infra][:50],
        )

        headline = Metric(
            key="accuracy",
            label="Diagnostic accuracy",
            value=acc,
            unit="pct",
            ci=wilson_ci(n_correct, n_scored),
            n=n_scored,
            floor=0.0,
            ceiling=100.0,
            note="upstream's formula, unmodified: correct / presented",
        )

        # "Committed" means a diagnosis was actually named. A refusal and a case that
        # ran out of inferences are both non-commits — counting the second as a commit
        # would report a 100% commit rate on a run where ten cases never concluded.
        n_committed = sum(
            1
            for c in scored
            if dig(c, "score", "outcome") in ("correct", "incorrect")
            and not dig(c, "score", "declined")
        )
        secondary = [
            Metric(
                key="accuracy_when_committed",
                label="Accuracy when a diagnosis was actually given",
                value=round(100.0 * n_correct / n_committed, 1) if n_committed else None,
                unit="pct",
                ci=wilson_ci(n_correct, n_committed),
                n=n_committed,
                floor=0.0,
                ceiling=100.0,
                note="refusals and non-commits removed from the denominator",
            ),
            Metric(
                key="commit_rate",
                label="Commit rate",
                value=round(100.0 * n_committed / n_scored, 1) if n_scored else None,
                unit="pct",
                ci=wilson_ci(n_committed, n_scored),
                n=n_scored,
                floor=0.0,
                ceiling=100.0,
                note="how often the agent named a diagnosis at all",
            ),
        ]

        tables = [
            Table(
                key="outcomes",
                title="Outcome distribution",
                columns=["Outcome", "N", "Share", "Reading"],
                rows=[
                    [
                        k,
                        str(v),
                        f"{100.0 * v / n_scored:.1f}%" if n_scored else "—",
                        {
                            "correct": "named the right diagnosis",
                            "incorrect": "named a diagnosis; it was wrong",
                            "declined": "refused to commit — safe, but unhelpful",
                            "no_commit": "ran out of inferences without committing",
                        }.get(k, ""),
                    ]
                    for k, v in outcomes.most_common()
                ],
                note=(
                    "`declined` and `no_commit` both count as incorrect in the headline "
                    "accuracy, which is upstream's rule. Separating them is how you tell "
                    "a cautious agent from a lost one."
                ),
                allow_context=True,
            )
        ]

        inf = [c.get("inferences_used") for c in scored if isinstance(c.get("inferences_used"), int)]
        tests = [len(c.get("tests_ordered") or []) for c in scored]
        if inf:
            tables.append(
                Table(
                    key="effort",
                    title="Diagnostic effort",
                    columns=["Measure", "Mean", "Max", "Budget"],
                    rows=[
                        [
                            "Inferences used",
                            f"{sum(inf) / len(inf):.1f}",
                            str(max(inf)),
                            str(meta.get("total_inferences", "—")),
                        ],
                        [
                            "Tests ordered",
                            f"{sum(tests) / len(tests):.1f}" if tests else "—",
                            str(max(tests)) if tests else "—",
                            "unbounded",
                        ],
                    ],
                    note=(
                        "An agent that hits the inference budget is being cut off "
                        "mid-workup, and its `no_commit` count is a budget artefact "
                        "rather than a capability finding."
                    ),
                    allow_context=True,
                )
            )

        case_prov = _first_diag_provenance(cases)
        provenance = Provenance(
            agent_id=meta.get("agent_id") or case_prov.get("agent_id"),
            base_url=meta.get("base") or case_prov.get("base_url"),
            endpoint=case_prov.get("transport_endpoint") or "POST /api/bench/agent-turn",
            mode=str(meta.get("mode") or "text"),
            harness_commit=case_prov.get("harness_commit"),
            repo_commit=ctx.repo_commit(),
            run_dir=str(run_dir),
            captured_at=case_prov.get("captured_at") or _ts_to_iso(meta.get("ts")),
            dataset=str(meta.get("dataset") or "MedQA"),
            dataset_size=meta.get("dataset_size"),
            upstream=meta.get("upstream")
            or "github.com/SamuelSchmidgall/AgentClinic (arXiv:2405.07960)",
            extra={
                "protocol": meta.get("protocol"),
                "history": meta.get("history"),
                "prompt_mode": meta.get("prompt_mode"),
                "vision": meta.get("vision"),
                "agent_type": meta.get("agent_type"),
                "agent_created_for_run": meta.get("agent_created_for_run"),
                "agent_deleted": meta.get("agent_deleted"),
            },
        )

        sampling = Sampling(
            method=f"{meta.get('sample', 'head')}-of-set selection",
            n_population=meta.get("dataset_size"),
            n_requested=meta.get("limit"),
            n_selected=len(meta.get("selected_ids") or []) or len(cases),
            seed=meta.get("seed"),
            strata_keys=[],
            note=(
                "`head` selection takes the leading N scenarios of the dataset. It is "
                "deterministic and it is not random — any ordering structure in the "
                "dataset is inherited wholesale."
            ),
        )

        judge = Judge(
            kind="llm_jury",
            provider=meta.get("judge_provider"),
            model=meta.get("judge_model"),
            endpoint=meta.get("judge_endpoint"),
            independent=meta.get("judge_independent"),
            k=1,
            note=str(meta.get("judge_independence_note") or ""),
            calls=meta.get("judge_calls"),
            cost_usd=meta.get("judge_cost_usd"),
        )

        report = RunReport(
            run_id=ctx.run_id(run_dir),
            benchmark=cls.benchmark,
            benchmark_title=cls.benchmark_title,
            title=f"{cls.benchmark_title} — Whissle as the doctor ({meta.get('dataset', 'MedQA')})",
            mode=str(meta.get("mode") or "text"),
            series_key=(
                f"agentclinic:{meta.get('dataset', 'MedQA')}:{meta.get('mode', 'text')}"
                f":{meta.get('prompt_mode', 'override')}:{meta.get('vision', 'off')}"
            ),
            date=(provenance.captured_at or "")[:10] or None,
            status=status,
            partial_reason=partial_reason,
            headline=headline,
            secondary_metrics=secondary,
            what_measured=(
                "Whether an agent can run a diagnostic consultation: take a patient's "
                "presentation, ask the questions that discriminate between the "
                "candidate diagnoses, order the tests it needs, and commit to an "
                "answer within a bounded number of inferences. The agent plays the "
                "doctor; a simulated patient and a simulated measurement device play "
                "the other side."
            ),
            why_measured=(
                "A single-turn medical QA score says whether a model knows the answer. "
                "This says whether it can *get* to the answer through a conversation "
                "where the information arrives only if it asks — which is the shape of "
                "every real intake."
            ),
            methodology=[
                ("Agent under test", "the deployed Whissle agent brain, unmodified"),
                (
                    "Mode",
                    f"`{meta.get('mode')}` transport, "
                    f"`{meta.get('protocol', 'markers')}` action protocol, "
                    f"vision `{meta.get('vision', 'off')}`",
                ),
                ("Endpoint", f"`{provenance.endpoint}`"),
                (
                    "Prompt handling",
                    f"`{meta.get('prompt_mode', 'override')}` — the benchmark's doctor "
                    "prompt is used verbatim, which is what keeps the number in the "
                    "same units as the published table",
                ),
                (
                    "Turn limit",
                    f"{meta.get('total_inferences', '—')} inferences per case; a case "
                    "that has not committed by then is scored `no_commit`, and "
                    "`no_commit` counts as incorrect",
                ),
                (
                    "Tools bound",
                    "the benchmark's own action markers (ask / order test / commit "
                    "diagnosis), parsed by the harness",
                ),
                (
                    "Judge",
                    "a moderator model decides whether the committed free-text "
                    "diagnosis matches the reference, and a decline-judge separates a "
                    "refusal from a wrong answer",
                ),
                (
                    "Scoring rule",
                    "accuracy = correct / presented, upstream's formula unmodified",
                ),
            ],
            scoring_rule="accuracy = total_correct / total_presents (upstream formula)",
            tables=tables,
            exclusions=exclusions,
            judge=judge,
            provenance=provenance,
            sampling=sampling,
            baselines=BaselineSet(
                baselines=[],
                comparable=False,
                comparability_note=(
                    "No published AgentClinic baseline is registered in this harness, so "
                    "no comparison table is printed. The upstream paper reports "
                    "accuracies on this dataset, but under a different moderator and "
                    "with a different inference budget; transcribing those numbers here "
                    "without re-running under a matched protocol would produce a "
                    "comparison that looks rigorous and is not. This section stays empty "
                    "until the protocol is matched."
                ),
                published_protocol="see arXiv:2405.07960 — not replicated here",
            ),
            failures=_failures(scored, infra),
            limitations=_limitations(meta, exclusions, sampling, n_scored, status, partial_reason),
            reproduction=Reproduction(
                commands=[
                    "uv sync --extra dev",
                    (
                        "python -m tau2.health.agentclinic.run "
                        f"--dataset {meta.get('dataset', 'MedQA')} "
                        f"--limit {meta.get('limit', 100)} "
                        f"--prompt-mode {meta.get('prompt_mode', 'override')} "
                        f"--seed {meta.get('seed', 42)}"
                    ),
                    (
                        "python -m tau2.reporting.cli build "
                        f"results/whissle/agentclinic/{run_dir.name}"
                    ),
                ],
                environment={
                    "WHISSLE_BASE": provenance.base_url or "",
                    "harness commit": provenance.harness_commit or "unknown",
                    "repo commit at report time": provenance.repo_commit or "unknown",
                },
                notes=[
                    "`head` selection with a fixed limit reproduces the same scenario "
                    "set exactly.",
                    "The run provisions a throwaway agent and deletes it afterwards "
                    f"(`agent_deleted: {meta.get('agent_deleted')}`), so the agent id in "
                    "provenance will not resolve after the fact.",
                ],
            ),
            artifacts=artifacts_for(
                run_dir,
                [
                    ("RUN.json", "run configuration, written before the first case"),
                    ("SUMMARY.json", "run-level aggregation, written on completion"),
                    ("cases/", f"{len(cases)} per-case records with `diagnostics`"),
                    ("transcripts/", "human-readable consultation transcripts"),
                    ("REPORT.md", "this report"),
                    ("report.json", "machine-readable form of this report"),
                ],
            ),
            licence_note=(
                "AgentClinic, arXiv:2405.07960. Research measurement only — not a "
                "clinical evaluation of anything."
            ),
            warnings=warnings,
        )
        return report


def _ts_to_iso(ts: Any) -> str:
    s = str(ts or "")
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return ""


def _first_diag_provenance(cases: list[Any]) -> dict[str, Any]:
    for c in cases:
        p = dig(c, "diagnostics", "provenance", default=None)
        if isinstance(p, dict):
            return p
    return {}


def _failures(scored: list[Any], infra: list[Any]) -> list[FailureCategory]:
    out: list[FailureCategory] = []
    n = len(scored) or 1

    wrong = [c for c in scored if dig(c, "score", "outcome") == "incorrect"]
    if wrong:
        out.append(
            FailureCategory(
                key="incorrect",
                label="Committed to the wrong diagnosis",
                count=len(wrong),
                severity="high",
                denominator=n,
                description=(
                    "The agent named a diagnosis and it was not the reference one. "
                    "These are the cases worth reading: a confident wrong answer is the "
                    "failure mode with clinical consequences, and the transcript shows "
                    "which question was never asked."
                ),
                examples=[
                    FailureExample(
                        case_id=str(c.get("scenario_id")),
                        summary=(
                            f"said “{dig(c, 'score', 'doctor_diagnosis', default='?')}”, "
                            f"reference “{c.get('correct_diagnosis', '?')}” · "
                            f"{c.get('inferences_used', '?')} inferences · "
                            f"{len(c.get('tests_ordered') or [])} tests"
                        ),
                        evidence=str(dig(c, "score", "doctor_final_text", default=""))[:300],
                        artifact=f"cases/{c.get('scenario_id')}.json",
                    )
                    for c in wrong[:3]
                ],
            )
        )

    nocommit = [c for c in scored if dig(c, "score", "outcome") == "no_commit"]
    if nocommit:
        out.append(
            FailureCategory(
                key="no_commit",
                label="Never committed — ran out of budget",
                count=len(nocommit),
                severity="medium",
                denominator=n,
                description=(
                    "The inference budget expired before the agent named a diagnosis. "
                    "Scored as incorrect, but it is a different defect from being wrong: "
                    "it is an agent that gathers indefinitely and never concludes. Where "
                    "these cluster, the accuracy number is partly a measure of pacing."
                ),
                examples=[
                    FailureExample(
                        case_id=str(c.get("scenario_id")),
                        summary=(
                            f"used {c.get('inferences_used', '?')}/"
                            f"{c.get('max_inferences', '?')} inferences · "
                            f"{len(c.get('tests_ordered') or [])} tests ordered"
                        ),
                        evidence=str(dig(c, "score", "doctor_final_text", default=""))[:300],
                        artifact=f"cases/{c.get('scenario_id')}.json",
                    )
                    for c in nocommit[:3]
                ],
            )
        )

    declined = [c for c in scored if dig(c, "score", "declined")]
    if declined:
        out.append(
            FailureCategory(
                key="declined",
                label="Refused to commit",
                count=len(declined),
                severity="medium",
                denominator=n,
                description=(
                    "The agent explicitly declined to give a diagnosis. Under upstream's "
                    "formula this is scored the same as being wrong, which understates a "
                    "safety-appropriate refusal and overstates the capability gap."
                ),
                examples=[
                    FailureExample(
                        case_id=str(c.get("scenario_id")),
                        summary=str(dig(c, "score", "decline_reason", default="")),
                        evidence=str(dig(c, "score", "doctor_final_text", default=""))[:300],
                        artifact=f"cases/{c.get('scenario_id')}.json",
                    )
                    for c in declined[:2]
                ],
            )
        )

    deviations = [c for c in scored if dig(c, "score", "format_deviation")]
    if deviations:
        out.append(
            FailureCategory(
                key="format_deviation",
                label="Answer needed moderator normalisation",
                count=len(deviations),
                severity="low",
                denominator=n,
                description=(
                    "The committed answer did not match the expected marker format and "
                    "the moderator had to retry or normalise it. Not a reasoning "
                    "failure, but it is the amount of the score that depends on the "
                    "moderator being lenient."
                ),
                examples=[
                    FailureExample(
                        case_id=str(c.get("scenario_id")),
                        summary=f"moderator attempts: {dig(c, 'score', 'moderator_attempts', default='?')}",
                        evidence=str(dig(c, "score", "moderator_raw_text", default=""))[:200],
                        artifact=f"cases/{c.get('scenario_id')}.json",
                    )
                    for c in deviations[:2]
                ],
            )
        )

    if infra:
        out.append(
            FailureCategory(
                key="infra_fail",
                label="Transport failure — the case never ran",
                count=len(infra),
                severity="high",
                denominator=len(scored) + len(infra),
                description=(
                    "The consultation never produced a gradable outcome. Excluded from "
                    "the accuracy denominator; see the exclusions section."
                ),
                examples=[
                    FailureExample(
                        case_id=str(c.get("scenario_id")),
                        evidence=str(c.get("error") or "")[:220],
                        artifact=f"cases/{c.get('scenario_id')}.json",
                    )
                    for c in infra[:3]
                ],
            )
        )
    return out


def _limitations(
    meta: dict,
    exclusions: Exclusions,
    sampling: Sampling,
    n_scored: int,
    status: str,
    partial_reason: str,
) -> list[Limitation]:
    out = [
        Limitation(
            "run_incomplete",
            (
                "This report was generated over a run that had not finished. "
                + partial_reason
                + " Nothing here is a final figure."
            ),
            "high",
        )
        if status == "partial"
        else None,
        Limitation(
            "judge_independence",
            (
                "The moderator, the patient simulator and the decline-judge all ran on "
                "the same vendor's model API as the agent under test. Constant across "
                "runs it measures change honestly; against an external leaderboard it "
                "does not."
            ),
            "high",
        )
        if meta.get("judge_independent") is False
        else None,
        Limitation(
            "moderator_leniency",
            (
                "Accuracy depends on a moderator model deciding whether free text "
                "matches a reference diagnosis. The strict and lenient counts differ, "
                "and the headline uses the strict one — but the boundary is a model's "
                "judgement, not a string match."
            ),
            "medium",
        ),
        Limitation(
            "answer_options_leak",
            (
                "The upstream dataset presents the reference diagnosis among a small "
                "set of options in some configurations, which inflates accuracy for "
                "every model equally. It is left as-is so the number stays comparable, "
                "but it is not a measure of open-ended diagnostic ability."
            ),
            "medium",
        ),
        Limitation(
            "head_selection",
            (
                f"{sampling.n_selected} scenarios were taken from the head of a "
                f"{sampling.n_population}-scenario dataset rather than drawn at random."
            ),
            "medium",
        )
        if sampling.n_population
        else None,
        Limitation(
            "simulated_patient",
            (
                "The patient is a language model following a case card. It answers "
                "questions more cooperatively, more fluently and more consistently than "
                "a person in a waiting room, so the intake task here is easier than the "
                "product's real one."
            ),
            "high",
        ),
    ]
    return [x for x in out if x]
