"""Transport contract against a mocked Whissle endpoint.

Nothing here touches the network. The fake session records every request body so the
tests can assert on the exact wire shape the real endpoint was observed to accept.

Message/tool translation lives in ``test_patientagent_translation.py``, which needs
langchain_core; these tests deliberately do not, so the transport and error taxonomy
stay verifiable in any environment.
"""

from __future__ import annotations

import json

import pytest

from tau2.health.patientagent.client import (
    WhissleAuthError,
    WhissleBenchClient,
    WhissleConfig,
    WhissleInfraError,
    WhissleRequestError,
)


class FakeResponse:
    def __init__(self, status_code: int, payload=None, text: str = "", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Replays a scripted list of responses and records the requests."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def post(self, url, headers=None, data=None, timeout=None):
        self.requests.append(
            {"url": url, "headers": headers, "body": json.loads(data), "timeout": timeout}
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_client(responses, **kwargs) -> tuple[WhissleBenchClient, FakeSession]:
    session = FakeSession(responses)
    config = WhissleConfig(
        base="https://example.test/bot", agent_id="agent-123", api_key="wsk_test", **kwargs
    )
    return WhissleBenchClient(config, session=session), session


# -- transport contract ----------------------------------------------------------


def test_turn_sends_expected_wire_shape():
    client, session = make_client([FakeResponse(200, {"reply": "hello", "stop_reason": "end_turn"})])
    client.turn(
        [{"role": "user", "content": "hi"}],
        system="be safe",
        tools=[{"name": "get_profile", "description": "", "input_schema": {"type": "object"}}],
    )
    request = session.requests[0]
    assert request["url"] == "https://example.test/bot/api/bench/agent-turn"
    assert request["headers"]["Authorization"] == "Bearer wsk_test"
    assert request["body"]["agent_id"] == "agent-123"
    assert request["body"]["system"] == "be safe"
    assert request["body"]["tools"][0]["name"] == "get_profile"


def test_missing_credentials_fail_fast():
    with pytest.raises(WhissleRequestError, match="WHISSLE_AGENT_ID"):
        WhissleBenchClient(WhissleConfig(agent_id="", api_key="k"))


def test_auth_error_is_not_retried():
    client, session = make_client([FakeResponse(401, text="nope")])
    with pytest.raises(WhissleAuthError):
        client.turn([{"role": "user", "content": "hi"}])
    assert len(session.requests) == 1, "a 401 must fail fast, not burn retries"


def test_server_error_retries_then_raises_infra(monkeypatch):
    monkeypatch.setattr("tau2.health.patientagent.client.time.sleep", lambda _: None)
    client, session = make_client(
        [FakeResponse(503, text="unavailable")] * 3, max_attempts=3
    )
    # Classified INFRA so scoring excludes the session instead of scoring it badly.
    with pytest.raises(WhissleInfraError):
        client.turn([{"role": "user", "content": "hi"}])
    assert len(session.requests) == 3


def test_recovers_after_transient_error(monkeypatch):
    monkeypatch.setattr("tau2.health.patientagent.client.time.sleep", lambda _: None)
    client, _ = make_client(
        [FakeResponse(500, text="boom"), FakeResponse(200, {"reply": "recovered"})]
    )
    assert client.turn([{"role": "user", "content": "hi"}]).text == "recovered"


def test_non_json_200_is_transient(monkeypatch):
    monkeypatch.setattr("tau2.health.patientagent.client.time.sleep", lambda _: None)
    client, _ = make_client([FakeResponse(200, None, text="<html>"), FakeResponse(200, {"reply": "ok"})])
    assert client.turn([{"role": "user", "content": "hi"}]).text == "ok"


# -- response decoding -----------------------------------------------------------


def test_decodes_parallel_tool_calls():
    """The live endpoint really does emit several tool_use blocks in one turn."""
    client, _ = make_client(
        [
            FakeResponse(
                200,
                {
                    "reply": "Let me check.",
                    "stop_reason": "tool_use",
                    "tool_calls": [
                        {"id": "toolu_1", "name": "list_doctors", "arguments": {"specialty": "pulmonology"}},
                        {"id": "toolu_2", "name": "list_doctors", "arguments": {"specialty": "allergy"}},
                    ],
                },
            )
        ]
    )
    response = client.turn([{"role": "user", "content": "hi"}])
    assert response.text == "Let me check."
    assert [c["name"] for c in response.tool_calls] == ["list_doctors", "list_doctors"]
    assert response.tool_calls[0]["arguments"] == {"specialty": "pulmonology"}


def test_falls_back_to_content_blocks_when_tool_calls_absent():
    client, _ = make_client(
        [
            FakeResponse(
                200,
                {
                    "content": [
                        {"type": "text", "text": "One moment."},
                        {"type": "tool_use", "id": "t1", "name": "get_profile", "input": {"x": 1}},
                    ]
                },
            )
        ]
    )
    response = client.turn([{"role": "user", "content": "hi"}])
    assert response.text == "One moment."
    assert response.tool_calls == [{"id": "t1", "name": "get_profile", "arguments": {"x": 1}}]


def test_stringified_tool_arguments_are_parsed():
    """Some providers stringify tool inputs; an unparsed string would reach the
    sandbox as a non-dict and blow up mid-conversation."""
    client, _ = make_client(
        [FakeResponse(200, {"reply": "", "tool_calls": [{"id": "t", "name": "f", "arguments": '{"a": 2}'}]})]
    )
    response = client.turn([{"role": "user", "content": "x"}])
    assert response.tool_calls[0]["arguments"] == {"a": 2}
