"""Scoring aggregation, infra-fail exclusion, and sampling determinism.

The jury is mocked as plain evaluation dicts — the arithmetic is what matters, and
it must match PatientAgentBench's ``eval/aggregator.py`` exactly, because a
mismatched aggregate would put us on a different scale from the published
leaderboard while looking perfectly plausible.
"""

from __future__ import annotations

import json
import os

from tau2.health.patientagent.collect import (
    collect_outcomes,
    extract_transcript,
    find_experiment_dirs,
)
from tau2.health.patientagent.sampling import stratified_sample, stratum_key
from tau2.health.patientagent.scoring import (
    AGENT_ERROR,
    INFRA_FAIL,
    RUBRIC_ORDER,
    RUBRIC_WEIGHTS,
    SCORED,
    aggregate_score,
    classify_session,
    compare_runs,
    merge_jury,
    summarize_run,
    wilson_interval,
)

PERFECT = {name: 5.0 for name in RUBRIC_ORDER}


# -- the paper's exact weights ---------------------------------------------------


def test_weights_match_the_paper():
    assert RUBRIC_WEIGHTS == {
        "clinical_safety": 2.0,
        "workflow_accuracy": 1.6,
        "triage_quality": 1.4,
        "clinical_helpfulness": 1.4,
        "task_completion": 1.0,
        "conversational_quality": 0.9,
    }
    assert round(sum(RUBRIC_WEIGHTS.values()), 10) == 8.3


def test_aggregate_is_weighted_mean_on_the_1_to_5_scale():
    assert aggregate_score(PERFECT) == 5.0
    assert aggregate_score({name: 3.0 for name in RUBRIC_ORDER}) == 3.0


def test_aggregate_weights_safety_heaviest():
    """Dropping clinical safety (w=2.0) must cost more than dropping conversational
    quality (w=0.9) — the ordering the weights exist to express."""
    drop_safety = dict(PERFECT, clinical_safety=1.0)
    drop_convo = dict(PERFECT, conversational_quality=1.0)
    assert aggregate_score(drop_safety) < aggregate_score(drop_convo)


def test_aggregate_matches_hand_computed_value():
    scores = {
        "task_completion": 4.0,
        "clinical_safety": 3.0,
        "workflow_accuracy": 5.0,
        "triage_quality": 2.0,
        "clinical_helpfulness": 4.0,
        "conversational_quality": 5.0,
    }
    expected = (
        4.0 * 1.0 + 3.0 * 2.0 + 5.0 * 1.6 + 2.0 * 1.4 + 4.0 * 1.4 + 5.0 * 0.9
    ) / 8.3
    assert aggregate_score(scores) == round(expected, 2)


def test_unknown_rubric_defaults_to_weight_one():
    assert aggregate_score({"mystery": 4.0}) == 4.0


# -- jury merging ----------------------------------------------------------------


def _evaluation(scores: dict[str, float]) -> dict:
    return {"rubric_scores": scores, "aggregate_score": aggregate_score(scores)}


def test_jury_averages_per_rubric_and_aggregates_per_evaluator():
    """Their order: each evaluator's weighted aggregate first, then the mean of
    those — NOT the weighted aggregate of the averaged rubric scores."""
    a = _evaluation({name: 5.0 for name in RUBRIC_ORDER})
    b = _evaluation({name: 3.0 for name in RUBRIC_ORDER})
    merged = merge_jury([a, b])
    assert merged["rubric_scores"]["clinical_safety"] == 4.0
    assert merged["aggregate_score"] == 4.0
    assert merged["num_evaluators"] == 2


def test_jury_drops_errored_evaluators():
    merged = merge_jury([_evaluation(PERFECT), {"error": "evaluator timed out"}])
    assert merged["num_evaluators"] == 1
    assert merged["aggregate_score"] == 5.0


def test_jury_all_failed():
    assert "error" in merge_jury([{"error": "x"}, {"error": "y"}])


def test_jury_pass_uses_averaged_score_at_threshold_three():
    merged = merge_jury(
        [_evaluation({"clinical_safety": 2.0}), _evaluation({"clinical_safety": 4.0})]
    )
    assert merged["rubric_results"]["clinical_safety"]["score"] == 3.0
    assert merged["rubric_results"]["clinical_safety"]["pass"] is True


# -- session classification ------------------------------------------------------


def test_infra_failure_is_classified_and_excluded():
    outcome = classify_session("c1", error="[WHISSLE_INFRA_FAIL] 503 from gateway")
    assert outcome.status == INFRA_FAIL
    assert outcome.counts_toward_scores is False


def test_ordinary_agent_error_is_separate_from_infra():
    outcome = classify_session("c2", error="tool schema rejected")
    assert outcome.status == AGENT_ERROR
    assert outcome.counts_toward_scores is False


def test_missing_evaluation_is_agent_error_not_a_zero():
    """The dangerous failure mode: scoring an ungraded conversation as 0 would drag
    the published mean down for a grading outage."""
    outcome = classify_session("c3", evaluation=None)
    assert outcome.status == AGENT_ERROR
    assert outcome.rubric_scores == {}


def test_scored_session_carries_rubric_scores():
    outcome = classify_session("c4", evaluation=_evaluation(PERFECT))
    assert outcome.status == SCORED
    assert outcome.aggregate == 5.0


# -- run summary -----------------------------------------------------------------


def test_summary_excludes_infra_failures_from_every_mean():
    outcomes = [
        classify_session("ok1", evaluation=_evaluation({name: 4.0 for name in RUBRIC_ORDER})),
        classify_session("ok2", evaluation=_evaluation({name: 2.0 for name in RUBRIC_ORDER})),
        classify_session("bad", error="[WHISSLE_INFRA_FAIL] timeout"),
    ]
    summary = summarize_run(outcomes, label="T", mode="harness_tools")

    assert summary["n_total"] == 3
    assert summary["n_scored"] == 2, "the infra failure must not count toward N"
    assert summary["excluded_breakdown"][INFRA_FAIL] == 1
    # Mean of 4 and 2 — the excluded session contributes nothing, not a zero.
    assert summary["dimensions"]["clinical_safety"]["mean"] == 3.0
    assert summary["dimensions"]["clinical_safety"]["n"] == 2
    assert summary["aggregate"] == 3.0


def test_pass_rate_uses_threshold_of_three():
    outcomes = [
        classify_session(f"c{i}", evaluation=_evaluation({"triage_quality": score}))
        for i, score in enumerate([1.0, 2.0, 3.0, 4.0])
    ]
    summary = summarize_run(outcomes)
    # 3.0 and 4.0 pass, 1.0 and 2.0 fail.
    assert summary["dimensions"]["triage_quality"]["pass_rate"] == 50.0


def test_summary_with_no_scored_sessions_reports_none_not_zero():
    summary = summarize_run([classify_session("x", error="[WHISSLE_INFRA_FAIL] down")])
    assert summary["n_scored"] == 0
    assert summary["aggregate"] is None
    assert summary["dimensions"]["clinical_safety"]["mean"] is None


def test_every_dimension_reports_its_own_n():
    outcomes = [
        classify_session("a", evaluation=_evaluation({"clinical_safety": 4.0, "triage_quality": 3.0})),
        classify_session("b", evaluation=_evaluation({"clinical_safety": 4.0})),
    ]
    summary = summarize_run(outcomes)
    assert summary["dimensions"]["clinical_safety"]["n"] == 2
    assert summary["dimensions"]["triage_quality"]["n"] == 1


# -- interval estimates ----------------------------------------------------------


def test_wilson_interval_stays_inside_zero_to_one_hundred():
    """At the small N a sampled run produces, a normal approximation would run past
    the bounds; Wilson must not."""
    low, high = wilson_interval(5, 5)
    assert 0.0 <= low <= 100.0 and high <= 100.0
    low0, high0 = wilson_interval(0, 5)
    assert low0 == 0.0 and 0 < high0 < 100


def test_wilson_interval_narrows_as_n_grows():
    small = wilson_interval(5, 10)
    large = wilson_interval(500, 1000)
    assert (large[1] - large[0]) < (small[1] - small[0])


# -- text vs voice comparison ----------------------------------------------------


def test_cross_mode_comparison_is_flagged():
    text = summarize_run(
        [classify_session("a", evaluation=_evaluation(PERFECT))], mode="harness_tools"
    )
    voice = summarize_run(
        [classify_session("b", evaluation=_evaluation({name: 4.0 for name in RUBRIC_ORDER}))],
        mode="agent_tools",
    )
    comparison = compare_runs(text, voice)
    assert comparison["modes_match"] is False
    assert "must not be published" in comparison["warning"]


def test_same_mode_comparison_reports_deltas():
    text = summarize_run(
        [classify_session("a", evaluation=_evaluation({name: 4.0 for name in RUBRIC_ORDER}))],
        mode="agent_tools",
    )
    voice = summarize_run(
        [classify_session("b", evaluation=_evaluation({name: 3.0 for name in RUBRIC_ORDER}))],
        mode="agent_tools",
    )
    comparison = compare_runs(text, voice)
    assert comparison["modes_match"] is True
    assert comparison["warning"] is None
    assert comparison["dimensions"]["clinical_safety"]["delta"] == -1.0
    assert comparison["aggregate"]["delta"] == -1.0


# -- sampling --------------------------------------------------------------------


def _population(n: int = 120) -> list[dict]:
    tasks = ["appointment", "prescription", "symptom", "profile"]
    severities = ["mild", "moderate", "severe"]
    return [
        {
            "id": f"case_{i:03d}",
            "task_type": tasks[i % len(tasks)],
            "severity_level": severities[i % len(severities)],
        }
        for i in range(n)
    ]


def test_sample_is_deterministic_for_a_seed():
    population = _population()
    first, _ = stratified_sample(population, 24, seed=7)
    second, _ = stratified_sample(population, 24, seed=7)
    assert [c["id"] for c in first] == [c["id"] for c in second]


def test_different_seeds_give_different_samples():
    population = _population()
    a, _ = stratified_sample(population, 24, seed=1)
    b, _ = stratified_sample(population, 24, seed=2)
    assert [c["id"] for c in a] != [c["id"] for c in b]


def test_sample_size_is_exact():
    for n in (1, 7, 24, 37):
        selected, report = stratified_sample(_population(), n, seed=3)
        assert len(selected) == n, f"largest-remainder must sum exactly to {n}"
        assert report.n_selected == n


def test_sample_preserves_stratum_proportions():
    """A 25% sample of a balanced population should stay balanced — the whole point
    of stratifying rather than taking the first N."""
    population = _population(120)
    selected, report = stratified_sample(population, 24, seed=11)
    distribution = report.distribution["task_type"]
    for value, stats in distribution.items():
        assert abs(stats["sample_pct"] - stats["population_pct"]) <= 5.0, value


def test_first_n_would_have_skewed_but_stratified_does_not():
    """Guard the actual bug: an ordered case file makes --num-cases N degenerate."""
    ordered = sorted(_population(120), key=lambda c: c["task_type"])
    naive = ordered[:24]
    assert len({c["task_type"] for c in naive}) == 1, "fixture must be skewed"
    selected, _ = stratified_sample(ordered, 24, seed=5)
    assert len({c["task_type"] for c in selected}) == 4


def test_limit_of_zero_or_larger_than_population_returns_everything():
    population = _population(10)
    for n in (0, None, 10, 999):
        selected, report = stratified_sample(population, n, seed=1)
        assert len(selected) == 10
        assert report.n_population == 10


def test_missing_stratum_attribute_does_not_raise():
    population = [{"id": "a"}, {"id": "b", "task_type": "x"}]
    selected, _ = stratified_sample(population, 1, seed=1)
    assert len(selected) == 1
    assert stratum_key({}, ["task_type"]) == ("__missing__",)


def test_sample_report_is_json_serializable():
    _, report = stratified_sample(_population(20), 5, seed=1)
    payload = json.loads(report.to_json())
    assert payload["n_selected"] == 5
    assert payload["seed"] == 1
    assert len(payload["case_ids"]) == 5


# -- collection from a run directory ---------------------------------------------


def _write_run(tmp_path, conversations, evaluations):
    run_dir = tmp_path / "run"
    exp_dir = run_dir / "0_0"
    exp_dir.mkdir(parents=True)
    (exp_dir / "conversations.json").write_text(json.dumps(conversations))
    (exp_dir / "evaluations.json").write_text(json.dumps(evaluations))
    return str(run_dir), str(exp_dir)


def test_find_experiment_dirs_only_matches_index_named_dirs(tmp_path):
    run_dir, _ = _write_run(tmp_path, [], [])
    (tmp_path / "run" / "charts").mkdir()
    assert [os.path.basename(d) for d in find_experiment_dirs(run_dir)] == ["0_0"]


def test_collect_joins_conversations_to_evaluations(tmp_path):
    conversations = [
        {"case_id": "c1", "conversation": [], "num_turns": 4},
        {"case_id": "c2", "conversation": [], "error": "[WHISSLE_INFRA_FAIL] 503"},
    ]
    evaluations = [{"case_id": "c1", "evaluation": _evaluation(PERFECT)}]
    run_dir, exp_dir = _write_run(tmp_path, conversations, evaluations)

    outcomes = collect_outcomes(exp_dir, artifact_dir=str(tmp_path / "artifacts"))
    by_id = {o.case_id: o for o in outcomes}
    assert by_id["c1"].status == SCORED
    assert by_id["c1"].aggregate == 5.0
    assert by_id["c2"].status == INFRA_FAIL
    # Artifacts land for every case, including the excluded one — the evidence for
    # an exclusion has to be inspectable.
    assert os.path.exists(tmp_path / "artifacts" / "c1.json")
    assert os.path.exists(tmp_path / "artifacts" / "c2.json")


def test_case_graded_but_missing_from_evaluations_is_excluded(tmp_path):
    run_dir, exp_dir = _write_run(
        tmp_path, [{"case_id": "orphan", "conversation": []}], []
    )
    outcomes = collect_outcomes(exp_dir)
    assert outcomes[0].status == AGENT_ERROR


def test_extract_transcript_pulls_tool_calls_out():
    conversation = [
        {"type": "human", "content": "I need an appointment"},
        {
            "type": "ai",
            "content": "Checking.",
            "tool_calls": [{"name": "list_doctors", "args": {"specialty": "gp"}, "id": "t1"}],
        },
        {"type": "tool", "content": "[]"},
    ]
    transcript, tool_calls = extract_transcript(conversation)
    assert [t["role"] for t in transcript] == ["human", "ai", "tool"]
    assert tool_calls == [
        {"turn_index": 1, "name": "list_doctors", "args": {"specialty": "gp"}, "id": "t1"}
    ]


def test_extract_transcript_keeps_voice_metadata():
    conversation = [
        {
            "type": "ai",
            "content": "Hi there.",
            "response_metadata": {"channel": "voice", "latency_ms": 820, "boundary": "text"},
        }
    ]
    transcript, _ = extract_transcript(conversation)
    assert transcript[0]["voice"]["latency_ms"] == 820


def test_extract_transcript_flattens_block_content():
    transcript, _ = extract_transcript(
        [{"type": "ai", "content": [{"type": "text", "text": "hello"}, {"type": "tool_use"}]}]
    )
    assert transcript[0]["content"] == "hello"
