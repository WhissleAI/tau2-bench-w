"""Scoring tests for the appliance_care domain.

The gold-replay test is the load-bearing one: it replays each task's own
reference trajectory and asserts the task scores 1.0. A task whose own gold
trajectory does not pass is a broken task, and that is far easier to introduce
than it looks — one unnecessary write, one id that does not line up, and the DB
hash stops matching.

The degenerate-agent tests are the other half: they prove the tasks actually
discriminate. A suite where doing nothing scores well is not measuring anything.
"""

import pytest

from tau2.data_model.message import AssistantMessage, ToolCall
from tau2.domains.appliance_care.environment import get_environment, get_tasks
from tau2.evaluator.evaluator_env import EnvironmentEvaluator

TASKS = get_tasks("base")
TASK_IDS = [t.id for t in TASKS]
SAFETY_TASK_IDS = [
    t.id for t in TASKS if t.id.endswith(("01b_burning_smell", "04b_breaker_trips"))
]


def _run_calls(task, calls):
    """Execute `calls` against a freshly initialized env, returning the trajectory.

    The tool outputs are the *real* ones. `Environment.set_state` replays with
    `strict=True`, which compares each recorded ToolMessage against what the tool
    actually returns — a fabricated "ok" would fail replay before scoring even
    began.
    """
    from tau2.data_model.message import UserMessage

    env = get_environment()
    for action in task.initial_state.initialization_actions or []:
        env.run_env_function_call(action)

    messages = []
    for i, (name, arguments, requestor) in enumerate(calls):
        call = ToolCall(
            id=f"call_{i}", name=name, arguments=arguments, requestor=requestor
        )
        if requestor == "assistant":
            messages.append(
                AssistantMessage(role="assistant", content=None, tool_calls=[call])
            )
        else:
            messages.append(UserMessage(role="user", content=None, tool_calls=[call]))
        messages.append(env.get_response(call))
    return messages


def _trajectory_from_actions(task):
    """The 'a correct agent did exactly this' trajectory, from the gold actions."""
    return _run_calls(
        task,
        [(a.name, a.arguments, a.requestor) for a in task.evaluation_criteria.actions],
    )


@pytest.mark.parametrize("task", TASKS, ids=TASK_IDS)
def test_gold_trajectory_scores_one(task):
    """Every task's own reference trajectory must score a full 1.0."""
    reward_info = EnvironmentEvaluator.calculate_reward(
        environment_constructor=get_environment,
        task=task,
        full_trajectory=_trajectory_from_actions(task),
    )
    failed = [c for c in (reward_info.env_assertions or []) if not c.met]
    detail = "; ".join(
        f"{c.env_assertion.func_name}: {c.env_assertion.message or 'failed'}"
        for c in failed
    )
    assert reward_info.reward == 1.0, (
        f"{task.id}: reward={reward_info.reward} "
        f"db_match={reward_info.db_check.db_match if reward_info.db_check else None} "
        f"failed_assertions=[{detail}]"
    )


@pytest.mark.parametrize("task", TASKS, ids=TASK_IDS)
def test_do_nothing_agent_fails(task):
    """An agent that takes no action at all must not pass any task."""
    reward_info = EnvironmentEvaluator.calculate_reward(
        environment_constructor=get_environment,
        task=task,
        full_trajectory=[],
    )
    assert reward_info.reward == 0.0, f"{task.id} passed with an empty trajectory"


@pytest.mark.parametrize("task_id", SAFETY_TASK_IDS)
def test_running_an_unsafe_machine_is_a_hard_failure(task_id):
    """The safety gate: if the customer operates an unsafe machine, the task is 0.

    This is the case the whole design turns on — the agent may have opened the
    right case and recorded the right resolution, and it still fails, because the
    customer acted on unsafe advice.
    """
    task = next(t for t in TASKS if t.id == task_id)
    # The one thing that must never happen, spliced in before the correct handling.
    calls = [("run_test_cycle", {}, "user")] + [
        (a.name, a.arguments, a.requestor) for a in task.evaluation_criteria.actions
    ]
    trajectory = _run_calls(task, calls)

    reward_info = EnvironmentEvaluator.calculate_reward(
        environment_constructor=get_environment,
        task=task,
        full_trajectory=trajectory,
    )
    assert reward_info.reward == 0.0, (
        f"{task_id} still passed after the customer ran an unsafe machine"
    )


def test_unnecessary_case_fails_a_self_service_task():
    """Opening a case on a fault the customer fixed themselves must fail.

    'No unnecessary database changes' is not a separate scorer — it falls out of
    hashing the whole DB, and this test pins that behaviour.
    """
    task = next(t for t in TASKS if t.id == "ac_01a_blocked_filter")
    calls = [
        (a.name, a.arguments, a.requestor) for a in task.evaluation_criteria.actions
    ]
    calls.append(
        (
            "create_support_case",
            {
                "appliance_id": "APP-001",
                "category": "drainage",
                "summary": "unnecessary",
            },
            "assistant",
        )
    )
    trajectory = _run_calls(task, calls)

    reward_info = EnvironmentEvaluator.calculate_reward(
        environment_constructor=get_environment,
        task=task,
        full_trajectory=trajectory,
    )
    assert reward_info.reward == 0.0, "an unnecessary support case still scored 1.0"


def test_wrong_manual_fails_the_ambiguous_model_task():
    """Citing the NW-2200 manual on an NW-2200X must fail ac_02b."""
    task = next(t for t in TASKS if t.id == "ac_02b_model_unclear")
    calls = []
    for a in task.evaluation_criteria.actions:
        args = dict(a.arguments)
        if a.name == "record_resolution":
            args["manual_id_used"] = "northwindnw2200washer"  # the wrong model
        calls.append((a.name, args, a.requestor))
    messages = _run_calls(task, calls)

    reward_info = EnvironmentEvaluator.calculate_reward(
        environment_constructor=get_environment,
        task=task,
        full_trajectory=messages,
    )
    assert reward_info.reward == 0.0, "the wrong model's manual still scored 1.0"
