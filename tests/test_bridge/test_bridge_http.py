"""The bridge over its actual HTTP transport, as a Whissle custom tool sees it."""

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from tau2.bridge.tool_bridge import ToolBridge  # noqa: E402
from tau2.domains.appliance_care.environment import get_environment  # noqa: E402

TOKEN = "test-token-not-a-real-secret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client_and_bridge():
    bridge = ToolBridge(environment=get_environment(), token=TOKEN)
    return fastapi_testclient.TestClient(bridge.build_app()), bridge


def test_healthz_needs_no_token(client_and_bridge):
    client, _ = client_and_bridge
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["domain"] == "appliance_care"


def test_call_without_a_token_is_401(client_and_bridge):
    client, bridge = client_and_bridge
    resp = client.post("/tools/search_manuals", json={"query": "drain"})
    assert resp.status_code == 401
    assert bridge.records == []


def test_call_with_a_bad_token_is_403(client_and_bridge):
    client, _ = client_and_bridge
    resp = client.post(
        "/tools/search_manuals",
        json={"query": "drain"},
        headers={"Authorization": "Bearer nope"},
    )
    assert resp.status_code == 403


def test_authenticated_read_returns_the_tool_output(client_and_bridge):
    client, _ = client_and_bridge
    resp = client.post(
        "/tools/lookup_error_code",
        json={"model_id": "WAT28402UC", "code": "E:23"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is False
    assert "after-sales service" in body["result"]


def test_user_tools_have_no_route(client_and_bridge):
    """The hidden-state tools must not be reachable over HTTP at all."""
    client, _ = client_and_bridge
    resp = client.post("/tools/smell_check", json={}, headers=AUTH)
    assert resp.status_code == 404
    resp = client.post("/user_tools/smell_check", json={}, headers=AUTH)
    assert resp.status_code == 404


def test_bad_arguments_are_422(client_and_bridge):
    client, _ = client_and_bridge
    resp = client.post("/tools/lookup_error_code", json={"model_id": "X"}, headers=AUTH)
    assert resp.status_code == 422


def test_idempotency_header_is_honoured_over_http(client_and_bridge):
    client, bridge = client_and_bridge
    payload = {
        "appliance_id": "APP-001",
        "category": "drainage",
        "summary": "won't drain",
    }
    headers = dict(AUTH, **{"Idempotency-Key": "abc"})
    first = client.post("/tools/create_support_case", json=payload, headers=headers)
    second = client.post("/tools/create_support_case", json=payload, headers=headers)

    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True
    assert len(bridge.environment.tools.db.support_cases) == 1
    assert len(bridge.records) == 1


def test_the_bridge_publishes_no_schema_surface(client_and_bridge):
    """No docs/openapi: the tool list is not a discovery endpoint for callers."""
    client, _ = client_and_bridge
    for path in ("/docs", "/redoc", "/openapi.json", "/api/openapi.json"):
        assert client.get(path).status_code == 404
