# Copyright Sierra
"""The diagnostic-artifact contract, offline.

Four things this file is here to keep true, because each of them has a failure
mode that would silently poison a 100-case run:

1. **A trace that exists is captured and lands on the right case.** A run that
   scores 100 cases and attaches the trace of case 7 to case 8 is worse than one
   that attaches none.
2. **Absence is never a measurement.** In text mode the voice signals must read
   ``available: false`` with a reason, and every payload field must be ``None``.
   A ``[]`` or a ``0`` here would be read as "the hesitation predictor never
   fired" — a claim about the agent — when the truth is "no audio was involved".
3. **Per-case provenance is present**, so one case file lifted into a bug report
   still says which agent, which judge, whether the judge was independent, which
   seed and which stratum.
4. **The said-vs-emitted-vs-landed distinction survives.** MedAgentBench's
   headline finding is an agent that says it filed an order and never issues the
   POST; that must not flatten into "tools called: 0".

Everything here runs against fixtures and monkeypatched HTTP — no network, no key.
"""
from __future__ import annotations

import json

import pytest

from tau2.health import diagnostics as diag


# ── fixtures ────────────────────────────────────────────────────────────────────

FLOW_STEPS = [
    {"seq": 1, "turn": 0, "kind": "state_enter", "state": "greet"},
    {"seq": 2, "turn": 1, "kind": "say_emitted", "state": "greet", "text": "Hello."},
    {"seq": 3, "turn": 1, "kind": "transition_check", "from": "greet", "to": "collect",
     "result": False, "reason": "caller has not stated a symptom yet",
     "condition": "symptom_present"},
    {"seq": 4, "turn": 2, "kind": "transition_check", "from": "greet", "to": "collect",
     "result": True, "reason": "caller reported headache onset",
     "condition": "symptom_present"},
    {"seq": 5, "turn": 2, "kind": "state_enter", "state": "collect"},
    {"seq": 6, "turn": 2, "kind": "var_set", "name": "symptom", "value": "headache",
     "source": "extraction"},
    {"seq": 7, "turn": 3, "kind": "var_set", "name": "severity", "value": 7,
     "source": "tool_result"},
    {"seq": 8, "turn": 3, "kind": "tools_gated", "allowed": ["book_appointment"]},
    {"seq": 9, "turn": 4, "kind": "guard_trip", "guard": "no_diagnosis",
     "detail": "agent attempted to name a condition"},
    {"seq": 10, "turn": 4, "kind": "state_divergence", "expected": "collect",
     "actual": "wrapup"},
    {"seq": 11, "turn": 5, "kind": "flow_end", "state": "wrapup", "reason": "complete"},
]

VOICE_TURNS = [
    {"turn": 1, "latency_ms": 780, "boundary": "text",
     "signals": [{"signal": "hesitation", "p": 0.71},
                 {"signal": "shadow", "accepted": False}],
     "user_metadata": [{"emotion": "anxious", "intent": "report_symptom",
                        "probs": {"anxious": 0.8}}]},
    {"turn": 2, "latency_ms": 1120, "boundary": "text", "barge_in": True,
     "turn_completeness": 0.42,
     "signals": [{"signal": "speculative", "tool": "check_availability"}],
     "user_metadata": [{"emotion": "neutral", "intent": "confirm"}]},
]


# ── 1. the trace is captured, parsed, and lands on the right case ───────────────

def test_flow_section_parses_every_step_kind():
    section = diag.flow_section(FLOW_STEPS, source="test",
                                flow_spec={"start_state": "greet",
                                           "states": [{"id": "greet"}, {"id": "collect"},
                                                      {"id": "wrapup"}]})
    assert section["available"] is True
    assert section["reason"] is None
    counts = section["step_counts_by_kind"]
    for kind in diag.FLOW_STEP_KINDS:
        assert kind in counts, f"{kind} must be counted even when it never fired"
    assert counts["transition_check"] == 2
    assert counts["var_set"] == 2
    assert section["states_visited"] == ["greet", "collect"]
    assert section["states_unvisited"] == ["wrapup"]
    assert section["ended"] is True


def test_transition_reason_rationale_is_preserved():
    """#650 added the WHY behind each transition check. A trace that drops it turns
    every non-firing transition back into an unexplained dead end."""
    section = diag.flow_section(FLOW_STEPS, source="test")
    reasons = [t["reason"] for t in section["transitions"]]
    assert reasons == ["caller has not stated a symptom yet",
                       "caller reported headache onset"]
    assert len(section["transitions_fired"]) == 1
    assert section["transitions_fired"][0]["to"] == "collect"


def test_var_set_source_is_preserved():
    """tool_result / extraction / goal_complete are different evidence — a value the
    ENGINE derived versus one the caller stated."""
    section = diag.flow_section(FLOW_STEPS, source="test")
    assert section["var_sources"] == {"extraction": 1, "tool_result": 1}
    assert {v["name"] for v in section["var_sets"]} == {"symptom", "severity"}


def test_guard_trips_and_divergences_are_surfaced():
    section = diag.flow_section(FLOW_STEPS, source="test")
    assert len(section["guard_trips"]) == 1
    assert section["guard_trips"][0]["guard"] == "no_diagnosis"
    assert len(section["state_divergences"]) == 1
    assert section["state_divergences"][0]["actual"] == "wrapup"


def test_trace_is_attached_to_the_right_case(tmp_path, monkeypatch):
    """Two cases, two different traces, fetched per case: each file must carry ITS
    OWN trace. A cross-wired trace is the failure that makes a scaled run useless."""
    traces = {
        "conv-a": {"steps": [{"seq": 1, "turn": 1, "kind": "state_enter",
                              "state": "A"}], "current_state": "A"},
        "conv-b": {"steps": [{"seq": 1, "turn": 1, "kind": "state_enter",
                              "state": "B"}], "current_state": "B"},
    }
    client = diag.TraceClient(base="http://test", api_key="wsk_test")
    monkeypatch.setattr(
        client, "_get",
        lambda path: traces[path.split("conversation_id=")[1]])

    for case_id, conv in (("case-a", "conv-a"), ("case-b", "conv-b")):
        section = diag.flow_from_trace_response(
            client.flow_trace("agent-1", conv), source="test", conversation_id=conv)
        record = diag.attach({"case_id": case_id}, diag.build(
            benchmark="t", case_id=case_id, mode="voice", flow=section,
            signals=diag.signals_unavailable(), metadata_sidecar=diag.metadata_unavailable(),
            tools=diag.tools_section([], source="t"),
            provenance=diag.provenance("t", mode="voice", transport_endpoint="t"),
            cost=diag.cost_section()))
        diag.write_case(str(tmp_path), case_id, record)

    a = json.loads((tmp_path / "case-a.json").read_text())
    b = json.loads((tmp_path / "case-b.json").read_text())
    assert a["diagnostics"]["flow"]["states_visited"] == ["A"]
    assert b["diagnostics"]["flow"]["states_visited"] == ["B"]
    assert a["diagnostics"]["flow"]["conversation_id"] == "conv-a"


def test_failed_trace_fetch_degrades_with_the_error():
    client = diag.TraceClient(base="http://test", api_key="wsk_test")
    section = diag.flow_from_trace_response(
        None, source="test", fetch_error="HTTP 404: not found")
    assert section["available"] is False
    assert "404" in section["reason"]
    assert section["steps"] is None


# ── 2. absence is recorded as absence, never as zero ────────────────────────────

@pytest.mark.parametrize("section", [
    diag.flow_unavailable(diag.REASON_BENCH_ENDPOINT),
    diag.signals_unavailable(diag.REASON_TEXT_MODE),
    diag.metadata_unavailable(diag.REASON_TEXT_MODE),
])
def test_unavailable_sections_null_every_payload_field(section):
    """The core honesty rule: nothing in an unavailable section may be an empty
    container or a zero, because both read as a measurement of nothing."""
    assert section["available"] is False
    assert section["reason"]
    for key, value in section.items():
        if key in ("available", "reason"):
            continue
        assert value is None, f"{key} must be None in an unavailable section, got {value!r}"


def test_text_mode_signals_are_unavailable_not_zeroed():
    envelope = diag.build(
        benchmark="t", case_id="c1", mode="text",
        flow=diag.flow_unavailable(diag.REASON_BENCH_ENDPOINT),
        signals=diag.signals_unavailable(diag.REASON_TEXT_MODE),
        metadata_sidecar=diag.metadata_unavailable(diag.REASON_TEXT_MODE),
        tools=diag.tools_section([], source="t"),
        provenance=diag.provenance("t", mode="text", transport_endpoint="t"),
        cost=diag.cost_section())
    av = envelope["availability"]
    assert av["signals_available"] is False
    assert "voice" in av["signals_reason"].lower()
    assert av["flow_available"] is False
    assert av["metadata_sidecar_available"] is False
    assert envelope["signals"]["summary"] is None
    assert envelope["signals"]["turns"] is None


def test_voice_signals_are_captured_and_rolled_up():
    section = diag.signals_section(VOICE_TURNS, source="data channel")
    assert section["available"] is True
    s = section["summary"]
    assert s["frames_total"] == 3
    assert s["by_kind"] == {"hesitation": 1, "shadow": 1, "speculative": 1}
    assert s["hesitation_turns"] == [1]
    assert s["shadow_turns"] == [1]
    assert s["speculative_tools"] == {"check_availability": 1}
    assert s["barge_in_turns"] == [2]
    assert s["response_latency_ms"]["p50"] == 780
    assert s["turn_completeness"]["n"] == 1
    assert s["emitted_nothing"] is False


def test_voice_run_that_emitted_no_signals_is_available_but_says_so():
    """Over voice an empty capture IS a measurement — of the signal pipeline being
    dark. That is a different fact from a text run having no signals at all, and the
    two must not collapse into the same record."""
    section = diag.signals_section([{"turn": 1, "signals": []}], source="data channel")
    assert section["available"] is True
    assert section["summary"]["emitted_nothing"] is True
    assert section["summary"]["frames_total"] == 0


def test_metadata_sidecar_section():
    section = diag.metadata_section(VOICE_TURNS, source="data channel")
    assert section["available"] is True
    assert section["summary"]["frames_total"] == 2
    assert section["summary"]["emotions_seen"] == {"anxious": 1, "neutral": 1}
    assert section["summary"]["intents_seen"] == {"report_symptom": 1, "confirm": 1}


def test_call_trace_sections_map_through_and_degrade_independently():
    flow, signals = diag.sections_from_call_trace({
        "call_id": "c",
        "flow": {"available": True, "current_state": "collect",
                 "turns": [{"turn": 1, "steps": [
                     {"seq": 1, "turn": 1, "kind": "state_enter", "state": "collect"}]}]},
        "signals": {"available": False, "turns": []},
    })
    assert flow["available"] is True
    assert flow["states_visited"] == ["collect"]
    assert signals["available"] is False
    assert signals["turns"] is None


# ── 3. per-case provenance ──────────────────────────────────────────────────────

def test_provenance_is_self_describing():
    p = diag.provenance(
        "patientagentbench", mode="text",
        transport_endpoint="POST /api/bench/agent-turn",
        agent_id="agent-123", base_url="https://x/bot", seed=42,
        stratum={"task_type": "emergency", "severity_level": "high"},
        judge={"judge_provider": "whissle", "judge_independent": False})
    assert p["agent_id"] == "agent-123"
    assert p["base_url"] == "https://x/bot"
    assert p["mode"] == "text"
    assert p["seed"] == 42
    assert p["stratum"]["severity_level"] == "high"
    assert p["judge_provider"] == "whissle"
    assert p["judge_independent"] is False
    assert p["captured_at"]


def test_deterministic_benchmark_records_no_judge_rather_than_none():
    p = diag.provenance("medagentbench", mode="text", transport_endpoint="e", judge=None)
    assert p["judge"]["available"] is False
    assert "deterministic" in p["judge"]["reason"]
    c = diag.cost_section(reason=diag.REASON_NO_JUDGE)
    assert c["available"] is False
    assert c["judge_cost_usd"] is None, "a $0.00 would read as a measured zero spend"


# ── 4. tool forensics ───────────────────────────────────────────────────────────

def test_tool_calls_record_resolved_arguments_and_results():
    section = diag.tools_section([
        diag.tool_call("book_appointment", arguments={"slot": "2026-08-09T10:00"},
                       result="booked #A1", turn=3),
        diag.tool_call("lookup_patient", arguments={"mrn": "S123"},
                       result="Error: not found", ok=False,
                       error="Error: not found", turn=4),
    ], source="harness")
    assert section["summary"]["n_calls"] == 2
    assert section["summary"]["n_ok"] == 1
    assert section["summary"]["n_error"] == 1
    assert section["calls"][0]["arguments"]["slot"] == "2026-08-09T10:00"
    assert section["summary"]["errors"][0]["name"] == "lookup_patient"


def test_oversized_values_are_truncated_not_dropped():
    call = diag.tool_call("big", result="x" * (diag.VALUE_MAX_CHARS + 500))
    assert call["result"].endswith("…")
    assert len(call["result"]) == diag.VALUE_MAX_CHARS


def test_non_write_benchmark_marks_writes_unavailable():
    section = diag.tools_section([], source="s")
    assert section["writes"]["available"] is False
    assert "no writes" in section["writes"]["reason"]
