"""The Whissle adapter must record the model the platform actually served.

tau2 stamps run-level `agent_info.llm` from the `--agent-llm` flag. This agent
never uses that flag - Whissle picks the model - so the header can be actively
wrong. A ten-task run was reported as `gpt-4.1-2025-04-14` while the endpoint had
been serving `claude-haiku-4-5-20251001` throughout. These tests pin the fix:
the served model is captured per response and travels with the transcript.
"""

import os
from unittest.mock import patch

import pytest

from tau2.data_model.message import UserMessage

ENV = {
    "WHISSLE_AGENT_ID": "test-agent",
    "WHISSLE_API_KEY": "test-key-not-real",
    "WHISSLE_BASE": "https://example.invalid",
}


def _agent():
    from tau2.agent.whissle_agent import WhissleAgent

    with patch.dict(os.environ, ENV):
        return WhissleAgent(tools=[], domain_policy="test policy")


def _reply(agent, payload):
    with patch.object(agent, "_turn", return_value=payload):
        state = agent.get_init_state()
        return agent.generate_next_message(
            UserMessage(role="user", content="hello"), state
        )


def test_a_text_reply_records_the_served_model():
    agent = _agent()
    msg, _ = _reply(
        agent,
        {
            "reply": "Hello there.",
            "model": "claude-haiku-4-5-20251001",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 60, "output_tokens": 98},
        },
    )
    assert msg.raw_data["served_model"] == "claude-haiku-4-5-20251001"
    assert msg.raw_data["endpoint"] == "/api/bench/agent-turn"
    assert msg.raw_data["stop_reason"] == "end_turn"
    assert msg.usage == {"input_tokens": 60, "output_tokens": 98}


def test_a_tool_call_reply_records_it_too():
    """The tool-call branch is a separate construction path and was easy to miss."""
    agent = _agent()
    msg, _ = _reply(
        agent,
        {
            "tool_calls": [{"id": "t1", "name": "search_manuals", "arguments": {}}],
            "model": "claude-haiku-4-5-20251001",
            "stop_reason": "tool_use",
        },
    )
    assert msg.is_tool_call()
    assert msg.raw_data["served_model"] == "claude-haiku-4-5-20251001"


def test_the_agent_accumulates_every_distinct_model_it_saw():
    agent = _agent()
    for model in ("claude-haiku-4-5-20251001", "claude-haiku-4-5-20251001", "other-1"):
        _reply(agent, {"reply": "ok", "model": model})
    assert agent.served_models == ["claude-haiku-4-5-20251001", "other-1"], (
        "a model switch mid-run must be visible, not averaged away"
    )


def test_a_response_without_a_model_field_is_recorded_as_unknown_not_invented():
    agent = _agent()
    msg, _ = _reply(agent, {"reply": "ok"})
    assert msg.raw_data["served_model"] is None
    assert agent.served_models == []


@pytest.mark.parametrize("payload_key", ["reply", "tool_calls"])
def test_provenance_never_claims_the_tau_default(payload_key):
    """The failure being prevented: reporting the flag instead of the fact."""
    agent = _agent()
    payload = {"model": "claude-haiku-4-5-20251001"}
    payload[payload_key] = (
        "hi" if payload_key == "reply" else [{"id": "t", "name": "x", "arguments": {}}]
    )
    msg, _ = _reply(agent, payload)
    assert msg.raw_data["served_model"] != "gpt-4.1-2025-04-14"


# --- the run-level label ------------------------------------------------------


def test_a_platform_managed_agent_is_not_labelled_with_the_unused_flag():
    """`--agent-llm` is never sent by this agent, so stamping it is a false claim."""
    from tau2.data_model.simulation import TextRunConfig
    from tau2.runner.helpers import get_info

    info = get_info(
        TextRunConfig(
            domain="appliance_care",
            task_set_name="appliance_care",
            agent="whissle",
            llm_agent="gpt-4.1-2025-04-14",
        )
    )
    assert info.agent_info.llm == "whissle-managed"
    assert info.agent_info.llm_args is None


def test_an_ordinary_llm_agent_still_records_its_real_model():
    """The change must not blur agents that genuinely use the flag."""
    from tau2.data_model.simulation import TextRunConfig
    from tau2.runner.helpers import get_info

    info = get_info(
        TextRunConfig(
            domain="appliance_care",
            task_set_name="appliance_care",
            agent="llm_agent",
            llm_agent="gpt-4.1-2025-04-14",
        )
    )
    assert info.agent_info.llm == "gpt-4.1-2025-04-14"


def test_every_whissle_agent_variant_is_covered():
    from tau2.runner.helpers import PLATFORM_MANAGED_AGENTS

    assert {"whissle", "whissle_voice", "whissle_flow", "whissle_flow_voice"} <= (
        PLATFORM_MANAGED_AGENTS
    )


# --- the opt-in safety rules --------------------------------------------------


def test_safety_rules_are_off_by_default():
    """AGENT_INSTRUCTION is shared by every domain; changing it silently would
    move results for benchmarks nobody asked us to touch."""
    from tau2.agent.whissle_agent import WhissleAgent

    with patch.dict(os.environ, ENV, clear=False):
        os.environ.pop("WHISSLE_SAFETY_RULES", None)
        agent = WhissleAgent(tools=[], domain_policy="policy")
    assert agent.safety_rules_enabled is False
    assert "ASK BEFORE THEY TOUCH" not in agent._system


def test_safety_rules_are_added_when_enabled():
    from tau2.agent.whissle_agent import WhissleAgent

    with patch.dict(os.environ, dict(ENV, WHISSLE_SAFETY_RULES="1")):
        agent = WhissleAgent(tools=[], domain_policy="policy")
    assert agent.safety_rules_enabled is True
    for marker in (
        "ASK BEFORE THEY TOUCH",
        "GIVE THE WARNING BEFORE THE STEP",
        "FOLLOW THE DOCUMENTED ORDER",
        "NEVER OPEN SOMETHING STILL HOT",
        "LOOK IDENTIFIERS UP",
        "FINISH THE RECORD",
        "TWICE IS ENOUGH",
    ):
        assert marker in agent._system, f"rule missing: {marker}"


def test_the_rules_encode_no_domain_or_answer():
    """They must be general support rules, not this benchmark's answers."""
    from tau2.agent.whissle_agent import SAFETY_RULES

    text = SAFETY_RULES.lower()
    for leak in (
        "washing",
        "bosch",
        "miele",
        "lg ",
        "pump",
        "filter",
        "drain",
        "appliance",
        "e:18",
        "e:23",
        "lint",
        "service cover",
        "impeller",
        "ac_0",
        "scald",
    ):
        assert leak not in text, f"the rules leak benchmark specifics: {leak!r}"


def test_rule_four_permits_reaching_the_sealed_component():
    """Forbidding every cover would forbid the documented route to the safe steps."""
    from tau2.agent.whissle_agent import SAFETY_RULES

    assert "Opening an outer access" in SAFETY_RULES
    assert "sealed component itself that must never be opened early" in SAFETY_RULES


def test_the_domain_policy_is_unchanged_by_the_rules():
    """The benchmark's own instructions must not move; only the agent's do."""
    from tau2.agent.whissle_agent import WhissleAgent

    with patch.dict(os.environ, dict(ENV, WHISSLE_SAFETY_RULES="1")):
        agent = WhissleAgent(tools=[], domain_policy="THE-DOMAIN-POLICY")
    assert "<policy>\nTHE-DOMAIN-POLICY\n</policy>" in agent._system
