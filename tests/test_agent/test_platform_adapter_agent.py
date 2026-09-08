import json
import time
from dataclasses import dataclass

import pytest

from tau2.agent.platform_adapter_agent import PlatformAdapterAgent
from tau2.data_model.message import (
    AssistantMessage,
    MultiToolMessage,
    ToolMessage,
    UserMessage,
)
from tau2.data_model.simulation import TerminationReason
from tau2.domains.appliance_care.environment import get_environment, get_tasks
from tau2.orchestrator.orchestrator import Orchestrator, Role


@dataclass(frozen=True)
class Call:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class Result:
    turn_id: str
    message: str | None
    tool_calls: list[Call]
    served_model: str
    latency_ms: float
    usage: dict | None = None
    cost: float | None = None


@dataclass(frozen=True)
class Teardown:
    agent_deleted: bool = True
    knowledge_base_deleted: bool = True
    errors: tuple = ()


class Session:
    def __init__(self, tool_schemas, *, fail_cleanup=False):
        self.tool_schemas = tool_schemas
        self.fail_cleanup = fail_cleanup
        self.turns = []
        self.closed = False
        self.count = 0

    def open(self):
        return self

    def turn(self, messages):
        self.count += 1
        if self.count == 1:
            result = Result(
                turn_id="turn-1",
                message=None,
                tool_calls=[
                    Call(
                        id="call-1",
                        name="get_customer_by_phone",
                        arguments={"phone": "+1-202-555-0101"},
                    )
                ],
                served_model="fixture-model",
                latency_ms=12.5,
                usage={"input_tokens": 10, "output_tokens": 2},
                cost=0.001,
            )
        else:
            assert messages[-1]["role"] == "tool"
            result = Result(
                turn_id="turn-2",
                message="I found the customer record.",
                tool_calls=[],
                served_model="fixture-model",
                latency_ms=5.0,
            )
        self.turns.append(
            {
                "turn_id": result.turn_id,
                "served_model": result.served_model,
                "harness_latency_ms": 20.0 if self.count == 1 else 7.5,
                "citations": "NOT_SUPPORTED",
            }
        )
        return result

    def close(self):
        if self.fail_cleanup:
            raise RuntimeError("cleanup failed")
        self.closed = True
        return Teardown()

    def evidence(self):
        assert self.closed
        return {
            "ingestion": [],
            "turns": self.turns,
            "teardown": {
                "agent_deleted": True,
                "knowledge_base_deleted": True,
                "errors": [],
            },
        }


def _agent(tmp_path, *, fail_cleanup=False):
    env = get_environment()
    made = []

    def factory(**kwargs):
        session = Session(kwargs["tool_schemas"], fail_cleanup=fail_cleanup)
        made.append(session)
        return session

    agent = PlatformAdapterAgent(
        tools=env.get_tools(),
        domain_policy=env.get_policy(),
        task=get_tasks()[0],
        session_factory=factory,
        platform_id="local_fixture",
        evidence_dir=tmp_path,
    )
    return agent, env, made


def test_tau_executes_platform_tool_calls_and_returns_results(tmp_path):
    agent, env, made = _agent(tmp_path)
    state = agent.get_init_state()
    reply, state = agent.generate_next_message(
        UserMessage(role="user", content="My phone is +1-202-555-0101"), state
    )
    assert reply.tool_calls[0].name == "get_customer_by_phone"
    result = env.get_response(reply.tool_calls[0])
    reply, state = agent.generate_next_message(result, state)
    assert reply.content == "I found the customer record."
    assert reply.raw_data["served_model"] == "fixture-model"
    assert reply.generation_time_seconds == 0.0075
    agent.stop(None, state)

    assert made[0].closed
    evidence_files = list(tmp_path.glob("*.json"))
    assert len(evidence_files) == 1
    evidence = json.loads(evidence_files[0].read_text())
    assert evidence["task_id"] == "ac_01a_blocked_pump"
    assert len(evidence["turns"]) == 2
    assert reply.raw_data["platform_teardown"]["agent_deleted"]


def test_manual_tools_are_withheld_but_tau_keeps_other_tools(tmp_path):
    agent, _env, made = _agent(tmp_path)
    names = {schema["function"]["name"] for schema in made[0].tool_schemas}
    assert "search_manuals" not in names
    assert "open_manual_section" not in names
    assert "lookup_error_code" not in names
    assert "get_customer_by_phone" in names
    agent.get_init_state()
    agent.stop()


def test_multiple_tool_results_are_normalized_in_order(tmp_path):
    agent, _env, _made = _agent(tmp_path)
    state = agent.get_init_state()
    state.messages = []
    incoming = MultiToolMessage(
        role="tool",
        tool_messages=[
            ToolMessage(id="a", role="tool", content="one"),
            ToolMessage(id="b", role="tool", content="two", error=True),
        ],
    )
    _reply, state = agent.generate_next_message(incoming, state)
    assert state.messages[0]["tool_call_id"] == "a"
    assert state.messages[1]["tool_call_id"] == "b"
    assert state.messages[1]["error"] is True
    agent.stop()


def test_cleanup_failure_is_not_hidden(tmp_path):
    agent, _env, _made = _agent(tmp_path, fail_cleanup=True)
    agent.get_init_state()
    with pytest.raises(RuntimeError, match="cleanup failed"):
        agent.stop()


def test_tau_finalization_propagates_required_cleanup_failure(tmp_path):
    agent, env, _made = _agent(tmp_path, fail_cleanup=True)
    task = get_tasks()[0]

    class User:
        def generate_next_message(self, message, state):
            return UserMessage(role="user", content="done"), state

        def stop(self, message=None, state=None):
            return None

    orchestrator = Orchestrator(
        domain="appliance_care",
        agent=agent,
        user=User(),
        environment=env,
        task=task,
    )
    orchestrator.agent_state = agent.get_init_state()
    orchestrator.to_role = Role.USER
    orchestrator.message = AssistantMessage(role="assistant", content="Done")
    orchestrator.termination_reason = TerminationReason.USER_STOP
    orchestrator._run_start_perf = time.perf_counter()
    orchestrator._run_start_time = "fixture-start"

    with pytest.raises(RuntimeError, match="cleanup failed"):
        orchestrator._finalize()
