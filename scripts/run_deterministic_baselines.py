"""Replay ApplianceCare gold and deliberately bad deterministic baselines."""

from __future__ import annotations

import json

from tau2.data_model.message import AssistantMessage, ToolCall, UserMessage
from tau2.domains.appliance_care.environment import get_environment, get_tasks
from tau2.evaluator.evaluator_env import EnvironmentEvaluator


def run_calls(task, calls):
    env = get_environment()
    for action in task.initial_state.initialization_actions or []:
        env.run_env_function_call(action)

    messages = []
    for index, (name, arguments, requestor) in enumerate(calls):
        call = ToolCall(
            id=f"call_{index}",
            name=name,
            arguments=arguments,
            requestor=requestor,
        )
        message_type = AssistantMessage if requestor == "assistant" else UserMessage
        messages.append(message_type(role=requestor, content=None, tool_calls=[call]))
        messages.append(env.get_response(call))
    return messages


def score(task, calls):
    result = EnvironmentEvaluator.calculate_reward(
        environment_constructor=get_environment,
        task=task,
        full_trajectory=run_calls(task, calls),
    )
    return {
        "reward": result.reward,
        "db_match": result.db_check.db_match if result.db_check else None,
        "failed_assertions": [
            assertion.env_assertion.func_name
            for assertion in (result.env_assertions or [])
            if not assertion.met
        ],
    }


def gold_calls(task):
    return [
        (action.name, action.arguments, action.requestor)
        for action in task.evaluation_criteria.actions
    ]


def appliance_id(task):
    for action in task.evaluation_criteria.actions:
        value = action.arguments.get("appliance_id")
        if value:
            return value
    raise ValueError(f"No appliance_id in {task.id}")


tasks = get_tasks("base")
rows = []

for task in tasks:
    app_id = appliance_id(task)
    variants = {
        "gold": gold_calls(task),
        "do_nothing": [],
        "always_refuse": [],
        "always_escalate": [
            (
                "create_support_case",
                {
                    "appliance_id": app_id,
                    "category": "general",
                    "summary": "Escalated without diagnosis",
                },
                "assistant",
            ),
            (
                "record_resolution",
                {
                    "appliance_id": app_id,
                    "outcome": "unresolved",
                    "steps_taken": [],
                },
                "assistant",
            ),
        ],
    }
    for behavior, calls in variants.items():
        rows.append({"task_id": task.id, "behavior": behavior, **score(task, calls)})


def task_by_id(task_id):
    return next(task for task in tasks if task.id == task_id)


for task_id in ("ac_01b_burning_smell", "ac_04b_breaker_trips"):
    task = task_by_id(task_id)
    calls = [("run_test_cycle", {}, "user"), *gold_calls(task)]
    rows.append({"task_id": task.id, "behavior": "unsafe_operation", **score(task, calls)})

task = task_by_id("ac_01a_blocked_filter")
calls = [
    *gold_calls(task),
    (
        "create_support_case",
        {
            "appliance_id": "APP-001",
            "category": "drainage",
            "summary": "Unnecessary support case",
        },
        "assistant",
    ),
]
rows.append({"task_id": task.id, "behavior": "unnecessary_write", **score(task, calls)})

task = task_by_id("ac_02b_model_unclear")
calls = []
for action in task.evaluation_criteria.actions:
    arguments = dict(action.arguments)
    if action.name == "record_resolution":
        arguments["manual_id_used"] = "northwindnw2200washer"
    calls.append((action.name, arguments, action.requestor))
rows.append({"task_id": task.id, "behavior": "wrong_manual", **score(task, calls)})

behaviors = sorted({row["behavior"] for row in rows})
summary = {
    behavior: {
        "evaluated": sum(row["behavior"] == behavior for row in rows),
        "reward_one": sum(
            row["behavior"] == behavior and row["reward"] == 1.0 for row in rows
        ),
        "reward_zero": sum(
            row["behavior"] == behavior and row["reward"] == 0.0 for row in rows
        ),
    }
    for behavior in behaviors
}

print(
    json.dumps(
        {
            "schema": "appliance_care.deterministic_baselines/v1",
            "task_count": len(tasks),
            "primary_scorer": "EnvironmentEvaluator final DB and hidden-state checks",
            "summary": summary,
            "results": rows,
        },
        indent=2,
        sort_keys=True,
    )
)
