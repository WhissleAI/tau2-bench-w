"""Pins the upstream MedAgentBench wire protocol.

Every assertion here encodes a quirk of `stanfordmlgroup/MedAgentBench` that our
score depends on. If one of these breaks, our number stopped being comparable to
the published baselines.
"""

import json

import pytest

from tau2.health.medagent.protocol import (
    HistoryItem,
    Trajectory,
    accepted_posts,
    build_prompt,
    has_post,
    parse_action,
)

FUNCS = [{"name": "GET {api_base}/Patient", "description": "d", "parameters": {}}]


def test_prompt_keeps_api_base_placeholder_inside_the_function_catalogue():
    """Upstream substitutes {api_base} in the prose but NOT in the function
    list — the agent sees the literal placeholder there. Substituting it would
    make the task easier than the published one."""
    p = build_prompt("http://x/fhir/", FUNCS, "ctx", "q")
    assert "you should use http://x/fhir/ as the api_base" in p
    assert "GET {api_base}/Patient" in p
    assert "Context: ctx" in p
    assert "Question: q" in p


def test_prompt_survives_braces_in_the_function_json():
    """The catalogue is JSON full of braces; it must not be re-interpreted as
    format fields."""
    funcs = [{"name": "x", "parameters": {"properties": {"a": {"type": "string"}}}}]
    p = build_prompt("http://x/fhir/", funcs, "", "")
    assert json.dumps(funcs) in p


def test_get_appends_format_json_with_an_ampersand():
    """Upstream appends `&_format=json`, not `?_format=json`."""
    a = parse_action("GET http://x/fhir/Patient?identifier=S1")
    assert a.kind == "get"
    assert a.url == "http://x/fhir/Patient?identifier=S1&_format=json"


def test_gemini_code_fences_are_stripped():
    a = parse_action("```tool_code\nGET http://x/fhir/Patient?a=b\n```")
    assert a.kind == "get"
    assert a.url == "http://x/fhir/Patient?a=b&_format=json"


def test_post_splits_url_from_json_payload():
    a = parse_action('POST http://x/fhir/Observation\n{"resourceType": "Observation"}')
    assert a.kind == "post"
    assert a.url == "http://x/fhir/Observation"
    assert a.payload == {"resourceType": "Observation"}


def test_post_with_unparseable_payload_is_flagged_not_crashed():
    a = parse_action("POST http://x/fhir/Observation\nnot json")
    assert a.kind == "post"
    assert a.payload is None
    assert a.payload_error


def test_post_with_non_object_payload_is_rejected():
    a = parse_action("POST http://x/fhir/Observation\n[1, 2]")
    assert a.kind == "post"
    assert a.payload is None


def test_finish_trims_to_the_inner_list():
    a = parse_action('FINISH(["S123"])')
    assert a.kind == "finish"
    assert a.result == '["S123"]'
    assert json.loads(a.result) == ["S123"]


def test_anything_else_is_an_invalid_action():
    for reply in ("Let me look that up.", "", "I think the MRN is S1."):
        assert parse_action(reply).kind == "invalid"


@pytest.fixture
def traj_with_accepted_post():
    t = Trajectory()
    t.add("user", "prompt")
    t.add("agent", 'POST http://x/fhir/Observation\n{"resourceType": "Observation"}')
    t.add("user", "POST request accepted and executed successfully. Please call FINISH")
    t.add("agent", "FINISH([])")
    t.result = "[]"
    return t


def test_accepted_posts_requires_the_acceptance_message(traj_with_accepted_post):
    assert accepted_posts(traj_with_accepted_post) == [
        ("http://x/fhir/Observation", {"resourceType": "Observation"})
    ]


def test_a_post_the_harness_rejected_is_not_an_accepted_post():
    t = Trajectory()
    t.add("agent", "POST http://x/fhir/Observation\nbroken")
    t.add("user", "Invalid POST request")
    assert accepted_posts(t) == []
    # ...but it still trips has_post, so a read-only task fails. That
    # asymmetry is upstream's and it is load-bearing.
    assert has_post(t) is True


def test_has_post_ignores_user_turns():
    t = Trajectory()
    t.add("user", "Here is the response... POST is not allowed")
    assert has_post(t) is False


def test_trajectory_exposes_the_shape_refsol_expects():
    t = Trajectory()
    t.add("agent", "x")
    assert isinstance(t.history[0], HistoryItem)
    assert t.history[0].role == "agent" and t.history[0].content == "x"
    assert hasattr(t, "result")
