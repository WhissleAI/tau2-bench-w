"""The saved-flow adapter's contract with the orchestrator.

The single most dangerous mistake on this path is returning bridge-executed calls
as `tool_calls`: the orchestrator would route them to the environment and run
every write a second time. These tests pin the behaviour that prevents it.
"""

import os
from unittest.mock import patch

import pytest

from tau2.bridge.tool_bridge import ToolBridge
from tau2.data_model.message import AssistantMessage, UserMessage
from tau2.domains.appliance_care.environment import get_environment

TOKEN = "test-token-not-a-real-secret"
AUTH = f"Bearer {TOKEN}"

ENV_VARS = {
    "WHISSLE_AGENT_ID": "test-agent-id",
    "WHISSLE_API_KEY": "test-key-not-real",
    "WHISSLE_BASE": "https://example.invalid",
}


@pytest.fixture
def agent_and_bridge():
    from tau2.agent.whissle_flow_agent import WhissleFlowAgent

    env = get_environment()
    bridge = ToolBridge(environment=env, token=TOKEN)
    with patch.dict(os.environ, ENV_VARS):
        agent = WhissleFlowAgent(
            tools=env.get_tools(), domain_policy=env.get_policy(), bridge=bridge
        )
    return agent, bridge, env


def _reply(agent, bridge, text, tool_calls=()):
    """Drive one turn with a stubbed Whissle response.

    The stub stands in for the flow: it calls the bridge for each tool the flow
    would have called, then returns the flow's reply.
    """

    def fake_chat_turn(message, conversation_id):
        for name, args in tool_calls:
            bridge.handle(name, args, authorization=AUTH)
        return {"reply": text, "conversation_id": "conv-1", "flow": {"steps": []}}

    with patch.object(agent, "_chat_turn", side_effect=fake_chat_turn):
        state = agent.get_init_state()
        return agent.generate_next_message(
            UserMessage(role="user", content="my washer won't drain"), state
        )


def test_the_reply_never_carries_tool_calls(agent_and_bridge):
    """If this ever returns tool_calls, every write runs twice."""
    agent, bridge, _env = agent_and_bridge
    msg, _state = _reply(
        agent,
        bridge,
        "Let me look that up.",
        tool_calls=[("search_manuals", {"query": "drain filter"})],
    )
    assert isinstance(msg, AssistantMessage)
    assert not msg.is_tool_call()
    assert msg.content == "Let me look that up."


def test_executed_calls_are_handed_over_for_the_trajectory(agent_and_bridge):
    agent, bridge, _env = agent_and_bridge
    _reply(
        agent,
        bridge,
        "Found it.",
        tool_calls=[
            ("get_customer_by_phone", {"phone": "+14155550101"}),
            ("list_owned_appliances", {"customer_id": "CUST-001"}),
        ],
    )
    records = agent.drain_trajectory_records()
    assert len(records) == 4  # two (call, result) pairs
    assert records[0].tool_calls[0].name == "get_customer_by_phone"
    assert records[2].tool_calls[0].name == "list_owned_appliances"


def test_draining_twice_yields_nothing_the_second_time(agent_and_bridge):
    """Double-drain would duplicate every call in the scored trajectory."""
    agent, bridge, _env = agent_and_bridge
    _reply(agent, bridge, "ok", tool_calls=[("search_manuals", {"query": "drain"})])
    assert len(agent.drain_trajectory_records()) == 2
    assert agent.drain_trajectory_records() == []


def test_a_turn_with_no_tool_calls_yields_no_records(agent_and_bridge):
    agent, bridge, _env = agent_and_bridge
    _reply(agent, bridge, "Which model is it?")
    assert agent.drain_trajectory_records() == []


def test_an_empty_reply_is_replaced_not_sent_blank(agent_and_bridge):
    agent, bridge, _env = agent_and_bridge
    msg, _ = _reply(agent, bridge, "")
    assert msg.content


def test_the_adapter_requires_credentials():
    from tau2.agent.whissle_flow_agent import WhissleFlowAgent

    env = get_environment()
    with patch.dict(os.environ, {"WHISSLE_AGENT_ID": "", "WHISSLE_API_KEY": ""}):
        with pytest.raises(ValueError):
            WhissleFlowAgent(tools=env.get_tools(), domain_policy=env.get_policy())


def test_the_factory_builds_a_bridge_over_the_scored_environment():
    from tau2.agent.whissle_flow_agent import create_whissle_flow_agent

    env = get_environment()
    with patch.dict(os.environ, dict(ENV_VARS, TAU_BRIDGE_TOKEN=TOKEN)):
        agent = create_whissle_flow_agent(
            tools=env.get_tools(), domain_policy=env.get_policy(), environment=env
        )
    assert agent.bridge is not None
    # The same object, not a copy — otherwise the scored DB is not the one written.
    assert agent.bridge.environment is env


def test_the_factory_warns_rather_than_opening_an_unauthenticated_bridge():
    from tau2.agent.whissle_flow_agent import create_whissle_flow_agent

    env = get_environment()
    stripped = dict(ENV_VARS)
    with patch.dict(os.environ, stripped, clear=False):
        os.environ.pop("TAU_BRIDGE_TOKEN", None)
        agent = create_whissle_flow_agent(
            tools=env.get_tools(), domain_policy=env.get_policy(), environment=env
        )
    assert agent.bridge is None


def test_the_orchestrator_hook_is_wired():
    """The core hook must call the method this adapter exposes."""
    import inspect

    from tau2.orchestrator.orchestrator import Orchestrator

    source = inspect.getsource(Orchestrator.step)
    assert "drain_trajectory_records" in source
    assert source.index("drain_trajectory_records") < source.index(
        "self.trajectory.append(agent_msg)"
    ), "records must be spliced in BEFORE the reply"
