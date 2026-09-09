"""Unit tests for the authenticated tau2 tool bridge.

These cover the contract a Whissle custom HTTP tool depends on: who may call,
what shapes are accepted, that a call executes exactly once, and that the order
tau2 records is the order the bridge executed.
"""

import pytest

from tau2.bridge.tool_bridge import BridgeError, ToolBridge, build_trajectory_records
from tau2.data_model.message import AssistantMessage, ToolMessage
from tau2.domains.appliance_care.environment import get_environment

TOKEN = "test-token-not-a-real-secret"
AUTH = f"Bearer {TOKEN}"


@pytest.fixture
def bridge():
    return ToolBridge(environment=get_environment(), token=TOKEN)


# ── authentication ──────────────────────────────────────────────────────────


def test_missing_authorization_is_401(bridge):
    with pytest.raises(BridgeError) as exc:
        bridge.handle("search_manuals", {"query": "drain"}, authorization=None)
    assert exc.value.status == 401


@pytest.mark.parametrize("header", ["", "token abc", "Bearer", "Basic " + TOKEN, TOKEN])
def test_malformed_authorization_is_rejected(bridge, header):
    with pytest.raises(BridgeError) as exc:
        bridge.handle("search_manuals", {"query": "drain"}, authorization=header)
    assert exc.value.status in (401, 403)


def test_wrong_token_is_403(bridge):
    with pytest.raises(BridgeError) as exc:
        bridge.handle(
            "search_manuals", {"query": "drain"}, authorization="Bearer wrong"
        )
    assert exc.value.status == 403


def test_rejected_calls_never_execute(bridge):
    """An unauthenticated request must not touch the database."""
    before = bridge.environment.get_db_hash()
    for header in (None, "Bearer wrong"):
        with pytest.raises(BridgeError):
            bridge.handle(
                "create_support_case",
                {"appliance_id": "APP-001", "category": "drainage", "summary": "x"},
                authorization=header,
            )
    assert bridge.environment.get_db_hash() == before
    assert bridge.records == []


def test_a_bridge_cannot_be_opened_without_a_token():
    with pytest.raises(ValueError):
        ToolBridge(environment=get_environment(), token="")


# ── the user-tool boundary ──────────────────────────────────────────────────


def test_only_agent_tools_are_exposed(bridge):
    """The hidden state must not be reachable. This is the answer key."""
    assert len(bridge.tool_names) == 16
    hidden = {
        "read_display_code",
        "smell_check",
        "open_pump_cover",
        "run_test_cycle",
        "read_model_label",
    }
    assert hidden.isdisjoint(bridge.tool_names)
    with pytest.raises(BridgeError) as exc:
        bridge.handle("smell_check", {}, authorization=AUTH)
    assert exc.value.status == 404


# ── schema validation ───────────────────────────────────────────────────────


def test_unknown_tool_is_404(bridge):
    with pytest.raises(BridgeError) as exc:
        bridge.handle("definitely_not_a_tool", {}, authorization=AUTH)
    assert exc.value.status == 404


def test_missing_required_argument_is_422(bridge):
    with pytest.raises(BridgeError) as exc:
        bridge.handle(
            "lookup_error_code", {"model_id": "WAT28400UC"}, authorization=AUTH
        )
    assert exc.value.status == 422
    assert "code" in exc.value.detail


def test_unknown_argument_is_422(bridge):
    with pytest.raises(BridgeError) as exc:
        bridge.handle(
            "search_manuals", {"query": "drain", "limit": "3"}, authorization=AUTH
        )
    assert exc.value.status == 422
    assert "limit" in exc.value.detail


def test_wrong_argument_type_is_422(bridge):
    with pytest.raises(BridgeError) as exc:
        bridge.handle("search_manuals", {"query": 17}, authorization=AUTH)
    assert exc.value.status == 422


def test_validation_failures_do_not_execute(bridge):
    before = bridge.environment.get_db_hash()
    with pytest.raises(BridgeError):
        bridge.handle(
            "create_support_case", {"appliance_id": "APP-001"}, authorization=AUTH
        )
    assert bridge.environment.get_db_hash() == before
    assert bridge.records == []


# ── execution ───────────────────────────────────────────────────────────────


def test_a_read_tool_returns_its_real_output(bridge):
    result = bridge.handle(
        "lookup_error_code",
        {"model_id": "WAT28402UC", "code": "E:23"},
        authorization=AUTH,
    )
    assert result["error"] is False
    assert result["seq"] == 1
    assert result["call_id"] == "bridge_0001"
    # The real manufacturer text, not a stub.
    assert "after-sales service" in result["result"]


def test_a_read_tool_does_not_mutate(bridge):
    before = bridge.environment.get_db_hash()
    bridge.handle("search_manuals", {"query": "drain filter"}, authorization=AUTH)
    assert bridge.environment.get_db_hash() == before


def test_a_write_tool_mutates_once(bridge):
    before = bridge.environment.get_db_hash()
    result = bridge.handle(
        "create_support_case",
        {"appliance_id": "APP-001", "category": "drainage", "summary": "won't drain"},
        authorization=AUTH,
    )
    assert result["error"] is False
    assert bridge.environment.get_db_hash() != before
    assert len(bridge.environment.tools.db.support_cases) == 1


def test_a_tool_error_is_reported_not_raised(bridge):
    """A bad id is the agent's mistake, not the bridge's: 200 with error=True."""
    result = bridge.handle(
        "get_appliance_details", {"appliance_id": "APP-999"}, authorization=AUTH
    )
    assert result["error"] is True
    assert "Error" in result["result"]
    # It is still recorded — the trajectory must show what the agent actually did.
    assert len(bridge.records) == 1


# ── sequencing and logging ──────────────────────────────────────────────────


def test_records_are_sequential_and_ordered(bridge):
    calls = [
        ("get_customer_by_phone", {"phone": "+14155550101"}),
        ("list_owned_appliances", {"customer_id": "CUST-001"}),
        ("get_appliance_details", {"appliance_id": "APP-001"}),
        ("check_warranty", {"appliance_id": "APP-001"}),
    ]
    for name, args in calls:
        bridge.handle(name, args, authorization=AUTH)

    records = bridge.records
    assert [r.seq for r in records] == [1, 2, 3, 4]
    assert [r.tool_name for r in records] == [c[0] for c in calls]
    assert [r.arguments for r in records] == [c[1] for c in calls]
    assert [r.call_id for r in records] == [
        "bridge_0001",
        "bridge_0002",
        "bridge_0003",
        "bridge_0004",
    ]


def test_drain_returns_only_new_records(bridge):
    bridge.handle("search_manuals", {"query": "drain"}, authorization=AUTH)
    first = bridge.drain()
    assert len(first) == 1

    assert bridge.drain() == []

    bridge.handle("get_model_details", {"model_id": "WAT28400UC"}, authorization=AUTH)
    second = bridge.drain()
    assert len(second) == 1
    assert second[0].seq == 2


def test_records_never_contain_the_token(bridge):
    bridge.handle("search_manuals", {"query": "drain"}, authorization=AUTH)
    assert TOKEN not in repr(bridge.records)


# ── duplicate-execution prevention ──────────────────────────────────────────


def test_idempotency_key_prevents_a_second_write(bridge):
    """A Whissle-side retry must not open a second support case."""
    args = {"appliance_id": "APP-001", "category": "drainage", "summary": "won't drain"}
    first = bridge.handle(
        "create_support_case", args, authorization=AUTH, idempotency_key="retry-1"
    )
    after_first = bridge.environment.get_db_hash()

    second = bridge.handle(
        "create_support_case", args, authorization=AUTH, idempotency_key="retry-1"
    )

    assert second["replayed"] is True
    assert first["replayed"] is False
    assert second["call_id"] == first["call_id"]
    assert second["result"] == first["result"]
    assert bridge.environment.get_db_hash() == after_first
    assert len(bridge.environment.tools.db.support_cases) == 1
    # And it appears exactly once in what tau2 will score.
    assert len(bridge.records) == 1


def test_distinct_idempotency_keys_do_execute_twice(bridge):
    """The guard must not swallow a genuine second call."""
    args = {"appliance_id": "APP-001", "category": "drainage", "summary": "won't drain"}
    bridge.handle("create_support_case", args, authorization=AUTH, idempotency_key="a")
    bridge.handle("create_support_case", args, authorization=AUTH, idempotency_key="b")
    assert len(bridge.environment.tools.db.support_cases) == 2
    assert len(bridge.records) == 2


# ── trajectory shape ────────────────────────────────────────────────────────


def test_records_become_valid_trajectory_pairs(bridge):
    bridge.handle("search_manuals", {"query": "drain"}, authorization=AUTH)
    bridge.handle(
        "create_support_case",
        {"appliance_id": "APP-001", "category": "drainage", "summary": "x"},
        authorization=AUTH,
    )
    messages = build_trajectory_records(bridge.records)

    assert len(messages) == 4
    for call_msg, tool_msg in zip(messages[::2], messages[1::2]):
        assert isinstance(call_msg, AssistantMessage)
        assert call_msg.content is None, "a tool-call message must not also carry text"
        assert len(call_msg.tool_calls) == 1
        assert isinstance(tool_msg, ToolMessage)
        assert tool_msg.id == call_msg.tool_calls[0].id
    assert messages[0].tool_calls[0].name == "search_manuals"
    assert messages[2].tool_calls[0].name == "create_support_case"
