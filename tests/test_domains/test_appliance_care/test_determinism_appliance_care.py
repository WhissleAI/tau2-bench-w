"""Determinism tests for the appliance_care domain.

The primary score is a hash comparison over the whole support database plus the
hidden appliance state. Anything non-deterministic in a write path — a uuid, a
wall-clock timestamp, an unstable ordering — silently turns every task into a
coin flip. These tests pin that down.
"""

import re

from tau2.domains.appliance_care.environment import get_environment, get_tasks
from tau2.domains.appliance_care.utils import get_now, get_today


def test_fresh_environments_hash_identically():
    """Two freshly built environments must be byte-identical."""
    a, b = get_environment(), get_environment()
    assert a.get_db_hash() == b.get_db_hash()
    assert a.get_user_db_hash() == b.get_user_db_hash()


def test_identical_write_sequences_hash_identically():
    """The same writes in the same order must produce the same database."""
    hashes = []
    for _ in range(3):
        env = get_environment()
        env.tools.create_support_case(
            appliance_id="APP-001", category="drainage", summary="will not drain"
        )
        env.tools.schedule_service(
            case_id="CASE-001",
            date="2026-03-05",
            window="morning",
            visit_type="warranty",
        )
        env.tools.record_resolution(
            appliance_id="APP-001",
            outcome="service_scheduled",
            steps_taken=["checked the filter"],
            manual_id_used="northwindnw2200washer",
        )
        hashes.append(env.get_db_hash())
    assert len(set(hashes)) == 1, f"non-deterministic writes: {hashes}"


def test_generated_ids_are_sequential_not_random():
    """Ids must be a deterministic counter, not a uuid."""
    env = get_environment()
    first = env.tools.create_support_case(
        appliance_id="APP-001", category="drainage", summary="one"
    )
    second = env.tools.create_support_case(
        appliance_id="APP-001", category="electrical", summary="two"
    )
    assert first.case_id == "CASE-001"
    assert second.case_id == "CASE-002"
    appointment = env.tools.schedule_service(
        case_id="CASE-001", date="2026-03-05", window="morning", visit_type="warranty"
    )
    assert appointment.appointment_id == "APPT-001"
    resolution = env.tools.record_resolution(
        appliance_id="APP-001", outcome="unresolved", steps_taken=[]
    )
    assert resolution.resolution_id == "RES-001"


def test_no_uuid_or_wallclock_in_domain_sources():
    """Guard against reintroducing a uuid or a live clock in a write path."""
    import tau2.domains.appliance_care.tools as tools_mod
    import tau2.domains.appliance_care.user_tools as user_tools_mod

    for module in (tools_mod, user_tools_mod):
        src = open(module.__file__, encoding="utf-8").read()
        # Match real usage, not the word appearing in a docstring explaining why
        # we avoid it.
        assert not re.search(r"^\s*import uuid|uuid4\(|uuid\.", src, re.M), (
            f"{module.__name__} uses uuid"
        )
        assert not re.search(r"datetime\.now\(|date\.today\(|time\.time\(", src), (
            f"{module.__name__} reads the wall clock"
        )


def test_clock_is_frozen():
    """The domain clock must not move between calls."""
    assert get_now() == get_now()
    assert get_today() == get_today()
    assert get_today().isoformat() == "2026-03-02"


def test_manual_search_is_stable_across_environments():
    """Retrieval must not depend on dict ordering or any random seed."""
    runs = []
    for _ in range(3):
        env = get_environment()
        hits = env.tools.library.search("drain filter blocked will not drain")
        runs.append([(h[1], h[0].section_id, h[2]) for h in hits])
    assert runs[0] == runs[1] == runs[2], "manual search is not deterministic"


def test_every_task_initializes_deterministically():
    """Replaying a task's initialization twice must give the same hidden state."""
    for task in get_tasks("base"):
        hashes = []
        for _ in range(2):
            env = get_environment()
            for action in task.initial_state.initialization_actions or []:
                env.run_env_function_call(action)
            hashes.append(env.get_user_db_hash())
        assert len(set(hashes)) == 1, f"{task.id} initializes non-deterministically"
