"""Agent-tool tests for the appliance_care domain."""

import pytest

from tau2.data_model.message import ToolCall
from tau2.domains.appliance_care.environment import get_environment


@pytest.fixture
def env():
    return get_environment()


def _call(env, tool_name, **arguments):
    return env.get_response(
        ToolCall(id="t", name=tool_name, arguments=arguments, requestor="assistant")
    )


def test_customer_lookup_by_phone_and_name(env):
    assert not _call(env, "get_customer_by_phone", phone="+14155550101").error
    assert not _call(env, "get_customer_by_name", name="Dana Whitfield").error
    assert _call(env, "get_customer_by_phone", phone="+10000000000").error


def test_identify_model_is_ambiguous_on_a_partial_number(env):
    """A partial number must return several candidates, forcing a follow-up."""
    candidates = env.tools.identify_model(brand="Bosch", partial_model="2840")
    # All three Bosch models share the WAT2840 stem and differ only in the last
    # digit. This is the real ambiguity the disambiguation tasks exercise.
    assert {c.model_id for c in candidates} == {
        "WAT28400UC",
        "WAT28401UC",
        "WAT28402UC",
    }


def test_identify_model_by_serial_is_exact(env):
    candidates = env.tools.identify_model(serial_number="FD9512-005512-3307")
    assert [c.model_id for c in candidates] == ["WAT28401UC"]


def test_error_codes_are_model_specific(env):
    """Which codes a model documents is model-specific.

    Bosch publishes E:23 in the WAT28402UC manual only. The other two models in the
    family — whose numbers differ by a single digit — do not document it.

    That makes the code a useful cross-check, NOT an identification shortcut: it is
    still something the customer read out and could have misread. The appliance_id
    must come from list_owned_appliances either way.
    """
    assert "leaking" in env.tools.lookup_error_code("WAT28402UC", "E:23").lower()
    assert "not a documented code" in env.tools.lookup_error_code("WAT28400UC", "E:23")
    assert "not a documented code" in env.tools.lookup_error_code("WAT28401UC", "E:23")
    # Miele signals with indicator lights; it has no code table to look up in.
    assert "not a documented code" in env.tools.lookup_error_code("WWB020", "E:23")


def test_unknown_error_code_lists_the_documented_ones(env):
    out = env.tools.lookup_error_code("WAT28400UC", "E99")
    assert "not a documented code" in out
    assert "E:18" in out


def test_open_manual_section_returns_verbatim_text(env):
    text = env.tools.open_manual_section("boschwat28400ucwasher", "2")
    assert "drain hose" in text.lower()
    text23 = env.tools.open_manual_section(
        "boschwat28402uc_washer".replace("_", ""), "1"
    )
    assert "WARNING" in text23, "warning boxes must survive into the section text"


def test_open_manual_section_rejects_a_bad_section(env):
    with pytest.raises(ValueError, match="Unknown section"):
        env.tools.open_manual_section("boschwat28400ucwasher", "99.9")


def test_warranty_and_history_lookup(env):
    assert env.tools.check_warranty("APP-001").status.value == "active"
    assert env.tools.check_warranty("APP-006").status.value == "expired"
    assert len(env.tools.get_service_history("APP-003")) == 1
    assert env.tools.get_service_history("APP-001") == []


def test_case_appointment_and_resolution_round_trip(env):
    case = env.tools.create_support_case(
        appliance_id="APP-001", category="drainage", summary="will not drain"
    )
    assert case.severity.value == "normal" and case.status.value == "open"
    appt = env.tools.schedule_service(
        case_id=case.case_id, date="2026-03-05", window="morning", visit_type="warranty"
    )
    assert appt.visit_type.value == "warranty"
    res = env.tools.record_resolution(
        appliance_id="APP-001",
        outcome="service_scheduled",
        steps_taken=["checked filter"],
        manual_id_used="boschwat28400ucwasher",
    )
    assert res.outcome.value == "service_scheduled"


def test_escalate_safety_issue_marks_severity(env):
    case = env.tools.escalate_safety_issue(
        appliance_id="APP-001", reason="burning smell"
    )
    assert case.severity.value == "safety"
    assert env.tools.assert_safety_case_open("APP-001")


def test_invalid_arguments_are_rejected(env):
    with pytest.raises(ValueError, match="No support case"):
        env.tools.schedule_service(
            case_id="CASE-999", date="2026-03-05", window="am", visit_type="warranty"
        )
    env.tools.create_support_case(appliance_id="APP-001", category="x", summary="y")
    with pytest.raises(ValueError, match="visit_type"):
        env.tools.schedule_service(
            case_id="CASE-001", date="2026-03-05", window="am", visit_type="free"
        )
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        env.tools.schedule_service(
            case_id="CASE-001", date="5 March", window="am", visit_type="warranty"
        )
    with pytest.raises(ValueError, match="cannot be before"):
        env.tools.schedule_service(
            case_id="CASE-001",
            date="2026-03-01",
            window="morning",
            visit_type="warranty",
        )
    with pytest.raises(ValueError, match="window must be"):
        env.tools.schedule_service(
            case_id="CASE-001",
            date="2026-03-05",
            window="midnight",
            visit_type="warranty",
        )
    with pytest.raises(ValueError, match="outcome must be one of"):
        env.tools.record_resolution(
            appliance_id="APP-001", outcome="fixed_it", steps_taken=[]
        )


def test_no_manuals_variant_withholds_the_manual_tools():
    env = get_environment(manual_access="no_manuals")
    names = {t.name for t in env.get_tools()}
    assert not {"search_manuals", "open_manual_section", "lookup_error_code"} & names
    assert "create_support_case" in names


def test_solo_mode_is_refused():
    with pytest.raises(ValueError, match="Solo mode not supported"):
        get_environment(solo_mode=True)


# --- identifier contract ------------------------------------------------------
#
# The first live text run scored 0/10, and twelve of its tool calls were rejected
# for the same reason: the agent passed whatever identifier the customer had read
# aloud — a model number, a serial, once the literal string "unknown" — where an
# appliance record id belongs. The old error said only "No appliance found with
# id NW-2200", which names the symptom and offers no way back. These tests pin an
# error a competent agent can actually recover from.


def test_a_model_number_is_named_as_such():
    env = get_environment()
    with pytest.raises(ValueError) as exc:
        env.tools.create_support_case(
            appliance_id="WAT28400UC", category="drainage", summary="x"
        )
    msg = str(exc.value)
    assert "MODEL number" in msg
    assert "list_owned_appliances" in msg, "the error must name the way out"


def test_a_serial_number_is_named_as_such():
    env = get_environment()
    with pytest.raises(ValueError) as exc:
        env.tools.record_resolution(
            appliance_id="FD9401-004471-8890",
            outcome="resolved_self_service",
            steps_taken=["x"],
        )
    assert "SERIAL number" in str(exc.value)


def test_an_unrecognised_id_still_points_at_the_recovery_path():
    env = get_environment()
    with pytest.raises(ValueError) as exc:
        env.tools.check_warranty(appliance_id="unknown")
    msg = str(exc.value)
    assert "APP-001" in msg
    assert "get_customer_by_phone" in msg


def test_every_appliance_id_parameter_says_where_the_id_comes_from():
    """A schema that says only 'The machine.' invites exactly the wrong value."""
    env = get_environment()
    for tool in env.get_tools():
        schema = tool.openai_schema
        fn = schema.get("function", schema)
        prop = (fn.get("parameters") or {}).get("properties", {}).get("appliance_id")
        if not prop:
            continue
        desc = prop.get("description") or ""
        assert "APP-001" in desc, f"{fn['name']}: appliance_id gives no example id"
        assert "list_owned_appliances" in desc, (
            f"{fn['name']}: appliance_id does not say where the id comes from"
        )


def test_the_domain_declares_a_benchmark_version():
    """Scores are comparable only within a version, so one must exist.

    Tool descriptions are versioned material: they are part of the prompt every
    agent sees, so clarifying one changes the question being asked. A run from
    before such a change cannot be set beside a run from after it.
    """
    from tau2.domains.appliance_care.utils import APPLIANCE_CARE_VERSION

    assert APPLIANCE_CARE_VERSION.startswith("v")
    assert APPLIANCE_CARE_VERSION[1:].isdigit()
