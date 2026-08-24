"""The Whissle custom HTTP-tool specs published for the appliance_care agent."""

import pytest

from tau2.scripts.gen_whissle_tool_specs import EXPECTED_TOOLS, build_specs

BRIDGE = "https://bridge.example.invalid"


@pytest.fixture
def specs():
    return build_specs(BRIDGE, "conn-test")


def test_exactly_the_sixteen_approved_tools(specs):
    assert len(specs) == 16
    assert [s["name"] for s in specs] == EXPECTED_TOOLS


def test_no_user_tool_is_ever_published(specs):
    names = {s["name"] for s in specs}
    hidden = {
        "read_display_code",
        "smell_check",
        "run_test_cycle",
        "open_pump_cover",
        "clean_pump_housing",
    }
    assert hidden.isdisjoint(names)


def test_every_spec_posts_to_the_bridge(specs):
    for spec in specs:
        assert spec["kind"] == "http"
        assert spec["binding"]["method"] == "POST"
        assert spec["binding"]["url"] == f"{BRIDGE}/tools/{spec['name']}"


def test_no_spec_contains_a_secret(specs):
    """The token belongs in a stored connector, never in tool JSON."""
    import json

    blob = json.dumps(specs).lower()
    for marker in ("authorization", "bearer", "token", "wsk_", "api_key", "apikey"):
        assert marker not in blob, f"{marker!r} leaked into a tool spec"


def test_credential_is_referenced_by_id(specs):
    assert all(s["credential_id"] == "conn-test" for s in specs)


def test_schemas_are_the_domain_schemas(specs):
    by_name = {s["name"]: s for s in specs}
    assert by_name["lookup_error_code"]["parameters"]["required"] == [
        "model_id",
        "code",
    ]
    assert set(by_name["create_support_case"]["parameters"]["required"]) == {
        "appliance_id",
        "category",
        "summary",
    }


def test_every_spec_describes_itself(specs):
    for spec in specs:
        assert spec["description"].strip(), f"{spec['name']} has no description"
