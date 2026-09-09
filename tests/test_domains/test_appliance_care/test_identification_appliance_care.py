"""The agent must RESOLVE the appliance record, not infer or guess it.

An error code, a model number or a serial number is something the customer read out
loud. It is reported input, not proof of identity: a customer can misread a digit,
read the label of a different machine, or quote a code from a web search. Only
`list_owned_appliances`, which maps a verified customer to the internal ids they
actually own, resolves an `appliance_id`.

This mattered enough to pin down because final-state scoring cannot see it. An agent
that writes `record_resolution(appliance_id="APP-001")` with no lookup at all leaves
a database byte-identical to one that did the work properly — same resolution, same
outcome, same manual, same hash. `test_guessing_the_appliance_id_fails_only_on_action`
demonstrates exactly that: DB and ENV_ASSERTION both score 1.0 for the shortcut, and
only the ACTION component separates it from the gold path.

An earlier attempt at this check recorded lookups into the database and asserted over
that log. It cannot work: `Environment.set_state` skips non-mutating tools when
replaying a trajectory, so a READ tool never runs during evaluation and its audit
trail is always empty. ACTION is the component that inspects the trajectory itself,
which is why the requirement lives in `evaluation_criteria.actions` plus
`reward_basis`.
"""

from __future__ import annotations

import pytest

from tau2.data_model.message import AssistantMessage, ToolCall, UserMessage
from tau2.data_model.simulation import SimulationRun, TerminationReason
from tau2.data_model.tasks import Action
from tau2.domains.appliance_care.environment import get_environment, get_tasks
from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
from tau2.evaluator.evaluator_action import _check_actions

# The chain that turns a caller into a verified internal record.
RESOLUTION_CHAIN = {
    "get_customer_by_phone",
    "get_customer_by_name",
    "list_owned_appliances",
    "get_appliance_details",
}

# Tools that take an internal appliance_id and change state.
APPLIANCE_ID_WRITES = {
    "create_support_case",
    "escalate_safety_issue",
    "record_resolution",
}


@pytest.fixture(scope="module")
def tasks():
    return {t.id: t for t in get_tasks("base")}


def _gold(task):
    return [
        (a.name, a.arguments, a.requestor) for a in task.evaluation_criteria.actions
    ]


def _run(task, calls):
    env = get_environment()
    for action in task.initial_state.initialization_actions or []:
        env.run_env_function_call(action)
    messages = []
    for index, (name, arguments, requestor) in enumerate(calls):
        call = ToolCall(
            id=f"c{index}", name=name, arguments=arguments, requestor=requestor
        )
        message_type = AssistantMessage if requestor == "assistant" else UserMessage
        messages.append(message_type(role=requestor, content=None, tool_calls=[call]))
        messages.append(env.get_response(call))
    return messages


def _score(task, calls):
    sim = SimulationRun(
        id=f"{task.id}-probe",
        task_id=task.id,
        messages=_run(task, calls),
        start_time="2026-01-01T00:00:00",
        end_time="2026-01-01T00:01:00",
        duration=1.0,
        termination_reason=TerminationReason.USER_STOP,
    )
    return evaluate_simulation(
        simulation=sim,
        task=task,
        evaluation_type=EvaluationType.ALL,
        solo_mode=False,
        domain="appliance_care",
    )


# ── the requirement is actually wired up ─────────────────────────────────────


def test_every_task_gates_on_action(tasks):
    """Without ACTION in reward_basis the identification chain is unenforced."""
    for task in tasks.values():
        basis = [
            r.value if hasattr(r, "value") else str(r)
            for r in (task.evaluation_criteria.reward_basis or [])
        ]
        assert "ACTION" in basis, f"{task.id} does not gate on ACTION"


def test_customer_acknowledgement_is_not_a_mandatory_gold_action(tasks):
    """A simulator wording choice must not turn correct safety advice into zero."""
    for task in tasks.values():
        names = [action.name for action in task.evaluation_criteria.actions]
        assert "acknowledge_stop_using" not in names, task.id


def test_every_task_that_writes_must_first_list_owned_appliances(tasks):
    """A gold path that writes to an appliance must show where the id came from."""
    for task in tasks.values():
        names = [a.name for a in task.evaluation_criteria.actions]
        if not APPLIANCE_ID_WRITES.intersection(names):
            continue
        assert "list_owned_appliances" in names, (
            f"{task.id} writes to an appliance_id it never resolved"
        )
        first_write = min(i for i, n in enumerate(names) if n in APPLIANCE_ID_WRITES)
        assert names.index("list_owned_appliances") < first_write, (
            f"{task.id} writes before resolving the appliance"
        )

        customer_lookup = next(
            action
            for action in task.evaluation_criteria.actions
            if action.name == "get_customer_by_phone"
        )
        assert {alternative.name for alternative in customer_lookup.alternatives} == {
            "get_customer_by_name"
        }
        assert customer_lookup.must_precede == ["list_owned_appliances"]

        appliance_lookup = task.evaluation_criteria.actions[
            names.index("list_owned_appliances")
        ]
        assert set(appliance_lookup.must_precede) == APPLIANCE_ID_WRITES


def test_no_task_lets_an_error_code_stand_in_for_identification(tasks):
    """No gold path may reach a write via lookup_error_code without the chain.

    E:23 narrows which manual documents that code. It does not establish which
    machine the customer owns, and it must never be the only step before a write.
    """
    for task in tasks.values():
        names = [a.name for a in task.evaluation_criteria.actions]
        if not APPLIANCE_ID_WRITES.intersection(names):
            continue
        assert RESOLUTION_CHAIN.intersection(names), (
            f"{task.id} reaches a write with no identification step at all"
        )


# ── and it actually bites ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "task_id",
    ["ac_01a_blocked_pump", "ac_02b_model_unclear", "ac_05a_two_lint_filters"],
)
def test_guessing_the_appliance_id_fails_only_on_action(tasks, task_id):
    """The shortcut leaves an identical database. Only ACTION can catch it."""
    task = tasks[task_id]
    gold = _gold(task)
    guessed = [c for c in gold if c[0] not in RESOLUTION_CHAIN]

    gold_result = _score(task, gold)
    guess_result = _score(task, guessed)

    def part(result, key):
        return {
            (k.value if hasattr(k, "value") else str(k)): v
            for k, v in (result.reward_breakdown or {}).items()
        }.get(key)

    assert gold_result.reward == 1.0, "the gold path must still pass"
    assert guess_result.reward == 0.0, "guessing the appliance_id must fail"

    # The point of the whole check: the database cannot tell these apart.
    assert part(guess_result, "DB") == 1.0, (
        "final state is identical — if this ever differs, this test is no longer "
        "demonstrating why ACTION is required"
    )
    assert part(guess_result, "ENV_ASSERTION") == 1.0
    assert part(guess_result, "ACTION") == 0.0
    assert part(gold_result, "ACTION") == 1.0


@pytest.mark.parametrize("task_id", sorted(task.id for task in get_tasks("base")))
def test_verified_name_lookup_is_equivalent_to_verified_phone(tasks, task_id):
    """Either verified identifier may start the same customer-resolution chain."""
    task = tasks[task_id]
    calls = []
    for action in task.evaluation_criteria.actions:
        if action.name == "get_customer_by_phone":
            alternative = action.alternatives[0]
            calls.append((alternative.name, alternative.arguments, action.requestor))
        else:
            calls.append((action.name, action.arguments, action.requestor))
    result = _score(task, calls)
    assert result.reward == 1.0


@pytest.mark.parametrize(
    "task_id",
    ["ac_01a_blocked_pump", "ac_02b_model_unclear", "ac_05a_two_lint_filters"],
)
def test_identity_resolution_after_the_first_write_is_too_late(tasks, task_id):
    """A correct lookup performed after a write must not validate a prior guess."""
    task = tasks[task_id]
    gold = _gold(task)
    identity = [call for call in gold if call[0] in RESOLUTION_CHAIN]
    without_identity = [call for call in gold if call[0] not in RESOLUTION_CHAIN]
    first_write = next(
        index
        for index, (name, _arguments, _requestor) in enumerate(without_identity)
        if name in APPLIANCE_ID_WRITES
    )
    late = (
        without_identity[: first_write + 1]
        + identity
        + without_identity[first_write + 1 :]
    )

    result = _score(task, late)
    breakdown = {
        (key.value if hasattr(key, "value") else str(key)): value
        for key, value in (result.reward_breakdown or {}).items()
    }
    assert result.reward == 0.0
    assert breakdown["DB"] == 1.0
    assert breakdown["ENV_ASSERTION"] == 1.0
    assert breakdown["ACTION"] == 0.0


def test_customer_lookup_after_listing_appliances_is_out_of_order(tasks):
    task = tasks["ac_01a_blocked_pump"]
    calls = _gold(task)
    customer_index = next(
        index for index, call in enumerate(calls) if call[0] == "get_customer_by_phone"
    )
    appliance_index = next(
        index for index, call in enumerate(calls) if call[0] == "list_owned_appliances"
    )
    calls[customer_index], calls[appliance_index] = (
        calls[appliance_index],
        calls[customer_index],
    )

    result = _score(task, calls)
    breakdown = {
        (key.value if hasattr(key, "value") else str(key)): value
        for key, value in (result.reward_breakdown or {}).items()
    }
    assert result.reward == 0.0
    assert breakdown["ACTION"] == 0.0


def test_wrong_name_does_not_satisfy_the_lookup_alternative(tasks):
    task = tasks["ac_01a_blocked_pump"]
    calls = [
        (
            "get_customer_by_name" if name == "get_customer_by_phone" else name,
            {"name": "Not Dana"} if name == "get_customer_by_phone" else arguments,
            requestor,
        )
        for name, arguments, requestor in _gold(task)
    ]
    result = _score(task, calls)
    breakdown = {
        (key.value if hasattr(key, "value") else str(key)): value
        for key, value in (result.reward_breakdown or {}).items()
    }
    assert result.reward == 0.0
    assert breakdown["ACTION"] == 0.0


def test_actions_without_new_constraints_keep_the_old_matching_behavior():
    """Other Tau domains remain opt-in and backward-compatible."""
    action = Action(
        action_id="legacy",
        name="legacy_lookup",
        arguments={"identifier": "A-1"},
    )
    calls = [
        ToolCall(
            id="c0",
            name="unrelated_write",
            arguments={},
            requestor="assistant",
        ),
        ToolCall(
            id="c1",
            name="legacy_lookup",
            arguments={"identifier": "A-1"},
            requestor="assistant",
        ),
    ]
    [check] = _check_actions(calls, [action])
    assert check.action_match is True
    assert check.action_reward == 1.0


@pytest.mark.parametrize(
    ("task_id", "visit_type"),
    [
        ("ac_03a_warranty_active", "warranty"),
        ("ac_03b_warranty_expired", "billable"),
    ],
)
def test_valid_appointment_slots_are_flexible(tasks, task_id, visit_type):
    """No availability calendar makes the reference date uniquely correct."""
    task = tasks[task_id]
    calls = []
    for name, arguments, requestor in _gold(task):
        arguments = dict(arguments)
        if name == "schedule_service":
            arguments.update(date="2026-03-12", window="afternoon")
        calls.append((name, arguments, requestor))
    result = _score(task, calls)
    assert result.reward == 1.0
    breakdown = {
        (key.value if hasattr(key, "value") else str(key)): value
        for key, value in (result.reward_breakdown or {}).items()
    }
    assert breakdown["DB"] == 1.0
    assert breakdown["ENV_ASSERTION"] == 1.0
    assert breakdown["ACTION"] == 1.0
