# Copyright Sierra
"""The comparison harness's honesty contract, offline.

Every test here guards a specific way a vendor-comparison document goes wrong.
None of them touch the network or need a key.

1. **A competitor number can never be invented.** The ElevenLabs adapter without
   credentials must produce a structured refusal with no turns, no score and no
   estimate — and there must be no code path that fills the gap.
2. **A published number can never be built without its citation.** The
   ``published_external`` constructor refuses; rendering marks it visibly; a
   table holding both kinds prints the mixing warning above the numbers.
3. **No matched pair, no verdict.** A Whissle-only run reports
   ``cannot_compare``, and its report says NOT A COMPARISON in the title.
4. **Unknown never resolves toward Whissle.** A criterion that cannot be
   evaluated makes the scenario ``cannot_tell``, including when Whissle has a
   trace and the vendor does not.
5. **The banner is one edit.** Clearing the single outage constant removes it
   from the Markdown AND flips the machine-readable field, with nothing left
   behind.
6. **Absence is not a measurement.** The Whissle adapter's diagnostics envelope
   nulls out what it did not observe, and stamps the OUTAGE (not "text mode") on
   the metadata section while the head is down.
"""
from __future__ import annotations

import json

import pytest

from tau2.compare import baselines as bl
from tau2.compare import compare as cmp
from tau2.compare import criteria as crit
from tau2.compare import evidence as ev
from tau2.compare import honesty, report, scenarios
from tau2.compare.vendors import base as vbase
from tau2.compare.vendors.elevenlabs_convai import (
    ENV_AGENT_ID,
    ENV_API_KEY,
    ElevenLabsConvAIAdapter,
    _verbatim_parity,
)
from tau2.compare.vendors.whissle import TRANSPORT_BENCH, WhissleAdapter
from tau2.health import diagnostics as diag

# ── fixtures ────────────────────────────────────────────────────────────────────

FLOW_TRACE = {
    "current_state": "confirm",
    "steps": [
        {"seq": 1, "turn": 1, "kind": "state_enter", "state": "collect"},
        {"seq": 2, "turn": 1, "kind": "say_emitted", "state": "collect",
         "text": "Sure — what name should I put it under?"},
        {"seq": 3, "turn": 2, "kind": "var_set", "name": "patient_name",
         "value": "Anandini Balasubramanian", "source": "extraction"},
        {"seq": 4, "turn": 3, "kind": "var_set", "name": "patient_name",
         "value": "Nandini Balasubramanian", "source": "extraction"},
        {"seq": 5, "turn": 3, "kind": "transition_check", "from": "collect",
         "to": "confirm", "result": True,
         "reason": "caller corrected the name and both fields are now set"},
        {"seq": 6, "turn": 3, "kind": "state_enter", "state": "confirm"},
    ],
}


def _scenario(**over):
    node = {
        "id": "t_scenario",
        "title": "test scenario",
        "agent_type": "dental_receptionist",
        "system_prompt": "be a receptionist",
        "hypothesis": {
            "expectation": "cascade_wins",
            "claim": "c", "mechanism": "m", "falsifier": "f",
        },
        "turns": ["hello", "my name is Nandini"],
        "pass_criteria": [
            {"id": "greets", "description": "says hello",
             "check": {"kind": "reply_contains_any", "values": ["hello"]}},
        ],
        "trace_evidence": {"description": "a var_set appears",
                           "expect_step_kinds": ["var_set"]},
    }
    node.update(over)
    return scenarios.parse_scenario(node)


def _run(vendor="whissle", *, replies=None, tools=None, users=None,
         runnable=True, tools_visible=True, diagnostics=None, error=None):
    replies = replies if replies is not None else ["hello there", "noted"]
    users = users if users is not None else ["hello", "my name is Nandini"]
    run = vbase.ScenarioRun(
        vendor=vendor, scenario_id="t_scenario", runnable=runnable,
        tools_visible=tools_visible, diagnostics=diagnostics, error=error,
    )
    if runnable and not error:
        for i, (u, r) in enumerate(zip(users, replies), start=1):
            run.turns.append(
                vbase.TurnRecord(index=i, user=u, reply=r,
                                 tools=(tools or {}).get(i, []))
            )
    return run


class _FakeFlowClient:
    """Stands in for ``tau2.flow.client.FlowClient``. Records what it was asked."""

    def __init__(self, *, trace=FLOW_TRACE, tool_events=None, raise_on_turn=None):
        self.trace = trace
        self.tool_events = tool_events or {}
        self.raise_on_turn = raise_on_turn
        self.created = []
        self.deleted = []
        self.turns = []

    def create_typed_agent(self, name, agent_type, system_prompt):
        self.created.append((name, agent_type))
        return {"id": "agent-123", "flow": {"start_state": "collect",
                                            "states": [{"id": "collect"},
                                                       {"id": "confirm"}]}}

    def turn(self, agent_id, message, conversation_id=None):
        from tau2.flow.client import TurnResult

        n = len(self.turns) + 1
        self.turns.append(message)
        if self.raise_on_turn == n:
            raise RuntimeError("boom")
        return TurnResult(
            reply=f"reply to {message}",
            conversation_id="conv-1",
            tools_used=[],
            tool_events=self.tool_events.get(n, []),
            flow=None,
            raw={},
        )

    def get_trace(self, agent_id, conversation_id):
        return self.trace

    def delete_agent(self, agent_id, confirm=False):
        self.deleted.append(agent_id)


# ── 1. scenarios are data, and they are complete ────────────────────────────────


def test_six_scenarios_load_from_data_and_each_declares_a_falsifier():
    defs = scenarios.load()
    assert len(defs) == 6
    ids = {s.id for s in defs}
    assert ids == {
        "misheard_proper_noun",
        "barge_in_interrupt",
        "intent_switch_midturn",
        "hesitation_and_silence",
        "required_field_no_fabrication",
        "mutating_write_matches_speech",
    }
    for s in defs:
        assert s.hypothesis.falsifier.strip(), f"{s.id} has no falsifier"
        assert s.trace_evidence.description.strip(), f"{s.id} declares no evidence"
        assert s.pass_criteria, f"{s.id} has no criteria"
        assert s.turns, f"{s.id} has no turns"


def test_scenarios_cover_both_directions_of_the_cascade_claim():
    """A suite where the cascade wins every scenario is a marketing document."""
    got = {s.hypothesis.expectation for s in scenarios.load()}
    assert scenarios.CASCADE_WINS in got
    assert scenarios.CASCADE_LOSES in got


def test_hypothesis_without_a_falsifier_is_rejected():
    with pytest.raises(scenarios.ScenarioError, match="falsifier"):
        _scenario(hypothesis={"expectation": "cascade_wins", "claim": "c",
                              "mechanism": "m"})


def test_unknown_scenario_id_raises_rather_than_shrinking_the_run():
    with pytest.raises(scenarios.ScenarioError, match="unknown scenario id"):
        scenarios.select(["nope"])


# ── 2. baselines: the provenance taxonomy ───────────────────────────────────────


def test_published_external_requires_full_provenance():
    for missing in ("citation_url", "publication_date", "metric_definition"):
        kwargs = {
            "citation_url": "https://example.com/post",
            "publication_date": "2026-01-01",
            "metric_definition": "their definition",
            "unmatched": ["their task set"],
        }
        kwargs[missing] = ""
        with pytest.raises(bl.BaselineError, match=missing):
            bl.published_external("vendorx", "m", 1.0, **kwargs)


def test_published_external_requires_saying_what_we_could_not_match():
    with pytest.raises(bl.BaselineError, match="unmatched"):
        bl.published_external(
            "vendorx", "m", 1.0,
            citation_url="https://example.com/post",
            publication_date="2026-01-01",
            metric_definition="their definition",
            unmatched=[],
        )


def test_setup_matched_must_name_its_scenarios():
    with pytest.raises(bl.BaselineError, match="scenario_ids"):
        bl.setup_matched("whissle", "m", 1.0, scenario_ids=[])


def test_published_numbers_are_visibly_labelled_and_measured_ones_are_not():
    published = bl.published_external(
        "vendorx", "m", 90.0, citation_url="https://example.com/p",
        publication_date="2026-01-01", metric_definition="theirs",
        unmatched=["task set"],
    )
    measured = bl.setup_matched("whissle", "m", 1.0, scenario_ids=["s1"])
    assert bl.PUBLISHED_MARKER in published.label()
    assert bl.PUBLISHED_MARKER not in measured.label()
    assert published.is_measured is False and measured.is_measured is True


def test_mixing_the_two_kinds_prints_a_loud_warning_above_the_numbers():
    both = [
        bl.setup_matched("whissle", "m", 1.0, scenario_ids=["s1"]),
        bl.published_external("vendorx", "m", 90.0,
                              citation_url="https://example.com/p",
                              publication_date="2026-01-01",
                              metric_definition="theirs",
                              unmatched=["task set"]),
    ]
    assert bl.mixing_warning(both) == bl.MIXED_KINDS_WARNING
    table = bl.render_table(both)
    assert table.index(bl.MIXED_KINDS_WARNING) < table.index("| System |")
    # And a homogeneous table must NOT cry wolf.
    assert bl.mixing_warning(both[:1]) is None


def test_medagent_leaderboard_is_expressed_as_published_external():
    got = bl.medagent_published_baselines()
    assert got and all(b.kind == bl.PUBLISHED_EXTERNAL for b in got)
    assert all(b.unmatched and b.citation_url for b in got)


# ── 3. honesty banner: one constant, removable in one edit ──────────────────────


def test_banner_is_present_and_machine_readable_while_the_head_is_down():
    assert honesty.differentiator_status() == "disabled"
    banner = honesty.banner_markdown()
    assert "136.115.121.123:50051" in banner
    assert "2026-08-08" in banner
    block = honesty.banner_block()
    assert block["differentiator_status"] == "disabled"
    assert block["outage"]["target"] == "136.115.121.123:50051"


def test_clearing_the_single_constant_removes_the_banner_everywhere(monkeypatch):
    monkeypatch.setattr(honesty, "DIFFERENTIATOR_OUTAGE", None)
    assert honesty.differentiator_status() == "operational"
    assert honesty.banner_markdown() == ""
    assert honesty.metadata_unavailable_reason() is None
    assert honesty.banner_block()["outage"] is None


# ── 4. ElevenLabs: refuse, never fabricate ──────────────────────────────────────


def test_elevenlabs_without_credentials_refuses_and_reports_nothing(monkeypatch):
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    monkeypatch.delenv(ENV_AGENT_ID, raising=False)
    adapter = ElevenLabsConvAIAdapter()
    pre = adapter.preflight()
    assert pre.runnable is False
    assert set(pre.missing_env) == {ENV_API_KEY, ENV_AGENT_ID}

    run = adapter.run_scenario(_scenario())
    assert run.runnable is False
    assert run.measured is False
    assert run.turns == []
    assert run.diagnostics is None
    assert "credentials absent" in run.not_runnable_reason
    # The whole point: no score of any kind came out of a vendor we never called.
    # Not a zero, not a null-that-reads-as-zero — the object carries no result at
    # all, and the criterion evaluator can only answer "cannot tell" about it.
    checks = crit.evaluate(_scenario(), run)
    assert all(c.passed is None for c in checks)
    assert crit.verdict(checks)[0] is None
    assert run.to_dict()["measured"] is False


def test_elevenlabs_preflight_never_touches_the_network(monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "k")
    monkeypatch.setenv(ENV_AGENT_ID, "a")

    import requests

    def _boom(*a, **k):  # pragma: no cover - must never be reached
        raise AssertionError("preflight must not make an HTTP call")

    monkeypatch.setattr(requests, "post", _boom)
    monkeypatch.setattr(requests, "get", _boom)
    assert ElevenLabsConvAIAdapter().preflight().runnable is True


def test_elevenlabs_run_folds_the_simulation_and_flags_utterance_drift(monkeypatch):
    monkeypatch.setenv(ENV_API_KEY, "k")
    monkeypatch.setenv(ENV_AGENT_ID, "a")
    scenario = _scenario()

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {
                "simulated_conversation": [
                    {"role": "user", "message": "hello"},
                    {"role": "agent", "message": "hi there"},
                    # Drifted: the script said "my name is Nandini".
                    {"role": "user", "message": "the name is Nandini I think"},
                    {"role": "agent", "message": "got it",
                     "tool_calls": [{"tool_name": "book",
                                     "params_as_json": {"name": "Nandini"}}]},
                ]
            }

    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())
    run = ElevenLabsConvAIAdapter().run_scenario(scenario)
    assert run.measured is True
    assert [t.reply for t in run.turns] == ["hi there", "got it"]
    prov = run.diagnostics["provenance"]
    assert prov["utterances_matched"] is False
    assert any("did NOT deliver the script verbatim" in c for c in run.setup_caveats)
    # A vendor with no trace says so, rather than showing an empty flow.
    assert run.diagnostics["flow"]["available"] is False
    assert run.diagnostics["flow"]["steps"] is None


def test_verbatim_parity_accepts_an_exact_script():
    turns = [vbase.TurnRecord(index=1, user="hello", reply="hi"),
             vbase.TurnRecord(index=2, user="my name is Nandini", reply="ok")]
    parity = _verbatim_parity(["hello", "my name is Nandini"], turns)
    assert parity["matched"] is True and parity["caveats"] == []


# ── 5. criteria: unknown is a third answer ──────────────────────────────────────


def test_tool_criteria_against_a_vendor_with_no_tool_record_are_unknown_not_failed():
    run = _run("elevenlabs", tools_visible=False)
    res = crit.evaluate_check({"kind": "tool_called", "tool": "book"}, run)
    assert res.passed is None
    assert "no per-turn tool-call record" in res.reason


def test_from_turn_scope_only_looks_after_the_correction():
    run = _run(replies=["Anandini?", "Nandini, got it"],
               users=["my name is Nandini", "no — Nandini"])
    spec = {"kind": "reply_not_contains_any", "values": ["Anandini"]}
    assert crit.evaluate_check(spec, run).passed is False
    scoped = {**spec, "scope": "from_turn", "index": 2}
    assert crit.evaluate_check(scoped, run).passed is True


def test_fabrication_check_passes_on_no_write_and_fails_on_an_invented_value():
    grounded = {"kind": "tool_arg_grounded_in_user", "arg": ["dob", "date_of_birth"]}

    nothing_written = _run()
    res = crit.evaluate_check(grounded, nothing_written)
    assert res.passed is True and "nothing was invented" in res.reason

    invented = _run(tools={2: [diag.tool_call("book",
                                              arguments={"dob": "1984-02-11"})]})
    res = crit.evaluate_check(grounded, invented)
    assert res.passed is False and "FABRICATED" in res.reason

    said = _run(users=["hello", "my dob is 1984-02-11"],
                tools={2: [diag.tool_call("book",
                                          arguments={"dob": "1984-02-11"})]})
    assert crit.evaluate_check(grounded, said).passed is True


def test_written_value_must_be_echoed_in_the_spoken_confirmation():
    spec = {"kind": "tool_arg_echoed_in_reply", "arg": ["time", "start_time"]}
    agrees = _run(replies=["ok", "booked for 10:00 on Tuesday"],
                  tools={2: [diag.tool_call("book", arguments={"time": "10:00"})]})
    assert crit.evaluate_check(spec, agrees).passed is True

    diverges = _run(replies=["ok", "booked for 10:00 on Tuesday"],
                    tools={2: [diag.tool_call("book",
                                              arguments={"time": "14:00"})]})
    res = crit.evaluate_check(spec, diverges)
    assert res.passed is False and "diverge" in res.reason


def test_verdict_lets_unknown_beat_pass_but_not_a_critical_failure():
    ok = crit.CheckResult("a", True, "fine")
    unknown = crit.CheckResult("b", None, "no record")
    critical_fail = crit.CheckResult("c", False, "fabricated", critical=True)

    assert crit.verdict([ok, ok])[0] is True
    assert crit.verdict([ok, unknown])[0] is None
    assert crit.verdict([ok, unknown, critical_fail])[0] is False


def test_an_unimplemented_check_is_an_error_not_a_silent_unknown():
    with pytest.raises(crit.CheckSpecError):
        crit.evaluate_check({"kind": "vibes"}, _run())


# ── 6. comparison: refuse without a matched pair ────────────────────────────────


def test_whissle_only_run_refuses_to_produce_a_head_to_head():
    scenario = _scenario()
    runs = {
        "whissle": _run("whissle"),
        "elevenlabs": vbase.not_runnable(
            "elevenlabs", scenario.id, vbase.REASON_CREDENTIALS_ABSENT),
    }
    comparison = cmp.compare_scenario(scenario, runs)
    assert comparison.verdict == cmp.CANNOT_COMPARE
    assert comparison.comparable is False
    assert "only one vendor" in comparison.verdict_reason
    assert comparison.baselines == []


def test_drifted_utterances_break_the_matched_pair():
    scenario = _scenario()
    vendor_run = _run("elevenlabs")
    vendor_run.diagnostics = {"provenance": {"utterances_matched": False}}
    comparison = cmp.compare_scenario(
        scenario, {"whissle": _run("whissle"), "elevenlabs": vendor_run})
    assert comparison.verdict == cmp.CANNOT_COMPARE
    assert "did not hear the same utterances" in comparison.verdict_reason


def test_a_matched_pair_produces_a_verdict():
    scenario = _scenario()
    comparison = cmp.compare_scenario(scenario, {
        "whissle": _run("whissle", replies=["hello there", "ok"]),
        "elevenlabs": _run("elevenlabs", replies=["good day", "ok"]),
    })
    assert comparison.verdict == cmp.WIN
    assert comparison.comparable is True
    assert {b.kind for b in comparison.baselines} == {bl.SETUP_MATCHED}


def test_unknown_is_never_resolved_in_whissles_favour():
    """Whissle passes; the vendor's result is unmeasurable. That is CANNOT TELL —
    having more evidence than your competitor is not the same as winning."""
    scenario = _scenario(pass_criteria=[
        {"id": "used_a_tool", "description": "called a tool",
         "check": {"kind": "tool_called", "tool": "book"}},
    ])
    whissle = _run("whissle", tools={1: [diag.tool_call("book")]})
    vendor = _run("elevenlabs", tools_visible=False)
    comparison = cmp.compare_scenario(scenario, {"whissle": whissle,
                                                 "elevenlabs": vendor})
    assert comparison.verdict == cmp.CANNOT_TELL
    assert "not resolved in either vendor's favour" in comparison.verdict_reason


def test_a_win_without_mechanism_evidence_says_so_in_its_own_reason():
    scenario = _scenario()
    whissle = _run("whissle", replies=["hello there", "ok"])  # no diagnostics
    vendor = _run("elevenlabs", replies=["nope", "nope"])
    comparison = cmp.compare_scenario(scenario, {"whissle": whissle,
                                                 "elevenlabs": vendor})
    assert comparison.verdict == cmp.WIN
    assert "does not support the scenario's hypothesis" in comparison.verdict_reason


# ── 7. evidence: the trace, and its honest absence ──────────────────────────────


def test_evidence_reads_the_trace_and_narrates_the_reason():
    scenario = _scenario()
    run = _run("whissle")
    run.diagnostics = {"flow": diag.flow_section(FLOW_TRACE["steps"],
                                                 source="test")}
    result = ev.evaluate(scenario, run)
    assert result.status == ev.FOUND
    joined = " ".join(result.narrative)
    assert "caller corrected the name" in joined
    assert "set `patient_name`" in joined


def test_evidence_without_a_trace_is_cannot_tell_not_absent():
    scenario = _scenario()
    run = _run("whissle")
    run.diagnostics = {"flow": diag.flow_unavailable(diag.REASON_BENCH_ENDPOINT)}
    result = ev.evaluate(scenario, run)
    assert result.status == ev.CANNOT_TELL
    assert result.fired is None
    assert "stateless brain call" in result.reason


def test_a_mechanism_needing_the_disabled_head_cannot_be_proven_today():
    scenario = _scenario(trace_evidence={"description": "hesitation frames",
                                         "requires_metadata_head": True})
    run = _run("whissle")
    run.diagnostics = {"flow": diag.flow_section(FLOW_TRACE["steps"],
                                                 source="test")}
    result = ev.evaluate(scenario, run)
    assert result.status == ev.CANNOT_TELL
    assert "disabled" in result.reason


def test_a_voice_signal_mechanism_is_cannot_tell_on_a_text_run():
    scenario = _scenario(trace_evidence={"description": "barge-in signal",
                                         "requires_voice_signals": True})
    run = _run("whissle")
    run.diagnostics = {
        "flow": diag.flow_section(FLOW_TRACE["steps"], source="test"),
        "signals": diag.signals_unavailable(diag.REASON_TEXT_MODE),
    }
    result = ev.evaluate(scenario, run)
    assert result.status == ev.CANNOT_TELL
    assert "VOICE signals" in result.reason


def test_var_set_steps_keyed_key_are_recognised_not_silently_dropped():
    """Regression from a live run: the deployed flow engine names the field ``key``,
    which the shared diagnostics rollup normalises away to ``name: None``. Reading
    only the rollup would make the anti-fabrication evidence (``expect_no_vars_set``)
    trivially satisfiable — a written field would look like a field never written."""
    steps = [
        {"seq": 1, "turn": 1, "kind": "var_set", "key": "date_of_birth",
         "value": "1984-02-11", "source": "extraction"},
    ]
    flow = diag.flow_section(steps, source="test")
    assert flow["var_sets"][0]["name"] is None  # the rollup really does drop it

    scenario = _scenario(trace_evidence={
        "description": "nothing was written for the refused field",
        "expect_no_vars_set": ["date_of_birth"],
    })
    run = _run("whissle")
    run.diagnostics = {"flow": flow}
    result = ev.evaluate(scenario, run)
    assert result.status == ev.ABSENT
    assert "no_var_set:date_of_birth" in result.unsatisfied
    assert any("date_of_birth" in line for line in result.narrative)


def test_a_non_firing_transition_prints_its_condition_not_an_empty_target():
    flow = diag.flow_section(
        [{"seq": 1, "turn": 1, "kind": "transition_check", "from": "triage",
          "result": "not_satisfied", "condition": "caller wants to reschedule",
          "reason": "caller asked to book, not reschedule"}],
        source="test",
    )
    line = ev.narrative(flow)[0]
    assert "None" not in line
    assert "caller wants to reschedule" in line
    assert "did not fire" in line


def test_a_read_trace_missing_the_mechanism_is_absent_not_unknown():
    scenario = _scenario(trace_evidence={"description": "a guard trips",
                                         "expect_guard_trip": True})
    run = _run("whissle")
    run.diagnostics = {"flow": diag.flow_section(FLOW_TRACE["steps"],
                                                 source="test")}
    result = ev.evaluate(scenario, run)
    assert result.status == ev.ABSENT
    assert result.fired is False


# ── 8. the Whissle adapter's diagnostics envelope ───────────────────────────────


def test_whissle_run_captures_the_trace_and_stamps_the_outage_on_metadata(monkeypatch):
    adapter = WhissleAdapter(api_key="wsk_test")
    fake = _FakeFlowClient()
    monkeypatch.setattr(adapter, "_client", lambda: fake)

    run = adapter.run_scenario(_scenario())
    assert run.measured is True
    assert fake.created == [("compare-t_scenario-" + fake.created[0][0].split("-")[-1],
                             "dental_receptionist")]
    assert fake.deleted == ["agent-123"], "the throwaway agent must be torn down"

    d = run.diagnostics
    assert d["schema"] == diag.SCHEMA
    assert d["flow"]["available"] is True
    assert [s["name"] for s in d["flow"]["var_sets"]] == ["patient_name",
                                                          "patient_name"]
    # Absence is not a measurement, and the RIGHT absence is recorded: the metadata
    # section blames the outage, not the transport.
    assert d["metadata_sidecar"]["available"] is False
    assert "NOT producing in production" in d["metadata_sidecar"]["reason"]
    assert d["metadata_sidecar"]["turns"] is None
    assert d["signals"]["available"] is False
    assert d["availability"]["flow_available"] is True
    assert d["provenance"]["differentiator_status"] == "disabled"


def test_whissle_bench_transport_records_why_it_has_no_trace(monkeypatch):
    adapter = WhissleAdapter(api_key="wsk_test", transport=TRANSPORT_BENCH)

    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"reply": "hi"}

    import requests

    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())
    run = adapter.run_scenario(_scenario())
    assert run.diagnostics["flow"]["available"] is False
    assert run.diagnostics["flow"]["reason"] == diag.REASON_BENCH_ENDPOINT
    assert run.tools_visible is False


def test_whissle_run_that_errors_reports_the_error_and_still_tears_down(monkeypatch):
    adapter = WhissleAdapter(api_key="wsk_test")
    fake = _FakeFlowClient(raise_on_turn=2)
    monkeypatch.setattr(adapter, "_client", lambda: fake)
    run = adapter.run_scenario(_scenario())
    assert run.error and "boom" in run.error
    assert run.measured is True or run.turns  # partial turns are kept, not discarded
    assert fake.deleted == ["agent-123"]


# ── 9. the report ───────────────────────────────────────────────────────────────


def _report_data(is_comparison: bool):
    scenario = _scenario()
    whissle = _run("whissle")
    whissle.diagnostics = {"flow": diag.flow_section(FLOW_TRACE["steps"],
                                                     source="test")}
    if is_comparison:
        runs = {"whissle": whissle, "elevenlabs": _run("elevenlabs")}
    else:
        runs = {
            "whissle": whissle,
            "elevenlabs": vbase.not_runnable(
                "elevenlabs", scenario.id, vbase.REASON_CREDENTIALS_ABSENT),
        }
    comparison = cmp.compare_scenario(scenario, runs)
    return cmp.build_report_data(
        "test-run", ["whissle", "elevenlabs"], [comparison],
        {"whissle": {"runnable": True, "reason": None, "missing_env": []},
         "elevenlabs": {"runnable": False,
                        "reason": vbase.REASON_CREDENTIALS_ABSENT,
                        "missing_env": [ENV_API_KEY, ENV_AGENT_ID]}},
    )


def test_the_banner_is_the_first_thing_on_the_page_before_any_number():
    md = report.render_markdown(_report_data(is_comparison=True))
    assert md.startswith("# ")
    assert "READ THIS FIRST" in md
    assert md.index("READ THIS FIRST") < md.index("Rollup")


def test_a_whissle_only_run_is_titled_not_a_comparison_and_says_so_in_json():
    data = _report_data(is_comparison=False)
    assert data.is_comparison is False
    md = report.render_markdown(data)
    assert "NOT A COMPARISON" in md.splitlines()[0]
    assert bl.NO_SETUP_MATCHED[:40] in md
    payload = report.render_json(data)
    assert payload["is_comparison"] is False
    assert payload["differentiator_status"] == "disabled"
    assert payload["not_a_comparison_reason"]
    assert report.summary_line(data).startswith("test-run: NOT A COMPARISON")


def test_every_scenario_prints_the_trace_narrative_explaining_why():
    md = report.render_markdown(_report_data(is_comparison=True))
    assert "Why Whissle did what it did (flow trace)" in md
    assert "caller corrected the name" in md


def test_report_json_always_carries_the_machine_readable_status():
    payload = report.render_json(_report_data(is_comparison=True))
    assert payload["differentiator_status"] == "disabled"
    assert payload["outage"]["symptom"].startswith("gRPC metadata target")
    assert payload["schema"] == "tau2.compare.report/v1"


def test_report_writes_both_formats_and_per_run_case_files(tmp_path):
    data = _report_data(is_comparison=False)
    paths = report.write(data, str(tmp_path))
    cases = report.write_runs(data, str(tmp_path))
    assert paths["markdown"].endswith("report.md")
    written = json.loads(open(paths["json"], encoding="utf-8").read())
    assert written["differentiator_status"] == "disabled"
    assert len(cases) == 2
    case = json.loads(open(cases[0], encoding="utf-8").read())
    assert case["differentiator_status"] == "disabled"


def test_rerender_flags_a_status_change_between_run_and_render(tmp_path, monkeypatch):
    data = _report_data(is_comparison=False)
    paths = report.write(data, str(tmp_path))
    monkeypatch.setattr(honesty, "DIFFERENTIATOR_OUTAGE", None)
    text = report.rerender(paths["json"])
    assert "differentiator status CHANGED" in text


# ── 10. the CLI ─────────────────────────────────────────────────────────────────


def test_cli_list_succeeds_with_no_credentials(monkeypatch, capsys):
    from tau2.compare import run as cli

    monkeypatch.delenv(ENV_API_KEY, raising=False)
    monkeypatch.delenv(ENV_AGENT_ID, raising=False)
    assert cli.main(["list"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "READ THIS FIRST" in out
    assert "elevenlabs: NOT RUNNABLE" in out


def test_cli_run_without_elevenlabs_credentials_succeeds_and_marks_the_report(
    monkeypatch, tmp_path,
):
    from tau2.compare import run as cli
    from tau2.compare.vendors import whissle as whissle_mod

    monkeypatch.delenv(ENV_API_KEY, raising=False)
    monkeypatch.delenv(ENV_AGENT_ID, raising=False)
    monkeypatch.setenv("WHISSLE_API_KEY", "wsk_test")
    monkeypatch.setattr(
        whissle_mod.WhissleAdapter, "_client", lambda self: _FakeFlowClient())

    code = cli.main([
        "run", "--vendor", "whissle,elevenlabs",
        "--scenario", "misheard_proper_noun",
        "--out", str(tmp_path), "--run-id", "cli-test",
    ])
    assert code == cli.EXIT_OK
    payload = json.loads(
        open(tmp_path / "cli-test" / "report.json", encoding="utf-8").read())
    assert payload["is_comparison"] is False
    assert payload["differentiator_status"] == "disabled"
    md = open(tmp_path / "cli-test" / "report.md", encoding="utf-8").read()
    assert "NOT A COMPARISON" in md


def test_cli_rejects_an_unknown_vendor(monkeypatch):
    from tau2.compare import run as cli

    assert cli.main(["run", "--vendor", "acme"]) == cli.EXIT_USAGE


def test_cli_exits_nonzero_when_there_is_nothing_to_measure(monkeypatch):
    from tau2.compare import run as cli

    monkeypatch.delenv("WHISSLE_API_KEY", raising=False)
    assert cli.main(["run", "--vendor", "whissle"]) == cli.EXIT_NOTHING_TO_MEASURE
