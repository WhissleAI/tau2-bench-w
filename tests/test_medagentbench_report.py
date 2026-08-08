"""Scoring aggregation, infra-fail exclusion, and subset selection.

The rule this file exists to enforce: an episode that never ran (brain or EHR
unreachable) is `infra_fail` and must not appear in any denominator.
"""

import json

import pytest

from tau2.flow.analyze import DEFAULT_SEVERITY
from tau2.health.medagent.data import Case, load_cases
from tau2.health.medagent.episode import Episode
from tau2.health.medagent.grader import GradeResult
from tau2.health.medagent.integrity import assess
from tau2.health.medagent.protocol import Trajectory, Turn, parse_action
from tau2.health.medagent.report import TaskResult, render_markdown, summarize, write_artifacts


def make_result(task_id, *, correct=True, infra=False, status="completed",
                agent_turns=(), result="[]"):
    case = Case(id=task_id, instruction="i", context="c", sol=["x"], eval_mrn="S1")
    t = Trajectory()
    t.add("user", "prompt")
    turns = []
    for n, turn in enumerate(agent_turns):
        t.add("agent", turn)
        action = parse_action(turn)
        obs = None
        if turn.startswith("POST"):
            obs = "POST request accepted and executed successfully."
            t.add("user", obs)
        turns.append(
            Turn(
                round=n,
                agent_reply=turn,
                action_kind=action.kind,
                url=action.url,
                payload=action.payload,
                observation=obs,
            )
        )
    t.result = None if infra else result
    t.status = "infra_fail" if infra else status
    ep = Episode(
        case=case,
        trajectory=t,
        turns=turns,
        infra_fail=infra,
        infra_reason="endpoint down" if infra else None,
    )
    grade = None if infra else GradeResult(correct)
    return TaskResult(episode=ep, grade=grade, integrity=assess(task_id, t))


META = {"agent_id": "a", "model": "m"}


def test_infra_failures_are_excluded_from_every_rate():
    results = [
        make_result("task1_1", correct=True),
        make_result("task1_2", correct=False),
        make_result("task1_3", infra=True),
    ]
    s = summarize(results, mode="brain-parity", run_meta=META)

    assert s["n_tasks_attempted"] == 3
    assert s["n_scored"] == 2
    assert s["n_infra_fail"] == 1
    assert s["infra_fail_task_ids"] == ["task1_3"]
    # 1 of 2 measured, NOT 1 of 3.
    assert s["overall"] == {"n": 2, "correct": 1, "success_rate_pct": 50.0}
    assert s["per_category"]["task1"]["n"] == 2


def test_infra_fail_uses_the_shared_taxonomy():
    """Same word, same meaning as the flow suites."""
    assert DEFAULT_SEVERITY["infra_fail"] == "high"
    r = make_result("task1_1", infra=True)
    types = [f.type for f in r.findings()]
    assert types == ["infra_fail"]


def test_an_all_infra_run_reports_none_not_zero_percent():
    """A total outage must not read as '0% — the agent failed everything'."""
    s = summarize([make_result("task1_1", infra=True)], mode="brain-parity", run_meta=META)
    assert s["n_scored"] == 0
    assert s["overall"]["success_rate_pct"] is None


def test_query_action_split_matches_the_papers_definition():
    results = [
        make_result("task1_1", correct=True),   # query
        make_result("task2_1", correct=True),   # query
        make_result("task3_1", correct=False),  # action
        make_result("task8_1", correct=True),   # action
    ]
    s = summarize(results, mode="brain-parity", run_meta=META)
    assert s["query"] == {"n": 2, "correct": 2, "success_rate_pct": 100.0}
    assert s["action"] == {"n": 2, "correct": 1, "success_rate_pct": 50.0}
    assert s["overall"]["success_rate_pct"] == 75.0


def test_published_baselines_decompose_exactly():
    """Sanity-check the baseline table we print alongside our number:
    Claude 3.5 Sonnet v2 = 209/300 overall, 128/150 query, 81/150 action."""
    s = summarize([make_result("task1_1")], mode="brain-parity", run_meta=META)
    b = s["published_baselines_full_300"]["Claude 3.5 Sonnet v2"]
    assert round(150 * b["query"] / 100) + round(150 * b["action"] / 100) == 209
    assert round(300 * b["overall"] / 100) == 209


def test_said_but_did_not_write_becomes_a_high_severity_finding():
    r = make_result("task3_1", correct=False, result='["I have placed the order."]')
    findings = {f.type: f for f in r.findings()}
    assert "compliance" in findings
    assert findings["compliance"].severity == "high"


def test_turn_cap_and_invalid_action_are_reported_as_findings():
    assert "turn_cap_exceeded" in {
        f.type for f in make_result("task1_1", status="task_limit_reached").findings()
    }
    assert "say_fidelity" in {
        f.type for f in make_result("task1_1", status="agent_invalid_action").findings()
    }


def test_summary_always_carries_n_and_the_comparability_caveat():
    s = summarize([make_result("task1_1")], mode="brain-parity", run_meta=META)
    assert "n" in s["overall"]
    assert "300" in s["comparability_note"]


def test_artifacts_land_under_the_expected_layout(tmp_path):
    results = [make_result("task1_1"), make_result("task3_1", agent_turns=[
        'POST http://ehr.test/fhir/Observation\n{"resourceType": "Observation"}'
    ])]
    s = summarize(results, mode="brain-parity", run_meta=META)
    run_dir = write_artifacts(results, s, root=tmp_path, run_name="fixed")

    assert run_dir == tmp_path / "brain-parity_fixed"
    assert (run_dir / "SUMMARY.json").exists()
    assert (run_dir / "SUMMARY.md").exists()

    task = json.loads((run_dir / "tasks" / "task3_1.json").read_text())
    # Per-task artifact carries messages, the tool/action calls, and the
    # resulting FHIR write record.
    assert task["history"] and task["turns"]
    assert task["integrity"]["emitted_writes"] == 1
    assert "prompt" in task and "grade" in task


def test_markdown_renders_without_a_write_check():
    s = summarize([make_result("task1_1")], mode="brain-parity", run_meta=META)
    md = render_markdown(s)
    assert "# MedAgentBench" in md
    assert "Overall" in md
    assert "infra_fail" in md


# ------------------------------------------------------- subset selection


def _cases(n_per_cat=5):
    out = []
    for c in range(1, 11):
        for i in range(n_per_cat):
            out.append(
                {
                    "id": f"task{c}_{i}",
                    "instruction": "i",
                    "context": "",
                    "sol": ["x"],
                    "eval_MRN": "S1",
                }
            )
    return out


@pytest.fixture
def task_file(tmp_path):
    p = tmp_path / "test_data_v2.json"
    p.write_text(json.dumps(_cases()))
    return p


def test_limit_is_stratified_across_every_category(task_file):
    """A cheap subset run must still cover Query and Action, or its number is
    meaningless."""
    cases = load_cases(task_file, limit=10)
    assert len(cases) == 10
    assert {c.category for c in cases} == {f"task{i}" for i in range(1, 11)}


def test_limit_larger_than_the_set_returns_everything(task_file):
    assert len(load_cases(task_file, limit=999)) == 50


def test_filtering_by_bare_category_and_by_exact_id(task_file):
    assert {c.category for c in load_cases(task_file, task_ids=["task3"])} == {"task3"}
    got = load_cases(task_file, task_ids=["task3_1", "task8_2"])
    assert sorted(c.id for c in got) == ["task3_1", "task8_2"]


def test_category_filter(task_file):
    got = load_cases(task_file, categories=["task5", "task9"])
    assert {c.category for c in got} == {"task5", "task9"}


def test_action_and_query_flags(task_file):
    assert load_cases(task_file, task_ids=["task3_1"])[0].is_action is True
    assert load_cases(task_file, task_ids=["task1_1"])[0].is_action is False


def test_raw_round_trips_to_the_shape_graders_expect(task_file):
    raw = load_cases(task_file, task_ids=["task1_1"])[0].raw
    assert raw["eval_MRN"] == "S1"
    assert raw["id"] == "task1_1"
