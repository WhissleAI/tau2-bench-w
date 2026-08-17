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
    candidates = env.tools.identify_model(brand="Northwind", partial_model="2200")
    assert {c.model_id for c in candidates} == {"NW-2200", "NW-2200X"}


def test_identify_model_by_serial_is_exact(env):
    candidates = env.tools.identify_model(serial_number="NW22X-5512-3307")
    assert [c.model_id for c in candidates] == ["NW-2200X"]


def test_error_codes_are_model_specific(env):
    """The same code means different things on different models."""
    assert "Drain fault" in env.tools.lookup_error_code("NW-2200", "E24")
    assert "Door lock fault" in env.tools.lookup_error_code("NW-2400", "E24")


def test_unknown_error_code_lists_the_documented_ones(env):
    out = env.tools.lookup_error_code("NW-2200", "E99")
    assert "not a documented code" in out
    assert "E24" in out


def test_open_manual_section_returns_verbatim_text(env):
    text = env.tools.open_manual_section("northwindnw2200washer", "5.1")
    assert "drain filter" in text.lower()
    assert "WARNING" in text, "warning boxes must survive into the section text"


def test_open_manual_section_rejects_a_bad_section(env):
    with pytest.raises(ValueError, match="Unknown section"):
        env.tools.open_manual_section("northwindnw2200washer", "99.9")


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
        manual_id_used="northwindnw2200washer",
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
