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
from tau2.domains.appliance_care.environment import get_environment, get_tasks
from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation

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
    ["ac_01a_blocked_filter", "ac_02b_model_unclear", "ac_05a_manual_has_procedure"],
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
