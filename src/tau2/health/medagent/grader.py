"""MedAgentBench graders.

Upstream ships its grading module (`refsol.py`) as a separate download rather
than in the repo, to keep it out of training corpora. We therefore do not
vendor it. Instead:

* `builtin_grade` is a faithful reimplementation derived from the published
  task specifications — every constant it checks (NDC codes, SNOMED/LOINC
  codes, dosing rules, the referral free text) is stated in the task's own
  `context`/`instruction`, so nothing here is secret.
* `--refsol /path/to/refsol.py` swaps in the official module for the number you
  publish. `Trajectory` deliberately exposes `.history` / `.result` in the exact
  shape it expects, so it loads unmodified.

`tests/test_medagentbench_grader.py` pins the built-in graders against
hand-built trajectories; `--refsol` additionally lets a run cross-check the two.

Every grader answers a strict boolean: correct, or not. That is the benchmark's
metric — success rate = correct / N.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from tau2.health.medagent.fhir import FhirInfraError, send_get_request
from tau2.health.medagent.protocol import Trajectory, accepted_posts, has_post

# Every task is framed as "it is now 2023-11-13T10:15:00+00:00".
NOW = datetime.fromisoformat("2023-11-13T10:15:00+00:00")
NOW_NAIVE = datetime(2023, 11, 13)


@dataclass
class GradeResult:
    correct: bool
    reason: str = ""
    expected: Optional[Any] = None
    got: Optional[Any] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "correct": self.correct,
            "reason": self.reason,
            "expected": self.expected,
            "got": self.got,
        }


# ---------------------------------------------------------------- helpers


def _answer(traj: Trajectory) -> Any:
    """The FINISH argument, parsed. Raises on anything unparseable."""
    if traj.result is None:
        raise ValueError("no FINISH answer")
    return json.loads(traj.result)


def _get_bundle(url: str) -> dict[str, Any]:
    """GET a FHIR search and return the parsed Bundle.

    A failure here is an EHR outage, not a wrong answer — raise
    `FhirInfraError` so the episode is excluded rather than scored 0.
    """
    res = send_get_request(url)
    if "data" not in res:
        raise FhirInfraError(f"grader GET failed: {res.get('error')}")
    data = res["data"]
    return json.loads(data) if isinstance(data, str) else data


def _observations(base: str, mrn: str, code: str) -> list[tuple[datetime, float, str]]:
    """All (effectiveDateTime, value, raw_timestamp) triples for one code.

    The raw string is kept because task10's expected answer echoes the chart's
    timestamp verbatim — re-serializing a parsed datetime would silently change
    the expected value for any timestamp spelling other than `+00:00`.
    """
    bundle = _get_bundle(
        f"{base}Observation?patient={mrn}&code={code}&_count=5000&_format=json"
    )
    out = []
    for entry in bundle.get("entry", []) or []:
        r = entry.get("resource", {})
        try:
            raw = r["effectiveDateTime"]
            out.append(
                (datetime.fromisoformat(raw), r["valueQuantity"]["value"], raw)
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _latest(
    obs: list[tuple[datetime, float, str]], within_hours: Optional[int] = None
):
    """Most recent (time, value, raw_time), optionally within a lookback window."""
    best_t, best_v, best_raw = None, None, None
    for t, v, raw in obs:
        if within_hours is not None and t < (NOW - timedelta(hours=within_hours)):
            continue
        if best_t is None or t > best_t:
            best_t, best_v, best_raw = t, v, raw
    return best_t, best_v, best_raw


def _subject(mrn: str) -> dict[str, str]:
    return {"reference": f"Patient/{mrn}"}


def _answer_ok(traj: Trajectory, ref: list[Any], *, allow_empty: bool = False) -> bool:
    """Answer equality, with upstream's leniency for check-only tasks.

    On the conditional-order tasks the instruction only asks the agent to
    *check*, so upstream accepts `[]` as well as the true value.
    """
    try:
        got = _answer(traj)
    except Exception:
        return False
    if ref == got:
        return True
    return allow_empty and got == []


def _approx_answer_ok(traj: Trajectory, ref: list[float], tol: float) -> bool:
    try:
        got = _answer(traj)
    except Exception:
        return False
    return (
        isinstance(got, list)
        and len(got) == 1
        and isinstance(got[0], (int, float))
        and abs(got[0] - ref[0]) < tol
    )


def _one_post(traj: Trajectory, base: str, resource: str):
    """Exactly one accepted POST, to the expected endpoint. Returns payload."""
    posts = accepted_posts(traj)
    if len(posts) != 1:
        raise AssertionError(f"expected exactly 1 accepted POST, got {len(posts)}")
    url, payload = posts[0]
    if url != f"{base}{resource}":
        raise AssertionError(f"POST url {url!r} != {base}{resource!r}")
    return payload


# ---------------------------------------------------------------- graders
# Signature mirrors upstream: (case_dict, trajectory, fhir_api_base) -> bool.


def task1(case: dict, traj: Trajectory, base: str) -> GradeResult:
    """MRN lookup by name + DOB. Read-only: any POST fails the task."""
    if has_post(traj):
        return GradeResult(False, "read-only task, but the agent attempted a POST")
    ref = case["sol"]
    ok = _answer_ok(traj, ref)
    return GradeResult(ok, "" if ok else "answer mismatch", ref, traj.result)


def task2(case: dict, traj: Trajectory, base: str) -> GradeResult:
    """Patient age as of 2023-11-13, floored to an integer."""
    if has_post(traj):
        return GradeResult(False, "read-only task, but the agent attempted a POST")
    bundle = _get_bundle(f"{base}Patient?identifier={case['eval_MRN']}&_format=json")
    dob = datetime.strptime(bundle["entry"][0]["resource"]["birthDate"], "%Y-%m-%d")
    age = NOW_NAIVE.year - dob.year
    if (NOW_NAIVE.month, NOW_NAIVE.day) < (dob.month, dob.day):
        age -= 1
    ref = [age]
    ok = _answer_ok(traj, ref)
    return GradeResult(ok, "" if ok else "answer mismatch", ref, traj.result)


def task3(case: dict, traj: Trajectory, base: str) -> GradeResult:
    """Record a blood-pressure observation. Pure write."""
    try:
        p = _one_post(traj, base, "Observation")
        assert p["resourceType"] == "Observation"
        assert len(p["category"]) == 1
        assert len(p["category"][0]["coding"]) == 1
        assert p["category"][0]["coding"][0] == {
            "system": "http://hl7.org/fhir/observation-category",
            "code": "vital-signs",
            "display": "Vital Signs",
        }
        assert p["code"] == {"text": "BP"}
        assert p["effectiveDateTime"] == "2023-11-13T10:15:00+00:00"
        assert p["status"] == "final"
        assert p["valueString"] == "118/77 mmHg"
        assert p["subject"] == _subject(case["eval_MRN"])
    except Exception as e:  # noqa: BLE001
        return GradeResult(False, f"observation payload wrong: {e}")
    return GradeResult(True)


def task4(case: dict, traj: Trajectory, base: str) -> GradeResult:
    """Last serum magnesium within 24h, or -1."""
    if has_post(traj):
        return GradeResult(False, "read-only task, but the agent attempted a POST")
    _, v, _ = _latest(_observations(base, case["eval_MRN"], "MG"), within_hours=24)
    ref = [v if v is not None else -1]
    ok = _answer_ok(traj, ref)
    return GradeResult(ok, "" if ok else "answer mismatch", ref, traj.result)


def task5(case: dict, traj: Trajectory, base: str) -> GradeResult:
    """Magnesium check, then conditional IV replacement order."""
    _, last, _ = _latest(_observations(base, case["eval_MRN"], "MG"), within_hours=24)
    if last is None:
        if has_post(traj):
            return GradeResult(False, "no magnesium in 24h — nothing should be ordered")
        return GradeResult(True)
    if last > 1.9:
        if has_post(traj):
            return GradeResult(False, "magnesium not low — nothing should be ordered")
    else:
        try:
            p = _one_post(traj, base, "MedicationRequest")
            assert p["resourceType"] == "MedicationRequest"
            coding = p["medicationCodeableConcept"]["coding"][0]
            assert coding["system"] == "http://hl7.org/fhir/sid/ndc"
            assert coding["code"] == "0338-1715-40"
            assert "2023-11-13T10:15" in p["authoredOn"]
            di = p["dosageInstruction"][0]
            assert di["route"] == "IV"
            if last < 1:
                dose, rate = 4, 4
            elif last < 1.5:
                dose, rate = 2, 2
            else:
                dose, rate = 1, 1
            assert di["doseAndRate"][0]["doseQuantity"] == {"value": dose, "unit": "g"}
            assert di["doseAndRate"][0]["rateQuantity"] == {"value": rate, "unit": "h"}
            assert p["status"] == "active"
            assert p["intent"] == "order"
            assert p["subject"] == _subject(case["eval_MRN"])
        except Exception as e:  # noqa: BLE001
            return GradeResult(False, f"magnesium order wrong: {e}")
    ref = [last]
    ok = _answer_ok(traj, ref, allow_empty=True)
    return GradeResult(ok, "" if ok else "answer mismatch", ref, traj.result)


def task6(case: dict, traj: Trajectory, base: str) -> GradeResult:
    """Mean glucose over the last 24h, or -1. Tolerance 0.1."""
    if has_post(traj):
        return GradeResult(False, "read-only task, but the agent attempted a POST")
    vals = [
        v
        for t, v, _ in _observations(base, case["eval_MRN"], "GLU")
        if t >= NOW - timedelta(hours=24)
    ]
    ref = [sum(vals) / len(vals) if vals else -1]
    ok = _approx_answer_ok(traj, ref, tol=0.1)
    return GradeResult(ok, "" if ok else "answer mismatch", ref, traj.result)


def task7(case: dict, traj: Trajectory, base: str) -> GradeResult:
    """Most recent glucose of any age, or -1."""
    if has_post(traj):
        return GradeResult(False, "read-only task, but the agent attempted a POST")
    _, v, _ = _latest(_observations(base, case["eval_MRN"], "GLU"))
    ref = [v if v is not None else -1]
    ok = _answer_ok(traj, ref)
    return GradeResult(ok, "" if ok else "answer mismatch", ref, traj.result)


TASK8_NOTE = (
    "Situation: acute left knee injury, Background: radiology report indicates ACL "
    "tear. Assessment: ACL tear grade II. Recommendation: request for Orthopedic "
    "service to evaluate and provide management recommendations."
)


def task8(case: dict, traj: Trajectory, base: str) -> GradeResult:
    """Orthopedic surgery referral. Pure write."""
    try:
        p = _one_post(traj, base, "ServiceRequest")
        assert p["resourceType"] == "ServiceRequest"
        assert p["code"]["coding"][0]["system"] == "http://snomed.info/sct"
        assert p["code"]["coding"][0]["code"] == "306181000000106"
        assert p["authoredOn"] == "2023-11-13T10:15:00+00:00"
        assert p["status"] == "active"
        assert p["intent"] == "order"
        assert p["priority"] == "stat"
        assert TASK8_NOTE in p["note"]["text"]
        assert p["subject"] == _subject(case["eval_MRN"])
    except Exception as e:  # noqa: BLE001
        return GradeResult(False, f"referral payload wrong: {e}")
    return GradeResult(True)


def task9(case: dict, traj: Trajectory, base: str) -> GradeResult:
    """Potassium repletion: a medication order AND a next-morning lab order."""
    _, last, _ = _latest(_observations(base, case["eval_MRN"], "K"))
    if last is None or last >= 3.5:
        if has_post(traj):
            return GradeResult(
                False, "potassium unavailable or not low — nothing should be ordered"
            )
    else:
        posts = accepted_posts(traj)
        if len(posts) != 2:
            return GradeResult(
                False,
                f"expected 2 accepted POSTs (repletion + morning level), got {len(posts)}",
            )
        url, p = posts[0]
        if url != f"{base}MedicationRequest":
            return GradeResult(False, f"first POST url {url!r} is not MedicationRequest")
        try:
            assert p["resourceType"] == "MedicationRequest"
            coding = p["medicationCodeableConcept"]["coding"][0]
            assert coding["system"] == "http://hl7.org/fhir/sid/ndc"
            assert coding["code"] == "40032-917-01"
            assert "2023-11-13T10:15" in p["authoredOn"]
            di = p["dosageInstruction"][0]
            assert di["route"].lower().strip() == "oral"
            dose = (3.5 - last) / 0.1 * 10
            assert abs(di["doseAndRate"][0]["doseQuantity"]["value"] - dose) <= 0.1
            assert di["doseAndRate"][0]["doseQuantity"]["unit"] == "mEq"
            assert p["status"] == "active"
            assert p["intent"] == "order"
            assert p["subject"] == _subject(case["eval_MRN"])
        except Exception as e:  # noqa: BLE001
            return GradeResult(False, f"potassium order wrong: {e}")

        url, p = posts[1]
        if url != f"{base}ServiceRequest":
            return GradeResult(False, f"second POST url {url!r} is not ServiceRequest")
        try:
            assert p["resourceType"] == "ServiceRequest"
            assert p["code"]["coding"][0]["system"] == "http://loinc.org"
            assert p["code"]["coding"][0]["code"] == "2823-3"
            assert p["authoredOn"] == "2023-11-13T10:15:00+00:00"
            assert p["status"] == "active"
            assert p["intent"] == "order"
            assert p["priority"] == "stat"
            assert p["subject"] == _subject(case["eval_MRN"])
            assert "2023-11-14T08:" in p["occurrenceDateTime"]
        except Exception as e:  # noqa: BLE001
            return GradeResult(False, f"morning potassium lab order wrong: {e}")

    ref = [last if last is not None else -1]
    ok = _answer_ok(traj, ref, allow_empty=True)
    return GradeResult(ok, "" if ok else "answer mismatch", ref, traj.result)


def task10(case: dict, traj: Trajectory, base: str) -> GradeResult:
    """Last HbA1C + value date; order a new one if it is over a year old."""
    obs = _observations(base, case["eval_MRN"], "A1C")
    last_t, last_v, last_raw = _latest(obs)
    if last_v is None:
        ref: list[Any] = [-1]
    else:
        ref = [last_v, last_raw]

    stale = last_v is None or last_t < datetime.fromisoformat(
        "2022-11-13T10:15:00+00:00"
    )
    if stale:
        try:
            p = _one_post(traj, base, "ServiceRequest")
            assert p["resourceType"] == "ServiceRequest"
            assert p["code"]["coding"][0]["system"] == "http://loinc.org"
            assert p["code"]["coding"][0]["code"] == "4548-4"
            assert p["authoredOn"] == "2023-11-13T10:15:00+00:00"
            assert p["status"] == "active"
            assert p["intent"] == "order"
            assert p["priority"] == "stat"
            assert p["subject"] == _subject(case["eval_MRN"])
        except Exception as e:  # noqa: BLE001
            return GradeResult(False, f"HbA1C lab order wrong: {e}")
    elif has_post(traj):
        return GradeResult(False, "HbA1C is current — nothing should be ordered")

    ok = _answer_ok(traj, ref, allow_empty=True)
    return GradeResult(ok, "" if ok else "answer mismatch", ref, traj.result)


BUILTIN_GRADERS: dict[str, Callable[[dict, Trajectory, str], GradeResult]] = {
    "task1": task1,
    "task2": task2,
    "task3": task3,
    "task4": task4,
    "task5": task5,
    "task6": task6,
    "task7": task7,
    "task8": task8,
    "task9": task9,
    "task10": task10,
}


def builtin_grade(case: dict, traj: Trajectory, base: str) -> GradeResult:
    """Grade one episode with the built-in reimplementation."""
    if traj.result is None:
        # Upstream: a task with no FINISH is never graded correct, but it stays
        # in the denominator.
        return GradeResult(False, f"episode ended without FINISH (status={traj.status})")
    category = case["id"].split("_")[0]
    grader = BUILTIN_GRADERS.get(category)
    if grader is None:
        return GradeResult(False, f"no grader for category {category}")
    try:
        return grader(case, traj, base)
    except FhirInfraError:
        raise
    except Exception as e:  # noqa: BLE001 — upstream treats grader errors as wrong
        return GradeResult(False, f"grader error: {e}")


def load_refsol(path: str):
    """Load the official `refsol.py` and return a grade function.

    The module imports `from .utils import *`, so we pre-seed a package stub
    exposing `send_get_request` before loading it.
    """
    pkg = "tau2_medagentbench_refsol"
    stub = sys.modules.get(pkg)
    if stub is None:
        import types

        stub = types.ModuleType(pkg)
        stub.__path__ = [str(Path(path).parent)]
        sys.modules[pkg] = stub

        utils = types.ModuleType(f"{pkg}.utils")
        utils.send_get_request = send_get_request
        utils.json = json
        sys.modules[f"{pkg}.utils"] = utils

    spec = importlib.util.spec_from_file_location(f"{pkg}.refsol", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load refsol from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{pkg}.refsol"] = mod
    spec.loader.exec_module(mod)

    def grade(case: dict, traj: Trajectory, base: str) -> GradeResult:
        if traj.result is None:
            return GradeResult(False, "episode ended without FINISH")
        fn = getattr(mod, case["id"].split("_")[0], None)
        if fn is None:
            return GradeResult(False, "refsol has no grader for this category")
        try:
            return GradeResult(fn(case, traj, base) is True, "refsol")
        except Exception as e:  # noqa: BLE001
            return GradeResult(False, f"refsol error: {e}")

    return grade
