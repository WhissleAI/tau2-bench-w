"""Grader behaviour, against a mocked FHIR server.

The graders are the score. These tests pin both the "correct" path and the
traps: a read-only task that touched POST, a conditional order that should NOT
have fired, and a payload that is right in every field but one.
"""

import json

import pytest

from tau2.health.medagent import grader as grader_mod
from tau2.health.medagent.grader import TASK8_NOTE, builtin_grade
from tau2.health.medagent.protocol import Trajectory

BASE = "http://ehr.test/fhir/"


def traj(*, agent_turns=(), result=None):
    """Build a trajectory where every POST is followed by acceptance."""
    t = Trajectory()
    t.add("user", "prompt")
    for turn in agent_turns:
        t.add("agent", turn)
        if turn.startswith("POST"):
            t.add("user", "POST request accepted and executed successfully.")
        else:
            t.add("user", "Here is the response from the GET request:\n{}.")
    if result is not None:
        t.add("agent", f"FINISH({result})")
        t.result = result
        t.status = "completed"
    return t


@pytest.fixture
def fhir(monkeypatch):
    """Route grader GETs to a canned bundle keyed by a substring of the URL."""
    routes: dict[str, dict] = {}

    def _get(url, params=None, headers=None, timeout=60.0):
        for frag, bundle in routes.items():
            if frag in url:
                return {"status_code": 200, "data": json.dumps(bundle)}
        return {"status_code": 200, "data": json.dumps({"entry": []})}

    monkeypatch.setattr(grader_mod, "send_get_request", _get)
    return routes


def obs_bundle(*pairs):
    return {
        "entry": [
            {
                "resource": {
                    "effectiveDateTime": t,
                    "valueQuantity": {"value": v},
                }
            }
            for t, v in pairs
        ]
    }


def case(task_id, mrn="S1", sol=None):
    d = {"id": task_id, "instruction": "i", "context": "c", "eval_MRN": mrn}
    if sol is not None:
        d["sol"] = sol
    return d


# ------------------------------------------------------- read-only tasks


def test_task1_correct_answer(fhir):
    g = builtin_grade(case("task1_1", sol=["S6534835"]), traj(result='["S6534835"]'), BASE)
    assert g.correct


def test_task1_wrong_answer(fhir):
    g = builtin_grade(case("task1_1", sol=["S6534835"]), traj(result='["S0000000"]'), BASE)
    assert not g.correct


def test_readonly_task_fails_if_the_agent_touched_post(fhir):
    """Upstream is strict: a read-only task that attempts a write is wrong even
    if the answer is right."""
    t = traj(agent_turns=['POST http://ehr.test/fhir/Observation\n{"a":1}'],
             result='["S6534835"]')
    g = builtin_grade(case("task1_1", sol=["S6534835"]), t, BASE)
    assert not g.correct
    assert "POST" in g.reason


def test_task2_computes_age_from_the_chart(fhir):
    fhir["Patient?identifier"] = {"entry": [{"resource": {"birthDate": "1932-12-29"}}]}
    # Birthday has not occurred by 2023-11-13, so age is 90.
    assert builtin_grade(case("task2_1"), traj(result="[90]"), BASE).correct
    assert not builtin_grade(case("task2_1"), traj(result="[91]"), BASE).correct


def test_task4_returns_minus_one_when_nothing_in_window(fhir):
    """A magnesium from 3 days ago is outside the 24h window."""
    fhir["code=MG"] = obs_bundle(("2023-11-10T10:00:00+00:00", 1.7))
    assert builtin_grade(case("task4_1"), traj(result="[-1]"), BASE).correct
    assert not builtin_grade(case("task4_1"), traj(result="[1.7]"), BASE).correct


def test_task6_mean_glucose_uses_a_tolerance(fhir):
    fhir["code=GLU"] = obs_bundle(
        ("2023-11-13T09:00:00+00:00", 100.0), ("2023-11-13T08:00:00+00:00", 101.0)
    )
    assert builtin_grade(case("task6_1"), traj(result="[100.5]"), BASE).correct
    assert builtin_grade(case("task6_1"), traj(result="[100.55]"), BASE).correct
    assert not builtin_grade(case("task6_1"), traj(result="[101.0]"), BASE).correct


def test_task7_takes_the_latest_glucose_of_any_age(fhir):
    fhir["code=GLU"] = obs_bundle(
        ("2020-01-01T00:00:00+00:00", 88.0), ("2022-06-01T00:00:00+00:00", 99.0)
    )
    assert builtin_grade(case("task7_1"), traj(result="[99.0]"), BASE).correct


# ----------------------------------------------------------- write tasks


GOOD_BP = {
    "resourceType": "Observation",
    "category": [
        {
            "coding": [
                {
                    "system": "http://hl7.org/fhir/observation-category",
                    "code": "vital-signs",
                    "display": "Vital Signs",
                }
            ]
        }
    ],
    "code": {"text": "BP"},
    "effectiveDateTime": "2023-11-13T10:15:00+00:00",
    "status": "final",
    "valueString": "118/77 mmHg",
    "subject": {"reference": "Patient/S1"},
}


def post(resource, payload):
    return f"POST {BASE}{resource}\n{json.dumps(payload)}"


def test_task3_accepts_a_correct_observation(fhir):
    t = traj(agent_turns=[post("Observation", GOOD_BP)], result="[]")
    assert builtin_grade(case("task3_1"), t, BASE).correct


def test_task3_rejects_a_wrong_value(fhir):
    bad = {**GOOD_BP, "valueString": "120/80 mmHg"}
    t = traj(agent_turns=[post("Observation", bad)], result="[]")
    assert not builtin_grade(case("task3_1"), t, BASE).correct


def test_task3_rejects_a_post_to_the_wrong_endpoint(fhir):
    t = traj(agent_turns=[post("MedicationRequest", GOOD_BP)], result="[]")
    assert not builtin_grade(case("task3_1"), t, BASE).correct


def test_task3_requires_exactly_one_post(fhir):
    t = traj(agent_turns=[post("Observation", GOOD_BP), post("Observation", GOOD_BP)],
             result="[]")
    g = builtin_grade(case("task3_1"), t, BASE)
    assert not g.correct and "exactly 1" in g.reason


def test_task3_fails_when_the_agent_only_talks(fhir):
    """No POST at all — the said-vs-wrote case, caught by the score too."""
    t = traj(result="[]")
    assert not builtin_grade(case("task3_1"), t, BASE).correct


GOOD_REFERRAL = {
    "resourceType": "ServiceRequest",
    "code": {"coding": [{"system": "http://snomed.info/sct", "code": "306181000000106"}]},
    "authoredOn": "2023-11-13T10:15:00+00:00",
    "status": "active",
    "intent": "order",
    "priority": "stat",
    "note": {"text": TASK8_NOTE},
    "subject": {"reference": "Patient/S1"},
}


def test_task8_accepts_a_correct_referral(fhir):
    t = traj(agent_turns=[post("ServiceRequest", GOOD_REFERRAL)], result="[]")
    assert builtin_grade(case("task8_1"), t, BASE).correct


def test_task8_rejects_a_truncated_note(fhir):
    bad = {**GOOD_REFERRAL, "note": {"text": "ACL tear"}}
    t = traj(agent_turns=[post("ServiceRequest", bad)], result="[]")
    assert not builtin_grade(case("task8_1"), t, BASE).correct


# --------------------------------------------------- conditional actions


def test_task5_orders_nothing_when_magnesium_is_normal(fhir):
    fhir["code=MG"] = obs_bundle(("2023-11-13T09:00:00+00:00", 2.1))
    assert builtin_grade(case("task5_1"), traj(result="[2.1]"), BASE).correct


def test_task5_fails_if_it_orders_when_magnesium_is_normal(fhir):
    """Over-ordering is a clinical error, and the benchmark scores it as one."""
    fhir["code=MG"] = obs_bundle(("2023-11-13T09:00:00+00:00", 2.1))
    t = traj(agent_turns=[post("MedicationRequest", {"resourceType": "MedicationRequest"})],
             result="[2.1]")
    assert not builtin_grade(case("task5_1"), t, BASE).correct


def test_task5_dosing_ladder_is_enforced(fhir):
    """0.8 mg/dL is severe: 4 g over 4 hours. 2 g must fail."""
    fhir["code=MG"] = obs_bundle(("2023-11-13T09:00:00+00:00", 0.8))

    def order(dose, rate):
        return {
            "resourceType": "MedicationRequest",
            "medicationCodeableConcept": {
                "coding": [
                    {"system": "http://hl7.org/fhir/sid/ndc", "code": "0338-1715-40"}
                ]
            },
            "authoredOn": "2023-11-13T10:15:00+00:00",
            "dosageInstruction": [
                {
                    "route": "IV",
                    "doseAndRate": [
                        {
                            "doseQuantity": {"value": dose, "unit": "g"},
                            "rateQuantity": {"value": rate, "unit": "h"},
                        }
                    ],
                }
            ],
            "status": "active",
            "intent": "order",
            "subject": {"reference": "Patient/S1"},
        }

    ok = traj(agent_turns=[post("MedicationRequest", order(4, 4))], result="[0.8]")
    assert builtin_grade(case("task5_1"), ok, BASE).correct

    under = traj(agent_turns=[post("MedicationRequest", order(2, 2))], result="[0.8]")
    assert not builtin_grade(case("task5_1"), under, BASE).correct


def test_task5_accepts_an_empty_answer_because_the_task_only_asks_to_check(fhir):
    fhir["code=MG"] = obs_bundle(("2023-11-13T09:00:00+00:00", 2.1))
    assert builtin_grade(case("task5_1"), traj(result="[]"), BASE).correct


def test_task9_requires_both_orders(fhir):
    fhir["code=K"] = obs_bundle(("2023-11-13T09:00:00+00:00", 3.0))
    med = {
        "resourceType": "MedicationRequest",
        "medicationCodeableConcept": {
            "coding": [{"system": "http://hl7.org/fhir/sid/ndc", "code": "40032-917-01"}]
        },
        "authoredOn": "2023-11-13T10:15:00+00:00",
        "dosageInstruction": [
            {
                "route": "oral",
                "doseAndRate": [{"doseQuantity": {"value": 50.0, "unit": "mEq"}}],
            }
        ],
        "status": "active",
        "intent": "order",
        "subject": {"reference": "Patient/S1"},
    }
    lab = {
        "resourceType": "ServiceRequest",
        "code": {"coding": [{"system": "http://loinc.org", "code": "2823-3"}]},
        "authoredOn": "2023-11-13T10:15:00+00:00",
        "status": "active",
        "intent": "order",
        "priority": "stat",
        "subject": {"reference": "Patient/S1"},
        "occurrenceDateTime": "2023-11-14T08:00:00+00:00",
    }
    both = traj(
        agent_turns=[post("MedicationRequest", med), post("ServiceRequest", lab)],
        result="[3.0]",
    )
    assert builtin_grade(case("task9_1"), both, BASE).correct

    # The repletion alone is not enough — the morning level is part of the task.
    only_med = traj(agent_turns=[post("MedicationRequest", med)], result="[3.0]")
    g = builtin_grade(case("task9_1"), only_med, BASE)
    assert not g.correct and "2 accepted POSTs" in g.reason


def test_task10_echoes_the_charts_raw_timestamp(fhir):
    """The expected answer carries the chart's own timestamp string. A
    re-serialized datetime would silently change the expected value."""
    fhir["code=A1C"] = obs_bundle(("2023-06-01T10:00:00+00:00", 6.4))
    ok = traj(result='[6.4, "2023-06-01T10:00:00+00:00"]')
    assert builtin_grade(case("task10_1"), ok, BASE).correct


def test_task10_orders_when_the_value_is_stale(fhir):
    fhir["code=A1C"] = obs_bundle(("2021-01-01T10:00:00+00:00", 6.4))
    order = {
        "resourceType": "ServiceRequest",
        "code": {"coding": [{"system": "http://loinc.org", "code": "4548-4"}]},
        "authoredOn": "2023-11-13T10:15:00+00:00",
        "status": "active",
        "intent": "order",
        "priority": "stat",
        "subject": {"reference": "Patient/S1"},
    }
    t = traj(agent_turns=[post("ServiceRequest", order)],
             result='[6.4, "2021-01-01T10:00:00+00:00"]')
    assert builtin_grade(case("task10_1"), t, BASE).correct

    # Same stale value, no order placed → wrong.
    no_order = traj(result='[6.4, "2021-01-01T10:00:00+00:00"]')
    assert not builtin_grade(case("task10_1"), no_order, BASE).correct


# ------------------------------------------------------------- edge cases


def test_no_finish_is_never_correct_but_is_still_a_graded_task(fhir):
    t = Trajectory()
    t.add("user", "prompt")
    t.status = "task_limit_reached"
    g = builtin_grade(case("task1_1", sol=["S1"]), t, BASE)
    assert not g.correct
    assert "FINISH" in g.reason


def test_unparseable_finish_payload_is_wrong_not_a_crash(fhir):
    t = traj(result="not json at all")
    assert not builtin_grade(case("task1_1", sol=["S1"]), t, BASE).correct


def test_grader_errors_do_not_escape(fhir):
    fhir["Patient?identifier"] = {"entry": []}  # IndexError inside task2
    g = builtin_grade(case("task2_1"), traj(result="[90]"), BASE)
    assert not g.correct
    assert "grader error" in g.reason
