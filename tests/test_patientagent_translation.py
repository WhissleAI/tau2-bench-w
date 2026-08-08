"""Message and tool-call translation, in both directions.

Needs langchain_core (a PatientAgentBench dependency — the adapter runs inside their
venv). Skipped rather than failed where it is absent, so the transport suite still
runs; ``langchain-core`` is in the dev extra so this suite runs in CI.
"""

from __future__ import annotations

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import (  # noqa: E402
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool  # noqa: E402

from tau2.health.patientagent.chat_model import (  # noqa: E402
    WhissleChatModel,
    lc_messages_to_anthropic,
    to_whissle_tool,
    turn_to_ai_message,
)
from tau2.health.patientagent.client import (  # noqa: E402
    WhissleBenchClient,
    WhissleConfig,
)
from tests.test_patientagent_adapter import FakeResponse, FakeSession  # noqa: E402

# -- message translation, both directions ----------------------------------------


def test_system_messages_hoisted_and_joined():
    messages, system = lc_messages_to_anthropic(
        [SystemMessage(content="A"), SystemMessage(content="B"), HumanMessage(content="hi")]
    )
    assert system == "A\n\nB"
    assert messages == [{"role": "user", "content": "hi"}]


def test_assistant_turn_carries_text_and_tool_use():
    ai = AIMessage(
        content="Checking.",
        tool_calls=[{"name": "list_doctors", "args": {"specialty": "gp"}, "id": "t1", "type": "tool_call"}],
    )
    messages, _ = lc_messages_to_anthropic([HumanMessage(content="hi"), ai])
    blocks = messages[1]["content"]
    assert blocks[0] == {"type": "text", "text": "Checking."}
    assert blocks[1] == {"type": "tool_use", "id": "t1", "name": "list_doctors", "input": {"specialty": "gp"}}


def test_parallel_tool_results_coalesce_into_one_user_turn():
    """Anthropic requires every tool_result for one assistant turn in a SINGLE user
    message; LangGraph emits them separately. Splitting them desyncs the history."""
    ai = AIMessage(
        content="",
        tool_calls=[
            {"name": "f", "args": {}, "id": "t1", "type": "tool_call"},
            {"name": "g", "args": {}, "id": "t2", "type": "tool_call"},
        ],
    )
    messages, _ = lc_messages_to_anthropic(
        [
            HumanMessage(content="hi"),
            ai,
            ToolMessage(content="r1", tool_call_id="t1"),
            ToolMessage(content="r2", tool_call_id="t2"),
        ]
    )
    assert len(messages) == 3, "the two tool results must share one user turn"
    blocks = messages[2]["content"]
    assert [b["tool_use_id"] for b in blocks] == ["t1", "t2"]
    assert all(b["type"] == "tool_result" for b in blocks)


def test_separate_tool_result_groups_do_not_merge():
    ai1 = AIMessage(content="", tool_calls=[{"name": "f", "args": {}, "id": "t1", "type": "tool_call"}])
    ai2 = AIMessage(content="", tool_calls=[{"name": "g", "args": {}, "id": "t2", "type": "tool_call"}])
    messages, _ = lc_messages_to_anthropic(
        [
            HumanMessage(content="hi"),
            ai1,
            ToolMessage(content="r1", tool_call_id="t1"),
            ai2,
            ToolMessage(content="r2", tool_call_id="t2"),
        ]
    )
    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant", "user", "assistant", "user"]


def test_tool_error_status_marked():
    messages, _ = lc_messages_to_anthropic(
        [ToolMessage(content="boom", tool_call_id="t1", status="error")]
    )
    assert messages[0]["content"][0]["is_error"] is True


def test_turn_to_ai_message_uses_langchain_args_key():
    from tau2.health.patientagent.client import TurnResponse

    message = turn_to_ai_message(
        TurnResponse(text="hi", tool_calls=[{"id": "t1", "name": "f", "arguments": {"a": 1}}])
    )
    assert message.tool_calls[0]["args"] == {"a": 1}, "LangChain names it args, not arguments"
    assert message.tool_calls[0]["id"] == "t1"


def test_round_trip_preserves_tool_call_through_both_translations():
    """A tool call must survive out-translation and back in as the same call."""
    from tau2.health.patientagent.client import TurnResponse

    ai = turn_to_ai_message(
        TurnResponse(text="", tool_calls=[{"id": "t9", "name": "schedule_appointment", "arguments": {"slot": "9am"}}])
    )
    messages, _ = lc_messages_to_anthropic([HumanMessage(content="book it"), ai])
    block = messages[1]["content"][0]
    assert block["name"] == "schedule_appointment"
    assert block["input"] == {"slot": "9am"}
    assert block["id"] == "t9"


# -- tool schema conversion ------------------------------------------------------


def test_langchain_tool_converts_to_endpoint_schema():
    @tool
    def schedule_appointment(doctor_id: str, slot: str) -> str:
        """Book a slot with a doctor."""
        return "ok"

    converted = to_whissle_tool(schedule_appointment)
    assert converted["name"] == "schedule_appointment"
    assert "Book a slot" in converted["description"]
    assert set(converted["input_schema"]["properties"]) == {"doctor_id", "slot"}


def test_already_converted_schema_passes_through():
    spec = {"name": "f", "description": "d", "input_schema": {"type": "object"}}
    assert to_whissle_tool(spec) is spec


# -- the chat model as create_agent sees it --------------------------------------


def test_bind_tools_returns_copy_with_schemas():
    @tool
    def get_profile() -> str:
        """Get the patient profile."""
        return "{}"

    model = WhissleChatModel(agent_id="a", api_key="k", base="https://example.test/bot")
    bound = model.bind_tools([get_profile])
    assert [t["name"] for t in bound.whissle_tools] == ["get_profile"]
    assert model.whissle_tools == [], "bind_tools must not mutate the original"


def test_generate_produces_ai_message_with_tool_calls(monkeypatch):
    model = WhissleChatModel(agent_id="a", api_key="k", base="https://example.test/bot")
    session = FakeSession(
        [FakeResponse(200, {"reply": "Checking.", "tool_calls": [{"id": "t1", "name": "list_doctors", "arguments": {}}], "stop_reason": "tool_use"})]
    )
    model._client = WhissleBenchClient(
        WhissleConfig(base="https://example.test/bot", agent_id="a", api_key="k"), session=session
    )
    result = model._generate([SystemMessage(content="sys"), HumanMessage(content="hi")])
    message = result.generations[0].message
    assert message.content == "Checking."
    assert message.tool_calls[0]["name"] == "list_doctors"
    # The system prompt must travel in the dedicated field, not as a user turn.
    assert session.requests[0]["body"]["system"] == "sys"
    assert session.requests[0]["body"]["messages"] == [{"role": "user", "content": "hi"}]
