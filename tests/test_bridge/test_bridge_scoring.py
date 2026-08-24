"""End-to-end proof that bridge-executed tool calls are scored correctly.

The question these tests exist to answer is narrow and load-bearing: when Whissle
runs its saved flow and calls tau2's tools over the bridge, does tau2 end up
scoring the *real* final database state, exactly once per call?

Everything here runs offline against a local Environment. No Whissle contact.
"""

import pytest

from tau2.bridge.tool_bridge import ToolBridge, build_trajectory_records
from tau2.data_model.message import AssistantMessage, ToolCall, UserMessage
from tau2.domains.appliance_care.environment import get_environment, get_tasks
from tau2.evaluator.evaluator_action import ActionEvaluator
from tau2.evaluator.evaluator_env import EnvironmentEvaluator

TOKEN = "test-token-not-a-real-secret"
AUTH = f"Bearer {TOKEN}"
TASK_ID = "ac_01a_blocked_pump"


def _task(task_id=TASK_ID):
    return next(t for t in get_tasks("base") if t.id == task_id)


def _initialized_env(task):
    env = get_environment()
    for action in task.initial_state.initialization_actions or []:
        env.run_env_function_call(action)
    return env


def _simulate_flow_run(task):
    """Replay the gold trajectory the way a saved-flow run actually produces it.

    Agent-side tools go through the BRIDGE (as Whissle would call them); user-side
    customer actions go through the environment directly (as the orchestrator
    runs them). The assembled trajectory is what tau2 would score.
    """
    env = _initialized_env(task)
    bridge = ToolBridge(environment=env, token=TOKEN)
    trajectory = []
    user_call_seq = 0

    for action in task.evaluation_criteria.actions:
        if action.requestor == "assistant":
            bridge.handle(action.name, action.arguments, authorization=AUTH)
            # Whissle replies once the flow finishes its tool work for the turn;
            # the orchestrator splices the drained records in ahead of that reply.
            trajectory.extend(build_trajectory_records(bridge.drain()))
            trajectory.append(
                AssistantMessage(role="assistant", content="Let me check that.")
            )
        else:
            user_call_seq += 1
            call = ToolCall(
                id=f"user_{user_call_seq}",
                name=action.name,
                arguments=action.arguments,
                requestor="user",
            )
            trajectory.append(UserMessage(role="user", content=None, tool_calls=[call]))
            trajectory.append(env.get_response(call))

    return env, bridge, trajectory


# ── the core claim ──────────────────────────────────────────────────────────


def test_a_bridge_run_scores_a_full_reward():
    """The whole point: tools called over the bridge produce a passing task."""
    task = _task()
    _env, _bridge, trajectory = _simulate_flow_run(task)

    reward_info = EnvironmentEvaluator.calculate_reward(
        environment_constructor=get_environment,
        task=task,
        full_trajectory=trajectory,
    )
    failed = [c for c in (reward_info.env_assertions or []) if not c.met]
    assert reward_info.reward == 1.0, (
        f"db_match={reward_info.db_check.db_match if reward_info.db_check else None} "
        f"failed={[c.env_assertion.func_name for c in failed]}"
    )


def test_the_evaluator_can_replay_the_recorded_calls():
    """Strict replay compares recorded output against re-execution, byte for byte.

    This is what would break if the bridge recorded anything it did not actually
    get back from the tool.
    """
    task = _task()
    _env, _bridge, trajectory = _simulate_flow_run(task)

    fresh = get_environment()
    fresh.set_state(
        initialization_data=task.initial_state.initialization_data,
        initialization_actions=task.initial_state.initialization_actions,
        message_history=trajectory,
        strict=True,  # any drift raises
    )
    assert len(fresh.tools.db.resolutions) == 1


def test_action_evaluator_sees_every_bridge_call():
    task = _task()
    _env, bridge, trajectory = _simulate_flow_run(task)

    extracted = ActionEvaluator.extract_tool_calls(trajectory)
    names = [c.name for c in extracted]
    for record in bridge.records:
        assert record.tool_name in names

    reward_info = ActionEvaluator.calculate_reward(
        task=task, full_trajectory=trajectory
    )
    assert reward_info.reward == 1.0


# ── exactly once ────────────────────────────────────────────────────────────


def test_a_mutating_call_happens_once_and_is_recorded_once():
    task = _task()
    env, bridge, trajectory = _simulate_flow_run(task)

    # once in the live database
    assert len(env.tools.db.resolutions) == 1

    # once in the bridge log
    writes = [r for r in bridge.records if r.tool_name == "record_resolution"]
    assert len(writes) == 1

    # once in the scored trajectory
    calls = [
        c
        for c in ActionEvaluator.extract_tool_calls(trajectory)
        if c.name == "record_resolution"
    ]
    assert len(calls) == 1


def test_every_bridge_call_appears_exactly_once_in_the_trajectory():
    task = _task()
    _env, bridge, trajectory = _simulate_flow_run(task)

    ids = [c.id for c in ActionEvaluator.extract_tool_calls(trajectory)]
    for record in bridge.records:
        assert ids.count(record.call_id) == 1, (
            f"{record.tool_name} ({record.call_id}) appears {ids.count(record.call_id)} times"
        )


def test_double_execution_would_have_been_caught():
    """A guard on the guard: if the orchestrator re-ran a bridge call, the task fails.

    Without this, 'we prevented double execution' would be an untested claim —
    the suite would look identical whether or not the protection worked.
    """
    task = _task()
    env, bridge, trajectory = _simulate_flow_run(task)

    # Simulate exactly the bug: the orchestrator executes the write a second time.
    duplicate = ToolCall(
        id="duplicate_1",
        name="record_resolution",
        arguments={
            "appliance_id": "APP-001",
            "outcome": "resolved_self_service",
            "steps_taken": ["cleaned the drain filter"],
            "manual_id_used": "boschwat28400ucwasher",
        },
        requestor="assistant",
    )
    trajectory.append(
        AssistantMessage(role="assistant", content=None, tool_calls=[duplicate])
    )
    trajectory.append(env.get_response(duplicate))

    reward_info = EnvironmentEvaluator.calculate_reward(
        environment_constructor=get_environment,
        task=task,
        full_trajectory=trajectory,
    )
    assert reward_info.reward == 0.0, "a doubled write still scored as a pass"


# ── the negative cases still fail ───────────────────────────────────────────


def test_a_flow_that_calls_nothing_scores_zero():
    """A chatty agent that never touches a tool must not pass."""
    task = _task()
    trajectory = [
        AssistantMessage(role="assistant", content="Have you tried the filter?")
    ]
    reward_info = EnvironmentEvaluator.calculate_reward(
        environment_constructor=get_environment,
        task=task,
        full_trajectory=trajectory,
    )
    assert reward_info.reward == 0.0


def test_final_database_state_is_what_is_checked_not_the_transcript():
    """Perfect prose plus a wrong write is still a failure."""
    task = _task()
    env, bridge, trajectory = _simulate_flow_run(task)

    bridge.handle(
        "create_support_case",
        {"appliance_id": "APP-001", "category": "drainage", "summary": "unnecessary"},
        authorization=AUTH,
    )
    trajectory.extend(build_trajectory_records(bridge.drain()))
    trajectory.append(
        AssistantMessage(role="assistant", content="All sorted, have a good day!")
    )

    reward_info = EnvironmentEvaluator.calculate_reward(
        environment_constructor=get_environment,
        task=task,
        full_trajectory=trajectory,
    )
    assert reward_info.reward == 0.0


@pytest.mark.parametrize("task_id", ["ac_01a_blocked_pump", "ac_03b_warranty_expired"])
def test_bridge_runs_score_correctly_across_tasks(task_id):
    task = _task(task_id)
    _env, _bridge, trajectory = _simulate_flow_run(task)
    reward_info = EnvironmentEvaluator.calculate_reward(
        environment_constructor=get_environment,
        task=task,
        full_trajectory=trajectory,
    )
    assert reward_info.reward == 1.0, f"{task_id} did not score 1.0"
