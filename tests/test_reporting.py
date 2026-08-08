"""The reporting layer's contract.

Three things are under test and only the first is about formatting:

1. **the adapters degrade** — a partial, empty or corrupt run directory produces a
   report that says so, never a traceback;
2. **the honesty rules bite** — each rule is exercised by tampering with a report
   that passes and asserting the rule fires. A rule that cannot fail is decoration;
3. **history accumulates** — the index merges rather than overwrites, and refuses to
   diff two runs that did not measure the same thing the same way.

The fixtures are minimal hand-built run directories rather than copies of real
runs, so a change in a benchmark's artifact shape shows up as a failing adapter
test instead of quietly reshaping every expectation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tau2.reporting import build_report, honesty, publish, render_md, web_export
from tau2.reporting import index as index_mod
from tau2.reporting.adapters import BuildContext, adapter_for
from tau2.reporting.adapters.agentclinic import AgentClinicAdapter
from tau2.reporting.adapters.flow_sim import FlowSimAdapter
from tau2.reporting.adapters.medagent import MedAgentBenchAdapter
from tau2.reporting.adapters.patientagent import PatientAgentBenchAdapter
from tau2.reporting.model import PRELIMINARY_N_THRESHOLD

# ---------------------------------------------------------------------------
# fixtures: hand-built run directories
# ---------------------------------------------------------------------------


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def _pab_case(case_id: str, *, status="scored", scores=None, detail=""):
    scores = scores or {
        "task_completion": 5.0,
        "clinical_safety": 4.0,
        "workflow_accuracy": 4.0,
        "triage_quality": 3.0,
        "clinical_helpfulness": 4.0,
        "conversational_quality": 5.0,
    }
    return {
        "case_id": case_id,
        "status": status,
        "detail": detail,
        "rubric_scores": scores if status == "scored" else {},
        "aggregate_score": 4.1 if status == "scored" else None,
        "scenario": {"task_type": "medication_dosage_question", "severity_level": "mild"},
        "evaluation": {
            "rubric_results": {
                k: {"score": v, "pass": v >= 3, "explanation": f"because of {k}"}
                for k, v in (scores or {}).items()
            }
        },
        "diagnostics": {
            "schema": "tau2.health.diagnostics/v1",
            "provenance": {
                "agent_id": "agent-123",
                "base_url": "https://example.invalid/bot",
                "transport_endpoint": "POST /api/bench/agent-turn",
                "harness_commit": "abc1234",
                "captured_at": "2026-08-08T09:40:28+00:00",
            },
        },
    }


@pytest.fixture
def pab_with_exclusions(tmp_path: Path) -> Path:
    """40 attempted, 30 scored, 10 dropped for transport failure."""
    run = tmp_path / "results" / "whissle" / "patientagentbench" / "run_excl"
    for i in range(30):
        _write(run / "cases" / f"ok{i}.json", _pab_case(f"ok{i}"))
    for i in range(10):
        _write(
            run / "cases" / f"bad{i}.json",
            _pab_case(
                f"bad{i}",
                status="infra_fail",
                detail="[WHISSLE_INFRA_FAIL] HTTP 502: no completion",
            ),
        )
    _write(
        run / "summary.json",
        {
            "label": "Whissle",
            "mode": "harness_tools",
            "n_total": 40,
            "n_scored": 30,
            "n_excluded": 10,
            "excluded_breakdown": {"infra_fail": 10, "agent_error": 0},
            "aggregate": 4.10,
            "aggregate_ci": [3.9, 4.3],
            "pass_threshold": 3,
            "weights": {"task_completion": 1.0, "clinical_safety": 2.0},
            "dimensions": {
                "task_completion": {
                    "n": 30,
                    "mean": 5.0,
                    "mean_ci": [4.9, 5.0],
                    "pass_rate": 100.0,
                    "pass_rate_ci": [88.0, 100.0],
                    "weight": 1.0,
                },
                "clinical_safety": {
                    "n": 30,
                    "mean": 4.0,
                    "mean_ci": [3.8, 4.2],
                    "pass_rate": 96.0,
                    "pass_rate_ci": [82.0, 99.0],
                    "weight": 2.0,
                },
            },
            "sampling": {
                "n_population": 120,
                "n_requested": 40,
                "n_selected": 40,
                "seed": 42,
                "strata_keys": ["task_type"],
            },
            "judge": {
                "judge_provider": "whissle",
                "judge_model": "default",
                "judge_independent": False,
                "jury_k": 1,
                "judge_independence_note": "the grader and the agent share a vendor.",
                "judge_calls": 300,
                "judge_cost_usd": 0.05,
            },
            "provenance": {"generated_at": "2026-08-08T09:40:27+00:00"},
        },
    )
    return run


@pytest.fixture
def mab_full(tmp_path: Path) -> Path:
    """A benchmark that *does* have published baselines, at less than the published N."""
    run = tmp_path / "results" / "whissle" / "medagentbench" / "run_mab"
    for i in range(40):
        _write(
            run / "tasks" / f"task1_{i}.json",
            {
                "task_id": f"task1_{i}",
                "category": "task1",
                "status": "completed",
                "correct": i % 2 == 0,
                "infra_fail": False,
                "turns": [{"agent_reply": "GET /Patient"}],
                "findings": [],
                "diagnostics": {"provenance": {"harness_commit": "abc1234"}},
            },
        )
    _write(
        run / "SUMMARY.json",
        {
            "suite": "medagentbench",
            "mode": "brain-parity",
            "generated_at": "2026-08-08T09:36:34+00:00",
            "run": {
                "base": "https://example.invalid/bot",
                "agent_id": "agent-456",
                "endpoint": "/api/bench/agent-turn",
                "max_round": 8,
                "grader": "builtin",
                "write_check": "execute",
                "system_mode": "neutral",
                "filters": {"limit": 40},
            },
            "n_tasks_attempted": 40,
            "n_scored": 40,
            "n_infra_fail": 0,
            "overall": {"n": 40, "correct": 20, "success_rate_pct": 50.0},
            "query": {"n": 20, "correct": 14, "success_rate_pct": 70.0},
            "action": {"n": 20, "correct": 6, "success_rate_pct": 30.0},
            "per_category": {"task1": {"n": 40, "correct": 20, "success_rate_pct": 50.0}},
            "write_integrity": {
                "n_action_episodes": 20,
                "write_check_mode": "execute",
                "total_writes_emitted": 5,
                "said_but_did_not_write": {"n": 0, "rate_pct": 0.0, "task_ids": []},
                "wrote_but_did_not_say": {"n": 0, "rate_pct": 0.0, "task_ids": []},
                "emitted_nonconformant_fhir": {"n": 0, "rate_pct": 0.0, "task_ids": []},
            },
            "status_counts": {"completed": 40},
            "finding_counts": {},
            "published_baselines_full_300": {
                "Claude 3.5 Sonnet v2": {"overall": 69.67, "query": 85.33, "action": 54.0},
                "GPT-4o": {"overall": 64.0},
            },
        },
    )
    return run


@pytest.fixture
def ac_preliminary(tmp_path: Path) -> Path:
    """A run whose N is below the preliminary threshold, with no baseline registered."""
    run = tmp_path / "results" / "whissle" / "agentclinic" / "run_small"
    for i in range(5):
        _write(
            run / "cases" / f"MedQA-{i}.json",
            {
                "scenario_id": f"MedQA-{i}",
                "correct_diagnosis": "gout",
                "infra_fail": False,
                "inferences_used": 4,
                "max_inferences": 20,
                "tests_ordered": [],
                "score": {
                    "outcome": "correct" if i < 3 else "incorrect",
                    "correctness": i < 3,
                    "doctor_diagnosis": "gout" if i < 3 else "sepsis",
                    "doctor_final_text": "I think this is gout.",
                    "declined": False,
                    "format_deviation": False,
                },
                "diagnostics": {"provenance": {"harness_commit": "abc1234"}},
            },
        )
    _write(
        run / "RUN.json",
        {
            "dataset": "MedQA",
            "ts": "20260808T092952Z",
            "mode": "text",
            "limit": 5,
            "sample": "head",
            "seed": 42,
            "prompt_mode": "override",
            "vision": "off",
            "total_inferences": 20,
            "dataset_size": 107,
            "selected_ids": [0, 1, 2, 3, 4],
        },
    )
    _write(run / "SUMMARY.json", {"judge_independent": True, "judge_provider": "external"})
    return run


@pytest.fixture
def flow_run(tmp_path: Path) -> Path:
    run = tmp_path / "results" / "whissle" / "flow_sim" / "headache_enrollment"
    for i in range(4):
        _write(
            run / f"hx_{i}_20260808T000000Z.session.json",
            {
                "task_id": f"hx_{i}",
                "agent_type": "headache_enrollment",
                "ts": "20260808T000000Z",
                "agent_id": "agent-789",
                "scenario": "full_intake",
                "outcome": {
                    "task_success": i < 3,
                    "task_success_reason": "the caller got what they came for"
                    if i < 3
                    else "intake ended early",
                    "ended": True,
                    "final_state": "done",
                },
                "metadata": {"num_turns": 8, "infra_fail": False},
                "turns": [{"n": 1}],
                "analyzer_findings": []
                if i < 3
                else [
                    {
                        "type": "premature_termination",
                        "severity": "medium",
                        "detail": "closed before intake was complete",
                        "state": "headache_profile",
                    }
                ],
            },
        )
    _write(
        run / "SUMMARY.json",
        {
            "agent_type": "headache_enrollment",
            "ts": "20260808T000000Z",
            "sessions": 4,
            "sessions_ran": 4,
            "sessions_infra": 0,
            "sessions_ended_cleanly": 4,
            "task_success": 3,
            "finding_counts_by_type": {"premature_termination": 1},
            "coverage": {
                "states_total": 10,
                "states_visited": 10,
                "states_unvisited": [],
                "transitions_total": 24,
                "transitions_fired": 17,
                "transitions_unfired": ["t2"],
            },
            "sessions_detail": [
                {
                    "task_id": f"hx_{i}",
                    "scenario": "full_intake",
                    "num_turns": 8,
                    "ended": True,
                    "task_success": i < 3,
                    "final_state": "done",
                    "high_severity": 0,
                }
                for i in range(4)
            ],
        },
    )
    return run


def _ctx(run: Path) -> BuildContext:
    root = run.parents[1]  # …/results/whissle
    return BuildContext(repo_root=root.parents[1], results_root=root)


def _build(run: Path):
    adapter = adapter_for(run)
    assert adapter is not None, f"no adapter recognised {run}"
    report = adapter.build(run, _ctx(run))
    return report, render_md.render(report)


# ---------------------------------------------------------------------------
# adapters: the happy paths
# ---------------------------------------------------------------------------


def test_adapter_detection_is_specific():
    """Each adapter claims its own runs and nobody else's."""
    assert PatientAgentBenchAdapter.benchmark == "patientagentbench"
    assert MedAgentBenchAdapter.benchmark == "medagentbench"
    assert AgentClinicAdapter.benchmark == "agentclinic"
    assert FlowSimAdapter.benchmark == "flow_sim"


def test_run_with_exclusions_reports_them_next_to_the_score(pab_with_exclusions):
    report, md = _build(pab_with_exclusions)
    assert report.exclusions.n_excluded == 10
    assert report.exclusions.rate_pct == pytest.approx(25.0)
    assert not honesty.audit(report, md)

    # the exclusion rate travels with the number, in the abstract and the glance table
    abstract = md.split("## At a glance")[0]
    assert "10/40 excluded" in abstract or "10 of 40" in abstract
    assert "25.0%" in abstract
    # …and the verbatim reason is quoted from the artifacts, not paraphrased
    assert "HTTP 502" in md
    # …and the bounding analysis is computed, not asserted
    assert "if every excluded unit had scored at the floor" in md


def test_exclusion_bounds_are_arithmetic_not_vibes(pab_with_exclusions):
    """4.10 over 30 of 40. Floor 1.0 → (4.10*30 + 1.0*10)/40 = 3.325.
    Ceiling 5.0 → (4.10*30 + 5.0*10)/40 = 4.325. Both are bounds, not estimates."""
    report, md = _build(pab_with_exclusions)
    from tau2.reporting.render_md import _bounds

    lo, hi = _bounds(report)
    assert lo == round((4.10 * 30 + 1.0 * 10) / 40, 2)
    assert hi == round((4.10 * 30 + 5.0 * 10) / 40, 2)
    # the bound is wider than the sampling CI [3.9, 4.3] — which is the point
    assert lo < report.headline.ci[0] and hi > report.headline.ci[1]
    assert f"**{lo}**" in md and f"**{hi}**" in md


def test_run_with_baselines_states_what_is_not_comparable(mab_full):
    report, md = _build(mab_full)
    assert report.baselines.any
    assert report.baselines.comparable is False
    assert "Not directly comparable" in md
    assert "scored 40 tasks" in md and "300" in md
    # vendor names appear, and only inside the sanctioned span
    assert not honesty.check_providers_markdown(md, report)
    assert "GPT-4o" in md


def test_run_without_baselines_says_so_explicitly(ac_preliminary):
    report, md = _build(ac_preliminary)
    assert not report.baselines.any
    assert "No published AgentClinic baseline is registered" in md
    assert "An empty comparison section is a result" in md
    assert not honesty.audit(report, md)


def test_small_n_run_is_labelled_preliminary_everywhere(ac_preliminary):
    report, md = _build(ac_preliminary)
    assert report.n_scored < PRELIMINARY_N_THRESHOLD
    assert report.preliminary
    assert "PRELIMINARY" in md.split("## Abstract")[0]  # in the banner, above the fold
    assert "PRELIMINARY" in honesty.qualifier(report)
    assert not honesty.audit(report, md)


def test_flow_run_builds_and_carries_coverage(flow_run):
    report, md = _build(flow_run)
    assert report.headline.value == pytest.approx(75.0)
    assert report.headline.n == 4
    assert report.series == "flow_sim:headache_enrollment"
    assert "transitions" in md.lower()
    assert "`t2`" in md  # the unfired transition is named, not summarised away
    assert not honesty.audit(report, md)


def test_failure_analysis_quotes_the_real_artifacts(flow_run):
    report, md = _build(flow_run)
    keys = {f.key for f in report.failures}
    assert "goal_not_met" in keys
    assert "closed before intake was complete" in md  # the finding's own words
    assert "intake ended early" in md  # the grader's own words


# ---------------------------------------------------------------------------
# degradation: partial and malformed run directories
# ---------------------------------------------------------------------------


def test_partial_run_without_summary_degrades_rather_than_crashing(ac_preliminary):
    """The common case: a report asked for while the run is still executing."""
    (ac_preliminary / "SUMMARY.json").unlink()
    report, md = _build(ac_preliminary)
    assert report.status == "partial"
    assert "SUMMARY.json has not been written" in report.partial_reason
    assert "Partial run" in md
    assert report.preliminary  # a partial run is never a settled number
    assert report.headline.value is not None  # …but it still reports what it has


def test_corrupt_case_file_is_reported_not_raised(pab_with_exclusions):
    (pab_with_exclusions / "cases" / "ok0.json").write_text("{not json", encoding="utf-8")
    report, md = _build(pab_with_exclusions)
    assert report.status == "partial"
    assert any("unreadable" in w for w in report.warnings)
    assert "Generator warnings" in md


def test_empty_run_directory_does_not_crash(tmp_path):
    run = tmp_path / "results" / "whissle" / "patientagentbench" / "empty"
    (run / "cases").mkdir(parents=True)
    report, md = _build(run)
    assert report.status == "partial"
    assert report.headline.value is None
    assert md  # a report is still produced


def test_summary_with_null_percentages_renders_a_dash(mab_full):
    """An older harness writes `null` where a newer one writes a number."""
    s = json.loads((mab_full / "SUMMARY.json").read_text())
    s["per_category"]["task1"]["success_rate_pct"] = None
    s["query"]["success_rate_pct"] = None
    (mab_full / "SUMMARY.json").write_text(json.dumps(s), encoding="utf-8")
    report, md = _build(mab_full)
    assert "—" in md
    assert report.headline.value is not None


def test_unrecognised_directory_returns_no_adapter(tmp_path):
    d = tmp_path / "not-a-run"
    d.mkdir()
    assert adapter_for(d) is None


# ---------------------------------------------------------------------------
# the honesty rules, exercised by tampering
# ---------------------------------------------------------------------------


def test_R1_bare_headline_number_is_rejected(pab_with_exclusions):
    report, md = _build(pab_with_exclusions)
    assert not honesty.check_headline_annotations(md, report)
    tampered = md + "\n\nOur aggregate is 4.10, which is excellent.\n"
    viols = honesty.check_headline_annotations(tampered, report)
    assert any(v.rule == "R1_headline_requires_n" for v in viols)


def test_R1_a_report_that_never_states_its_number_is_rejected(pab_with_exclusions):
    report, _ = _build(pab_with_exclusions)
    viols = honesty.check_headline_annotations("# A report with no number\n", report)
    assert any(v.rule == "R1_headline_requires_n" for v in viols)


def test_R2_non_independent_judge_must_be_disclosed_at_the_number(pab_with_exclusions):
    report, md = _build(pab_with_exclusions)
    assert report.judge.needs_disclosure
    tampered = md + "\n\nHeadline: 4.10 (N = 30 · 10/40 excluded).\n"
    viols = honesty.check_headline_annotations(tampered, report)
    assert any(v.rule == "R2_judge_independence_disclosed" for v in viols)


def test_R2_stripping_the_disclosure_from_the_document_is_caught(pab_with_exclusions):
    report, md = _build(pab_with_exclusions)
    stripped = md.replace("judge not independent", "").replace("NOT independent", "")
    viols = honesty.check_judge_disclosure(stripped, report)
    assert any(v.rule == "R2_judge_independence_disclosed" for v in viols)


def test_R2_deterministic_grading_is_not_a_failed_independence_check(mab_full):
    report, md = _build(mab_full)
    assert report.judge.independent is None  # absence of a judge, not a bad judge
    assert not report.judge.needs_disclosure
    assert not honesty.audit(report, md)


def test_R3_exclusion_rate_must_accompany_the_number(pab_with_exclusions):
    report, md = _build(pab_with_exclusions)
    tampered = md + "\n\nScore 4.10 (N = 30 · judge not independent).\n"
    viols = honesty.check_headline_annotations(tampered, report)
    assert any(v.rule == "R3_exclusion_rate_adjacent" for v in viols)


def test_R3_exclusions_without_a_reason_breakdown_are_rejected(pab_with_exclusions):
    report, _ = _build(pab_with_exclusions)
    report.exclusions.breakdown = {}
    viols = honesty.check_structure(report)
    assert any(v.rule == "R3_exclusion_rate_adjacent" for v in viols)


def test_R3_exclusion_arithmetic_must_close(pab_with_exclusions):
    report, _ = _build(pab_with_exclusions)
    report.exclusions.n_scored = 25  # 25 + 10 != 40
    viols = honesty.check_structure(report)
    assert any("does not close" in v.message for v in viols)


def test_R4_preliminary_label_cannot_be_removed(ac_preliminary):
    report, md = _build(ac_preliminary)
    stripped = md.replace("PRELIMINARY", "final")
    viols = honesty.check_preliminary_label(stripped, report)
    assert any(v.rule == "R4_preliminary_labelled" for v in viols)


def test_R4_threshold_is_the_only_thing_that_decides(pab_with_exclusions):
    report, _ = _build(pab_with_exclusions)
    assert report.n_scored == 30 == PRELIMINARY_N_THRESHOLD
    assert not report.preliminary
    report.exclusions.n_scored = PRELIMINARY_N_THRESHOLD - 1
    assert report.preliminary


def test_R5_provider_name_in_agent_facing_text_is_rejected(mab_full):
    report, md = _build(mab_full)
    assert not honesty.check_providers_markdown(md, report)
    report.what_measured = "Whether our Claude-powered agent can operate an EHR."
    viols = honesty.check_providers_structured(report)
    assert any(v.rule == "R5_no_provider_names" for v in viols)


def test_R5_provider_names_survive_inside_the_baseline_span(mab_full):
    report, md = _build(mab_full)
    assert "GPT-4o" in md and "Claude 3.5 Sonnet v2" in md
    assert not honesty.check_providers_markdown(md, report)
    # …but the same names outside the span are caught
    assert honesty.check_providers_markdown(md + "\nWe use GPT-4o.\n", report)


def test_R5_verbatim_error_payloads_are_redacted_not_dropped():
    raw = 'ModelError: gemini call failed after 6 attempts: HTTP 502'
    out = honesty.redact_providers(raw)
    assert "gemini" not in out.lower()
    assert honesty.REDACTION in out
    assert "HTTP 502" in out  # the evidence survives


def test_R6_baselines_without_a_comparability_statement_are_rejected(mab_full):
    report, _ = _build(mab_full)
    report.baselines.comparability_note = ""
    viols = honesty.check_structure(report)
    assert any(v.rule == "R6_comparability_stated" for v in viols)


def test_every_rule_appears_in_the_compliance_appendix(pab_with_exclusions):
    report, md = _build(pab_with_exclusions)
    for rule in honesty.ALL_RULES:
        assert rule in md
    assert "FAIL" not in md.split("Appendix B")[1]


# ---------------------------------------------------------------------------
# idempotence
# ---------------------------------------------------------------------------


def test_generation_is_idempotent(pab_with_exclusions):
    """Same inputs, same bytes — the generator can be re-run over any run dir."""
    _, first = _build(pab_with_exclusions)
    _, second = _build(pab_with_exclusions)
    assert first == second


def test_build_report_public_helper(pab_with_exclusions):
    report, md = build_report(pab_with_exclusions)
    assert report.benchmark == "patientagentbench"
    assert md.startswith("# PatientAgentBench")


# ---------------------------------------------------------------------------
# the cross-run index
# ---------------------------------------------------------------------------


def _entry(run_id, bench, series, value, n, **kw):
    e = {
        "run_id": run_id,
        "benchmark": bench,
        "series_key": series,
        "benchmark_title": bench,
        "mode": "text",
        "date": kw.pop("date", "2026-08-01"),
        "metric_key": "success_rate",
        "metric_unit": "pct",
        "value": value,
        "value_formatted": f"{value:.1f}%",
        "n_scored": n,
        "n_attempted": n,
        "n_excluded": 0,
        "exclusion_rate_pct": 0.0,
        "judge_independent": None,
        "preliminary": n < PRELIMINARY_N_THRESHOLD,
        "artifacts_present": True,
    }
    e.update(kw)
    return e


def test_index_accumulates_and_never_drops_a_run(tmp_path):
    root = tmp_path / "results" / "whissle"
    root.mkdir(parents=True)
    index_mod.write(root, [_entry("a/1", "b", "b:text", 50.0, 100)])
    index_mod.write(root, [_entry("a/2", "b", "b:text", 60.0, 100, date="2026-08-02")])
    idx = json.loads((root / "index.json").read_text())
    assert {r["run_id"] for r in idx["runs"]} == {"a/1", "a/2"}
    # the older run's artifacts are gone from disk; the record is not
    assert idx["runs"][0]["artifacts_present"] is False


def test_index_regenerates_over_an_existing_index_without_duplicating(tmp_path):
    root = tmp_path / "results" / "whissle"
    root.mkdir(parents=True)
    e = _entry("a/1", "b", "b:text", 50.0, 100)
    index_mod.write(root, [e])
    index_mod.write(root, [dict(e, value=55.0, value_formatted="55.0%")])
    idx = json.loads((root / "index.json").read_text())
    assert idx["n_runs"] == 1
    assert idx["runs"][0]["value"] == 55.0


def test_regression_diffs_only_within_a_series(tmp_path):
    runs = [
        _entry("f/appt_v1", "flow_sim", "flow_sim:appointments", 20.0, 40),
        _entry("f/appt_v2", "flow_sim", "flow_sim:appointments", 45.0, 40, date="2026-08-02"),
        _entry("f/car_v1", "flow_sim", "flow_sim:car_rental", 90.0, 40, date="2026-08-03"),
    ]
    view = index_mod.regression_view(runs)
    assert set(view) == {"flow_sim:appointments", "flow_sim:car_rental"}
    appts = view["flow_sim:appointments"]
    assert appts[1]["delta_vs_previous"] == pytest.approx(25.0)
    # the car-rental series starts fresh; it is never diffed against appointments
    assert view["flow_sim:car_rental"][0]["delta_vs_previous"] is None


def test_regression_refuses_to_diff_across_a_changed_judge(tmp_path):
    runs = [
        _entry("x/1", "b", "b:text", 50.0, 100, judge_independent=False),
        _entry("x/2", "b", "b:text", 70.0, 100, judge_independent=True, date="2026-08-02"),
    ]
    rows = index_mod.regression_view(runs)["b:text"]
    assert rows[1]["delta_vs_previous"] is None
    assert "judge independence changed" in rows[1]["comparability_note"]


def test_regression_refuses_to_diff_a_smoke_run_against_a_real_one():
    runs = [
        _entry("x/smoke", "b", "b:text", 100.0, 2),
        _entry("x/real", "b", "b:text", 54.0, 100, date="2026-08-02"),
    ]
    rows = index_mod.regression_view(runs)["b:text"]
    assert rows[1]["delta_vs_previous"] is None
    assert "not of the same order" in rows[1]["comparability_note"]


def test_index_markdown_shows_n_exclusions_and_judge_per_row():
    idx = index_mod.merge(
        None,
        [_entry("a/1", "b", "b:text", 50.0, 100, n_excluded=13, exclusion_rate_pct=11.5,
                judge_independent=False)],
        Path("/nonexistent"),
    )
    md = index_mod.render_markdown(idx)
    assert "13 (11.5%)" in md
    assert "**no**" in md  # judge independence
    assert "50.0%" in md


# ---------------------------------------------------------------------------
# the website export
# ---------------------------------------------------------------------------


def test_export_row_carries_every_honesty_field(pab_with_exclusions):
    report, _ = _build(pab_with_exclusions)
    row = web_export.row_for(report)
    assert row["sampleSize"] == 30
    assert row["attempted"] == 40 and row["excluded"] == 10
    assert row["exclusionRatePct"] == pytest.approx(25.0)
    assert row["judgeIndependent"] is False
    assert "excluded" in row["note"]
    assert "independent" in row["note"]
    assert row["artifact"].startswith("results/whissle/")


def test_export_rescales_a_rubric_score_and_says_that_it_did(pab_with_exclusions):
    report, _ = _build(pab_with_exclusions)
    row = web_export.row_for(report)
    assert row["scoreKind"] == "normalised_rubric"
    assert row["scoreNative"] == 4.10
    assert row["scoreNativeScale"] == "1–5"
    assert row["score"] == pytest.approx(77.5)  # (4.10-1)/4*100
    assert "not a pass rate" in row["note"]
    assert "passed" not in row  # a rescaled rubric is not a count of passes


def test_export_passed_matches_score_for_rate_metrics(mab_full):
    report, _ = _build(mab_full)
    row = web_export.row_for(report)
    assert row["passed"] == 20 and row["sampleSize"] == 40
    assert abs(row["passed"] / row["sampleSize"] * 100 - row["score"]) < 0.5


def test_export_maps_baselines_onto_the_frontend_allowlist(mab_full):
    report, _ = _build(mab_full)
    row = web_export.row_for(report)
    assert {b["label"] for b in row["baselines"]} == {"GPT-4o", "Claude-3.5-Sonnet"}
    # the full published table survives alongside it
    assert any(b["label"] == "Claude 3.5 Sonnet v2" for b in row["baselinesAll"])


def test_export_validation_catches_a_missing_n(mab_full):
    report, _ = _build(mab_full)
    export = web_export.build([report], {})
    assert not web_export.validate(export)
    export["rows"][0]["sampleSize"] = 0
    assert any(v.rule == "R1_headline_requires_n" for v in web_export.validate(export))


def test_export_validation_catches_an_unlabelled_small_sample(ac_preliminary, mab_full):
    report, _ = _build(mab_full)
    export = web_export.build([report], {})
    export["rows"][0]["sampleSize"] = 5
    export["rows"][0]["preliminary"] = False
    assert any(v.rule == "R4_preliminary_labelled" for v in web_export.validate(export))


def test_export_validation_catches_a_buried_exclusion(pab_with_exclusions):
    report, _ = _build(pab_with_exclusions)
    export = web_export.build([report], {})
    assert not web_export.validate(export)
    export["rows"][0]["note"] = "A very good score."
    viols = web_export.validate(export)
    assert any(v.rule == "R3_exclusion_rate_adjacent" for v in viols)
    assert any(v.rule == "R2_judge_independence_disclosed" for v in viols)


def test_export_validation_catches_a_provider_name_in_user_facing_text(mab_full):
    report, _ = _build(mab_full)
    export = web_export.build([report], {})
    export["rows"][0]["note"] = "Powered by Gemini."
    assert any(v.rule == "R5_no_provider_names" for v in web_export.validate(export))


def test_export_flow_rows_are_countable(flow_run):
    report, _ = _build(flow_run)
    export = web_export.build([report], {})
    assert export["rows"] == []  # the flow suite is not a leaderboard row
    ids = {r["id"] for r in export["flowRows"]}
    assert "flow-sim-headache-enrollment-task-success" in ids
    row = next(r for r in export["flowRows"] if r["id"].endswith("task-success"))
    assert row["value"] == "3 / 4" and row["passed"] == 3 and row["sampleSize"] == 4


def test_export_methodology_satisfies_the_publishing_gate(mab_full, pab_with_exclusions):
    reports = [_build(mab_full)[0], _build(pab_with_exclusions)[0]]
    export = web_export.build(reports, {})
    terms = {m["term"] for m in export["methodology"]}
    assert "What is under test" in terms
    joined = " ".join(m["detail"] for m in export["methodology"]).lower()
    for required in ("database state", "concurrency", "user simulator", "pass^1"):
        assert required in joined
    assert all(len(m["detail"]) > 20 for m in export["methodology"])
    assert not web_export.validate(export)


def test_export_honest_negatives_are_generated_from_the_runs(pab_with_exclusions):
    report, _ = _build(pab_with_exclusions)
    export = web_export.build([report], {})
    titles = " ".join(n["title"] for n in export["honestNegatives"])
    assert "never completed" in titles
    assert "graded our own homework" in titles


def test_export_history_comes_from_the_index(flow_run):
    report, _ = _build(flow_run)
    entry = index_mod.entry_for(report)
    idx = index_mod.merge(None, [entry], Path("/nonexistent"))
    export = web_export.build([report], idx)
    assert "flow_sim:headache_enrollment" in export["history"]
    assert export["history"]["flow_sim:headache_enrollment"][0]["sampleSize"] == 4


# ---------------------------------------------------------------------------
# publishing to the results store
# ---------------------------------------------------------------------------


def test_envelope_carries_the_mandatory_honesty_fields(pab_with_exclusions):
    report, md = _build(pab_with_exclusions)
    env = publish.run_envelope(report, md)
    for key in ("runId", "sampleSize", "attempted", "excluded", "exclusionRatePct"):
        assert key in env, key
    assert env["sampleSize"] == 30
    assert env["attempted"] == 40 and env["excluded"] == 10
    assert "independent" in env["judge"]
    assert env["judge"]["independent"] is False
    assert env["judge"]["note"]
    assert not publish.validate_envelope(env)


def test_envelope_is_idempotent_on_run_id(pab_with_exclusions):
    report, md = _build(pab_with_exclusions)
    a = publish.run_envelope(report, md)
    b = publish.run_envelope(report, md)
    assert a["runId"] == b["runId"] == "patientagentbench/run_excl"
    # only the generation timestamp may differ between two builds of one run
    a.pop("generatedAt"), b.pop("generatedAt")
    assert a == b


def test_store_rejects_a_run_with_no_sample_size(mab_full):
    report, md = _build(mab_full)
    env = publish.run_envelope(report, md)
    env["sampleSize"] = None
    assert any(v.rule == "R1_headline_requires_n" for v in publish.validate_envelope(env))


def test_store_rejects_a_run_that_omits_exclusions_entirely(mab_full):
    """Absent and zero must not be the same value."""
    report, md = _build(mab_full)
    env = publish.run_envelope(report, md)
    assert env["excluded"] == 0  # present, and zero
    del env["excluded"]
    assert any(v.rule == "R3_exclusion_rate_adjacent" for v in publish.validate_envelope(env))


def test_store_rejects_a_run_that_omits_judge_independence(mab_full):
    report, md = _build(mab_full)
    env = publish.run_envelope(report, md)
    assert env["judge"]["independent"] is None  # deterministic grading, explicitly
    assert not publish.validate_envelope(env)
    del env["judge"]["independent"]
    assert any(
        v.rule == "R2_judge_independence_disclosed" for v in publish.validate_envelope(env)
    )


def test_store_rejects_non_closing_exclusion_arithmetic(pab_with_exclusions):
    report, md = _build(pab_with_exclusions)
    env = publish.run_envelope(report, md)
    env["excluded"] = 5
    assert any("does not close" in v.message for v in publish.validate_envelope(env))


def test_envelope_carries_the_bounding_analysis(pab_with_exclusions):
    report, md = _build(pab_with_exclusions)
    env = publish.run_envelope(report, md)
    assert env["exclusionBounds"]["floor"] == round((4.10 * 30 + 1.0 * 10) / 40, 2)
    assert env["exclusionBounds"]["ceiling"] == round((4.10 * 30 + 5.0 * 10) / 40, 2)
    assert "bounds, not" in env["exclusionBounds"]["note"]


def test_envelope_carries_the_report_and_sample_cases(mab_full):
    report, md = _build(mab_full)
    env = publish.run_envelope(report, md)
    assert env["reportMarkdown"] == md  # per-run detail with no frontend deploy
    assert env["sampleCases"], "a page that cannot show a real case is a scoreboard"
    ids = [c["case_id"] for c in env["sampleCases"]]
    assert len(ids) == len(set(ids))
    assert any(c["is_success"] for c in env["sampleCases"])
    assert any(not c["is_success"] for c in env["sampleCases"])


def test_sample_cases_are_deterministic(mab_full):
    a, _ = _build(mab_full)
    b, _ = _build(mab_full)
    assert [c.case_id for c in a.sample_cases] == [c.case_id for c in b.sample_cases]


# --- R7: a comparator is named and sourced ---------------------------------


def test_R7_baselines_are_named_and_sourced(mab_full):
    report, md = _build(mab_full)
    assert not honesty.check_baseline_labels(report)
    env = publish.run_envelope(report, md)
    for b in env["baselines"]:
        assert b["label"] and b["source"] and b["score"] is not None
        assert b["display"].startswith(b["label"])
        assert b["source"] in b["display"]


def test_R7_a_vague_comparator_is_rejected(mab_full):
    report, _ = _build(mab_full)
    report.baselines.baselines[0].name = "Frontier text agent"
    viols = honesty.check_baseline_labels(report)
    assert any(v.rule == "R7_baseline_named" for v in viols)
    assert "describes a comparator instead of naming one" in viols[0].message


def test_R7_an_unsourced_comparator_is_rejected(mab_full):
    report, _ = _build(mab_full)
    report.baselines.baselines[0].source = ""
    assert any(
        "no published source" in v.message for v in honesty.check_baseline_labels(report)
    )


def test_R7_reaches_the_rendered_baseline_table(mab_full):
    report, md = _build(mab_full)
    assert "Published in" in md
    assert "MedAgentBench, NEJM AI 2025" in md
    assert "**unsourced**" not in md


# --- the client ------------------------------------------------------------


class _FakeStore(publish.BenchmarkStore):
    def __init__(self, fail_on=None):
        super().__init__(base="https://example.invalid", api_key="k")
        self.sent: list[dict] = []
        self.index_sent: list[dict] = []
        self._fail_on = fail_on

    def publish_run(self, envelope):
        self.sent.append(envelope)
        if self._fail_on and envelope["runId"] == self._fail_on:
            return publish.PublishResult(envelope["runId"], False, 500, "boom")
        return publish.PublishResult(envelope["runId"], True, 200, "upserted")

    def publish_index(self, envelope):
        self.index_sent.append(envelope)
        return publish.PublishResult("index", True, 200, "ok")


def test_publish_sends_each_run_and_then_the_history(mab_full, pab_with_exclusions):
    pairs = [_build(mab_full), _build(pab_with_exclusions)]
    store = _FakeStore()
    idx = index_mod.merge(None, [index_mod.entry_for(r) for r, _ in pairs], Path("/nope"))
    results, viols = publish.publish_reports(pairs, idx, store=store)
    assert not viols
    assert [e["runId"] for e in store.sent] == [
        "medagentbench/run_mab",
        "patientagentbench/run_excl",
    ]
    assert store.index_sent and store.index_sent[0]["schema"] == publish.INDEX_SCHEMA_WIRE
    assert all(r.ok for r in results)


def test_publish_does_not_send_history_when_a_run_failed(mab_full):
    store = _FakeStore(fail_on="medagentbench/run_mab")
    results, _ = publish.publish_reports([_build(mab_full)], {"n_runs": 1}, store=store)
    assert not results[0].ok
    assert store.index_sent == []


def test_publish_sends_nothing_when_validation_fails(mab_full):
    report, md = _build(mab_full)
    report.baselines.baselines[0].name = "Leading commercial agent"
    store = _FakeStore()
    results, viols = publish.publish_reports([(report, md)], None, store=store)
    assert results == []
    assert store.sent == []
    assert any(v.rule == "R7_baseline_named" for v in viols)


def test_publish_reports_a_missing_api_key_rather_than_hanging(monkeypatch, mab_full):
    monkeypatch.delenv("WHISSLE_API_KEY", raising=False)
    monkeypatch.delenv("WHISSLE_BASE", raising=False)
    results, viols = publish.publish_reports([_build(mab_full)], None)
    assert results == []
    assert any(v.rule == "W2_store_not_configured" for v in viols)


def test_publish_dry_run_touches_no_network(mab_full):
    results, viols = publish.publish_reports([_build(mab_full)], None, dry_run=True)
    assert results and results[0].detail == "dry-run"
    assert not viols


def test_history_envelope_carries_the_comparability_verdicts():
    runs = [
        _entry("x/1", "b", "b:text", 50.0, 100, judge_independent=False),
        _entry("x/2", "b", "b:text", 70.0, 100, judge_independent=True, date="2026-08-02"),
    ]
    idx = index_mod.merge(None, runs, Path("/nope"))
    env = publish.index_envelope(idx)
    assert env["schema"] == publish.INDEX_SCHEMA_WIRE
    series = env["series"]["b:text"]
    assert series[1]["delta_vs_previous"] is None
    assert "judge independence changed" in series[1]["comparability_note"]


# ---------------------------------------------------------------------------
# the flow suite's accumulating directory
# ---------------------------------------------------------------------------


def test_flow_counts_come_from_the_sidecars_not_a_stale_summary(flow_run):
    """The directory accumulates; SUMMARY.json describes only the last invocation.

    A one-scenario re-run of a passing case must not turn a 4-scenario suite into
    "100% (N = 1)".
    """
    summary = json.loads((flow_run / "SUMMARY.json").read_text())
    summary.update({"sessions": 1, "sessions_ran": 1, "task_success": 1, "ts": "20260809T000000Z"})
    (flow_run / "SUMMARY.json").write_text(json.dumps(summary), encoding="utf-8")

    report, md = _build(flow_run)
    assert report.headline.n == 4          # not 1
    assert report.headline.value == pytest.approx(75.0)
    assert report.status == "partial"
    assert "describes a 1-session invocation" in report.partial_reason
    # the out-of-scope coverage roll-up is withheld rather than quoted
    assert "t2" not in md
    assert not honesty.audit(report, md)


def test_flow_uses_the_summary_when_it_is_in_scope(flow_run):
    report, md = _build(flow_run)
    assert report.status == "complete"
    assert "`t2`" in md  # the coverage roll-up is in scope, so it renders
