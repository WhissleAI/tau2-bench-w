"""Adapter contract: the Whissle brain client and the episode loop.

Nothing here touches the network — the Whissle endpoint and the FHIR server are
both mocked, so `make test` runs this suite offline.
"""

import json

import pytest
import requests

from tau2.health.medagent import episode as episode_mod
from tau2.health.medagent.brain import BrainInfraError, WhissleBrain, _extract_text
from tau2.health.medagent.data import Case
from tau2.health.medagent.episode import (
    STATUS_COMPLETED,
    STATUS_INFRA,
    STATUS_INVALID,
    STATUS_LIMIT,
    run_episode,
)

API_BASE = "http://ehr.test/fhir/"
FUNCS = [{"name": "GET {api_base}/Patient"}]


def make_case(task_id="task1_1", instruction="What's the MRN?", context=""):
    return Case(
        id=task_id, instruction=instruction, context=context, sol=["S1"], eval_mrn="S1"
    )


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})
        self.headers = {"Content-Type": "application/json"}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


# ----------------------------------------------------------------- brain


def brain(**kw):
    return WhissleBrain(
        base="http://whissle.test", agent_id="agent-1", api_key="wsk_test", **kw
    )


def test_brain_sends_bearer_auth_and_no_tools(monkeypatch):
    """MedAgentBench is a text protocol — sending tool schemas would change
    the task. And /api/bench/agent-turn executes nothing anyway."""
    seen = {}

    def fake_post(url, headers=None, data=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers
        seen["body"] = json.loads(data)
        return FakeResponse(payload={"reply": "GET http://x?a=b"})

    monkeypatch.setattr(requests, "post", fake_post)
    out = brain().turn([{"role": "user", "content": "hi"}], "sys")

    assert out == "GET http://x?a=b"
    assert seen["url"] == "http://whissle.test/api/bench/agent-turn"
    assert seen["headers"]["Authorization"] == "Bearer wsk_test"
    assert seen["body"]["agent_id"] == "agent-1"
    assert seen["body"]["tools"] == []
    assert seen["body"]["system"] == "sys"


def test_brain_retries_5xx_then_succeeds(monkeypatch):
    calls = []

    def fake_post(url, headers=None, data=None, timeout=None):
        calls.append(1)
        if len(calls) < 3:
            return FakeResponse(status_code=503, text="upstream busy")
        return FakeResponse(payload={"reply": "FINISH([])"})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(episode_mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr("tau2.health.medagent.brain.time.sleep", lambda *_: None)

    assert brain().turn([], None) == "FINISH([])"
    assert len(calls) == 3


def test_brain_gives_up_after_retries_as_infra_error(monkeypatch):
    monkeypatch.setattr(
        requests, "post", lambda *a, **k: FakeResponse(status_code=500, text="boom")
    )
    monkeypatch.setattr("tau2.health.medagent.brain.time.sleep", lambda *_: None)
    with pytest.raises(BrainInfraError):
        brain().turn([], None)


def test_brain_fails_fast_on_4xx(monkeypatch):
    """A bad key or an agent outside the org cannot be fixed by retrying."""
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return FakeResponse(status_code=403, text="forbidden")

    monkeypatch.setattr(requests, "post", fake_post)
    with pytest.raises(BrainInfraError, match="rejected"):
        brain().turn([], None)
    assert len(calls) == 1


def test_brain_transport_failure_is_infra(monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.ConnectTimeout("no route")

    monkeypatch.setattr(requests, "post", boom)
    monkeypatch.setattr("tau2.health.medagent.brain.time.sleep", lambda *_: None)
    with pytest.raises(BrainInfraError):
        brain().turn([], None)


def test_extract_text_falls_back_to_content_blocks():
    assert (
        _extract_text({"reply": "", "content": [{"type": "text", "text": "GET x"}]})
        == "GET x"
    )


def test_extract_text_treats_an_empty_reply_as_infra():
    """An empty brain response is not a wrong clinical answer."""
    with pytest.raises(BrainInfraError):
        _extract_text({"reply": "", "content": [], "stop_reason": "max_tokens"})


def test_system_modes():
    assert brain(system_mode="neutral").system_for("P").startswith("You are completing")
    assert brain(system_mode="prompt-as-system").system_for("P") == "P"
    assert brain(system_mode="agent-default").system_for("P") is None


# --------------------------------------------------------------- episode


class ScriptedBrain:
    """Replays a fixed list of replies, one per turn."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen_messages = []

    def system_for(self, prompt):
        return "sys"

    def turn(self, messages, system):
        self.seen_messages.append(list(messages))
        if not self.replies:
            raise AssertionError("brain called more times than scripted")
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def fake_get(monkeypatch):
    """Stub the environment's GET with a canned FHIR bundle."""
    calls = []

    def _get(url, params=None, headers=None, timeout=60.0):
        calls.append(url)
        return {"status_code": 200, "data": '{"total": 1, "entry": []}'}

    monkeypatch.setattr(episode_mod, "send_get_request", _get)
    return calls


def test_episode_completes_on_finish(fake_get):
    b = ScriptedBrain(
        ["GET http://ehr.test/fhir/Patient?identifier=S1", 'FINISH(["S1"])']
    )
    ep = run_episode(make_case(), brain=b, funcs=FUNCS, api_base=API_BASE)

    assert ep.status == STATUS_COMPLETED
    assert ep.trajectory.result == '["S1"]'
    assert ep.infra_fail is False
    assert len(ep.turns) == 2
    # The GET actually hit the environment, with _format appended.
    assert fake_get == ["http://ehr.test/fhir/Patient?identifier=S1&_format=json"]


def test_observations_are_injected_back_as_user_turns(fake_get):
    b = ScriptedBrain(["GET http://ehr.test/fhir/Patient?a=b", "FINISH([])"])
    ep = run_episode(make_case(), brain=b, funcs=FUNCS, api_base=API_BASE)

    roles = [i.role for i in ep.trajectory.history]
    assert roles == ["user", "agent", "user", "agent"]
    assert "Here is the response from the GET request" in ep.trajectory.history[2].content
    # The brain saw the observation as a user message.
    assert b.seen_messages[-1][-1]["role"] == "user"


def test_invalid_action_terminates_the_episode(fake_get):
    b = ScriptedBrain(["Sure, let me check that for you."])
    ep = run_episode(make_case(), brain=b, funcs=FUNCS, api_base=API_BASE)
    assert ep.status == STATUS_INVALID
    assert ep.trajectory.result is None


def test_round_budget_is_enforced(fake_get):
    b = ScriptedBrain(["GET http://ehr.test/fhir/Patient?a=b"] * 3)
    ep = run_episode(make_case(), brain=b, funcs=FUNCS, api_base=API_BASE, max_round=3)
    assert ep.status == STATUS_LIMIT
    assert len(ep.turns) == 3


def test_brain_outage_marks_the_episode_infra_fail(fake_get):
    b = ScriptedBrain([BrainInfraError("endpoint down")])
    ep = run_episode(make_case(), brain=b, funcs=FUNCS, api_base=API_BASE)
    assert ep.infra_fail is True
    assert ep.status == STATUS_INFRA
    assert "endpoint down" in ep.infra_reason


def test_unexpected_harness_error_is_infra_not_a_wrong_answer(fake_get):
    b = ScriptedBrain([RuntimeError("kaboom")])
    ep = run_episode(make_case(), brain=b, funcs=FUNCS, api_base=API_BASE)
    assert ep.infra_fail is True
    assert "kaboom" in ep.infra_reason


def test_post_observation_is_byte_identical_to_upstream(fake_get):
    """The agent must be told the same thing upstream tells it, or its
    behaviour — and therefore the score — diverges."""
    b = ScriptedBrain(
        ['POST http://ehr.test/fhir/Observation\n{"resourceType":"Observation"}',
         "FINISH([])"]
    )
    ep = run_episode(make_case("task3_1"), brain=b, funcs=FUNCS, api_base=API_BASE)
    obs = ep.trajectory.history[2].content
    assert obs.startswith("POST request accepted and executed successfully.")


def test_malformed_post_gets_the_invalid_message(fake_get):
    b = ScriptedBrain(["POST http://ehr.test/fhir/Observation\nnope", "FINISH([])"])
    ep = run_episode(make_case("task3_1"), brain=b, funcs=FUNCS, api_base=API_BASE)
    assert ep.trajectory.history[2].content == "Invalid POST request"
