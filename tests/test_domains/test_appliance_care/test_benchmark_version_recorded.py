"""The benchmark version must be recorded by a real run, not by hand.

A version constant that only ever appears in a hand-written report is worth
little: the report is what people forget to update. These tests follow the value
through the code a normal run actually executes — environment → `get_info` →
run `Info` → serialized JSON — so a stored `results.json` carries the version it
was measured against without anyone remembering to add it.

Scores are comparable only within a version, and only when every agent in the
comparison ran against the same one.
"""

import json

from tau2.data_model.simulation import TextRunConfig
from tau2.domains.appliance_care.environment import get_environment
from tau2.domains.appliance_care.utils import APPLIANCE_CARE_VERSION
from tau2.runner.helpers import get_environment_info, get_info


def test_the_environment_reports_the_version():
    assert get_environment().get_benchmark_version() == APPLIANCE_CARE_VERSION


def test_get_info_carries_the_version():
    """The same call the runner makes when it builds run metadata."""
    info = get_environment_info("appliance_care")
    assert info.benchmark_version == APPLIANCE_CARE_VERSION


def test_the_ablation_domain_is_versioned_too():
    """The no-manuals variant is scored against the same corpus generation."""
    info = get_environment_info("appliance_care-no-manuals")
    assert info.benchmark_version == APPLIANCE_CARE_VERSION
    assert info.domain_name == "appliance_care-no-manuals"


def test_run_metadata_records_the_version():
    """Build the run `Info` exactly as a run does, and check it is stamped."""
    config = TextRunConfig(domain="appliance_care", task_set_name="appliance_care")
    info = get_info(config)
    assert info.environment_info.benchmark_version == APPLIANCE_CARE_VERSION


def test_the_version_survives_serialization_into_results_json():
    """The value must be present in the written file, not merely in memory."""
    config = TextRunConfig(domain="appliance_care", task_set_name="appliance_care")
    payload = json.loads(get_info(config).model_dump_json())
    assert payload["environment_info"]["benchmark_version"] == APPLIANCE_CARE_VERSION, (
        "results.json would not record which benchmark version produced the scores"
    )


def test_a_domain_without_a_version_reports_none_rather_than_guessing():
    """Absence must read as absence — never as some other domain's version."""
    assert get_environment_info("mock").benchmark_version is None


def test_the_recorded_version_matches_the_declared_constant():
    """Guards against a hardcoded string drifting away from the constant."""
    recorded = get_environment_info("appliance_care").benchmark_version
    assert recorded == APPLIANCE_CARE_VERSION
    assert recorded is not None and recorded.startswith("v")
