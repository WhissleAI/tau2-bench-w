# Copyright Sierra
"""The three health adapters emit ONE artifact shape — verified per adapter, offline.

The consistency claim is the load-bearing one: a single reader/report tool has to
work across PatientAgentBench, MedAgentBench and AgentClinic. So every adapter's
envelope is checked against the same structural assertions, and then each is
checked for the thing only IT can get wrong:

* PatientAgentBench — voice signals reach the artifact at all (its turn loop is
  upstream's, so the only path is ``response_metadata`` → transcript → envelope);
* MedAgentBench     — the said-vs-emitted-vs-landed write verdict survives;
* AgentClinic       — text and voice produce visibly different availability.

No network, no key: HTTP is monkeypatched and the run directories are fixtures.
"""
from __future__ import annotations

import json

import pytest

from tau2.health import diagnostics as diag
from tau2.health.agentclinic import diagnostics as clinic_diag
from tau2.health.medagent import diagnostics as med_diag
from tau2.health.patientagent import collect as pab_collect
from tau2.health.patientagent import diagnostics as pab_diag

REQUIRED_SECTIONS = ("flow", "signals", "metadata_sidecar", "tools", "provenance",
                     "cost", "availability")


def assert_shared_shape(envelope: dict) -> None:
    """The contract one reader depends on, asserted identically for all three."""
    assert envelope["schema"] == diag.SCHEMA
    for key in REQUIRED_SECTIONS:
        assert key in envelope, f"missing shared section {key!r}"
    for name in ("flow", "signals", "metadata_sidecar", "tools"):
        section = envelope[name]
        assert "available" in section and "reason" in section
        if not section["available"]:
            assert section["reason"], f"{name} is unavailable without saying why"
    av = envelope["availability"]
    for name in ("flow", "signals", "metadata_sidecar", "tools"):
        assert av[f"{name}_available"] == bool(envelope[name]["available"])
    assert envelope["provenance"]["benchmark"] == envelope["benchmark"]
    assert envelope["provenance"]["mode"] == envelope["mode"]
    assert envelope["provenance"]["transport_endpoint"]


# ── PatientAgentBench ───────────────────────────────────────────────────────────

TEXT_TRANSCRIPT = [
    {"index": 0, "role": "human", "content": "I have a headache."},
    {"index": 1, "role": "ai", "content": "Let me check the schedule."},
    {"index": 2, "role": "tool", "content": "3 slots available"},
]
TEXT_TOOL_CALLS = [{"turn_index": 1, "name": "check_availability",
                    "args": {"dept": "neurology"}, "id": "t1"}]

VOICE_TRANSCRIPT = [
    {"index": 0, "role": "human", "content": "Hello?"},
    {"index": 1, "role": "ai", "content": "Thanks for calling.",
     "voice": {"turn": 1, "latency_ms": 640, "boundary": "text",
               "room": "bench-1", "conversation_id": "conv-9",
               "signals": [{"signal": "hesitation", "p": 0.6}],
               "user_metadata": [{"emotion": "anxious", "intent": "report_symptom"}],
               "hesitant_input": True}},
]


def test_pab_text_mode_marks_voice_signals_unavailable():
    env = pab_diag.build(case_id="c1", mode="harness", transcript=TEXT_TRANSCRIPT,
                         raw_tool_calls=TEXT_TOOL_CALLS)
    assert_shared_shape(env)
    assert env["signals"]["available"] is False
    assert env["signals"]["turns"] is None
    assert env["metadata_sidecar"]["available"] is False
    assert env["flow"]["available"] is False
    # The reason must name the ENDPOINT, not just say "no trace" — the fact that
    # /api/bench/agent-turn runs no flow engine is the finding.
    assert "agent-turn" in env["flow"]["reason"]
    assert env["provenance"]["transport_endpoint"] == "POST /api/bench/agent-turn"


def test_pab_text_mode_pairs_tool_calls_with_their_results():
    env = pab_diag.build(case_id="c1", mode="harness", transcript=TEXT_TRANSCRIPT,
                         raw_tool_calls=TEXT_TOOL_CALLS)
    call = env["tools"]["calls"][0]
    assert call["name"] == "check_availability"
    assert call["arguments"] == {"dept": "neurology"}
    assert call["result"] == "3 slots available"
    assert call["ok"] is True


def test_pab_voice_mode_captures_signals_and_metadata():
    """The whole point of the voice arm: these frames only exist over audio, and
    before this they were dropped between the transport and the artifact."""
    env = pab_diag.build(case_id="c2", mode="voice", transcript=VOICE_TRANSCRIPT,
                         raw_tool_calls=[])
    assert_shared_shape(env)
    assert env["signals"]["available"] is True
    assert env["signals"]["summary"]["by_kind"] == {"hesitation": 1}
    assert env["signals"]["summary"]["hesitation_turns"] == [1]
    assert env["metadata_sidecar"]["summary"]["emotions_seen"] == {"anxious": 1}
    assert env["provenance"]["transport_endpoint"].startswith("POST /api/bench/voice/start")


def test_pab_voice_mode_fetches_the_flow_trace(monkeypatch):
    client = diag.TraceClient(base="http://test", api_key="wsk_test")
    monkeypatch.setattr(client, "_get", lambda path: {
        "steps": [{"seq": 1, "turn": 1, "kind": "state_enter", "state": "intake"}],
        "current_state": "intake"})
    env = pab_diag.build(case_id="c2", mode="voice", transcript=VOICE_TRANSCRIPT,
                         raw_tool_calls=[], trace_client=client,
                         provenance_extra={"agent_id": "a1"})
    assert env["flow"]["available"] is True
    assert env["flow"]["states_visited"] == ["intake"]
    assert env["flow"]["conversation_id"] == "conv-9"


def test_pab_per_case_provenance_and_cost_allocation():
    env = pab_diag.build(
        case_id="c1", mode="harness", transcript=TEXT_TRANSCRIPT,
        raw_tool_calls=TEXT_TOOL_CALLS,
        scenario={"task_type": "emergency", "severity_level": "high"},
        judge={"judge_provider": "whissle", "judge_independent": False,
               "judge_calls": 40, "judge_cost_usd": 0.8},
        sampling={"seed": 42, "strata_keys": ["task_type"], "n_selected": 10},
        provenance_extra={"agent_id": "agent-x", "base_url": "https://x/bot"},
        n_cases=10)
    p = env["provenance"]
    assert p["agent_id"] == "agent-x"
    assert p["seed"] == 42
    assert p["stratum"]["severity_level"] == "high"
    assert p["judge_independent"] is False
    assert env["cost"]["judge_calls"] == 4.0
    assert env["cost"]["run_judge_calls"] == 40
    # Labelled as an allocation so nobody quotes it as a measured per-case cost.
    assert "allocation" in env["cost"]["allocation"]


def test_pab_collect_writes_diagnostics_into_the_case_file(tmp_path):
    """End to end through the real collector: fixture run dir in, case file with a
    diagnostics envelope out."""
    exp = tmp_path / "0_0"
    exp.mkdir()
    (exp / "conversations.json").write_text(json.dumps([{
        "case_id": "case-1", "num_turns": 2, "personality": "anxious",
        "conversation": [
            {"type": "human", "content": "I have a headache."},
            {"type": "ai", "content": "Checking.",
             "tool_calls": [{"name": "check_availability", "args": {"d": 1}, "id": "x"}]},
            {"type": "tool", "content": "3 slots available"},
        ]}]))
    (exp / "evaluations.json").write_text(json.dumps([{
        "case_id": "case-1",
        "evaluation": {"rubric_results": {"task_completion": {"score": 4.0,
                                                              "pass": True}}}}]))

    artifacts = tmp_path / "cases"
    pab_collect.collect_outcomes(
        str(exp), artifact_dir=str(artifacts),
        case_metadata={"case-1": {"task_type": "emergency",
                                  "severity_level": "high"}},
        diagnostics=pab_collect.DiagnosticsContext(
            mode="harness",
            judge={"judge_provider": "whissle", "judge_independent": False},
            sampling={"seed": 42, "n_selected": 1},
            provenance={"agent_id": "a1", "base_url": "https://x/bot"},
            run_dir=str(tmp_path), n_cases=1))

    record = json.loads((artifacts / "case-1.json").read_text())
    # Every pre-existing key survives — the old readers must not break.
    for key in ("case_id", "status", "rubric_scores", "aggregate_score", "meta",
                "scenario", "transcript", "tool_calls", "evaluation"):
        assert key in record
    env = record["diagnostics"]
    assert_shared_shape(env)
    assert env["availability"]["signals_available"] is False
    assert env["provenance"]["stratum"]["severity_level"] == "high"
    assert env["tools"]["calls"][0]["result"] == "3 slots available"


def test_pab_collect_without_context_writes_no_fabricated_provenance(tmp_path):
    """Re-reporting an OLD run must not invent an agent id or a judge."""
    exp = tmp_path / "0_0"
    exp.mkdir()
    (exp / "conversations.json").write_text(json.dumps([
        {"case_id": "case-1", "conversation": []}]))
    (exp / "evaluations.json").write_text("[]")
    artifacts = tmp_path / "cases"
    pab_collect.collect_outcomes(str(exp), artifact_dir=str(artifacts))
    record = json.loads((artifacts / "case-1.json").read_text())
    assert "diagnostics" not in record


# ── MedAgentBench ───────────────────────────────────────────────────────────────

def _med_result(*, said: bool, emitted: int, landed: int) -> dict:
    return {
        "task_id": "task8-0", "category": "task8", "attempt": 1,
        "turns": [
            {"round": 1, "action_kind": "GET", "url": "/Patient?mrn=S123",
             "observation": "[{...}]", "latency_ms": 300},
            {"round": 2, "action_kind": "POST", "url": "/Observation",
             "payload": {"resourceType": "Observation", "valueQuantity": {"value": 7}},
             "observation": "POST request accepted", "latency_ms": 410},
            {"round": 3, "action_kind": "GET", "url": "/bad",
             "observation": "Error: unknown resource"},
        ],
        "integrity": {
            "task_id": "task8-0", "category": "task8", "is_action_category": True,
            "said_action": said,
            "said_evidence": "I have recorded the observation." if said else None,
            "emitted_writes": emitted, "attempted_writes": emitted,
            "accepted_by_ehr": landed, "rejected_by_ehr": emitted - landed,
            "verified_writes": landed, "nonconformant_writes": 0,
            "write_check_mode": "execute",
            "said_not_emitted": bool(said and emitted == 0),
            "emitted_not_said": bool(emitted and not said),
            "emitted_not_accepted": bool(emitted and not landed),
            "emitted_nonconformant": False,
            "write_attempts": [{"url": "/Observation", "accepted": bool(landed)}],
        },
    }


def test_med_envelope_matches_the_shared_shape():
    env = med_diag.build(_med_result(said=True, emitted=1, landed=1),
                         run_meta={"agent_id": "a1", "base": "https://x/bot",
                                   "endpoint": "/api/bench/agent-turn"})
    assert_shared_shape(env)
    assert env["flow"]["available"] is False
    assert env["signals"]["available"] is False
    assert env["cost"]["available"] is False  # deterministic graders, no judge spend


def test_med_said_but_never_emitted_survives_into_the_record():
    """The headline integrity failure: the agent told the clinician the order was
    filed and no POST ever left. Flattening this to "tools called: 0" is exactly
    what the write block exists to prevent."""
    env = med_diag.build(_med_result(said=True, emitted=0, landed=0))
    writes = env["tools"]["writes"]
    assert writes["available"] is True
    assert writes["said_action"] is True
    assert writes["emitted_writes"] == 0
    assert writes["said_not_emitted"] is True
    assert writes["verdict"].startswith("SAID but never EMITTED")
    assert writes["said_evidence"]


def test_med_emitted_but_rejected_is_distinct_from_landed():
    rejected = med_diag.build(_med_result(said=True, emitted=1, landed=0))
    landed = med_diag.build(_med_result(said=True, emitted=1, landed=1))
    assert rejected["tools"]["writes"]["verdict"].startswith("EMITTED but NOT ACCEPTED")
    assert landed["tools"]["writes"]["verdict"].startswith("EMITTED and LANDED")
    assert rejected["tools"]["writes"]["landed_writes"] == 0
    assert landed["tools"]["writes"]["landed_writes"] == 1


def test_med_tool_calls_carry_resolved_urls_payloads_and_failures():
    env = med_diag.build(_med_result(said=True, emitted=1, landed=1))
    calls = env["tools"]["calls"]
    assert [c["name"] for c in calls] == ["GET", "POST", "GET"]
    assert calls[1]["arguments"]["url"] == "/Observation"
    assert calls[1]["arguments"]["payload"]["resourceType"] == "Observation"
    assert calls[2]["ok"] is False and "Error" in calls[2]["error"]
    assert env["tools"]["summary"]["n_error"] == 1


def test_med_write_artifacts_attaches_the_envelope(tmp_path, monkeypatch):
    """Through the real writer, so the wiring — not just the builder — is covered."""
    from tau2.health.medagent import report as med_report

    class FakeResult:
        task_id = "task8-0"

        def as_dict(self):
            return _med_result(said=True, emitted=0, landed=0)

    # A real summary shape, built by the real summarizer, so the writer is exercised
    # exactly as a run exercises it.
    summary = med_report.summarize(
        [], mode="b", run_meta={"agent_id": "a1", "base": "https://x/bot",
                                "endpoint": "/api/bench/agent-turn"})
    run_dir = med_report.write_artifacts([FakeResult()], summary,
                                         root=tmp_path, run_name="t")
    record = json.loads((run_dir / "tasks" / "task8-0.json").read_text())
    assert record["integrity"]["said_not_emitted"] is True   # original key intact
    assert_shared_shape(record["diagnostics"])
    assert record["diagnostics"]["tools"]["writes"]["said_not_emitted"] is True
    assert record["diagnostics"]["provenance"]["agent_id"] == "a1"


# ── AgentClinic ─────────────────────────────────────────────────────────────────

CLINIC_TEXT_CASE = {
    "scenario_id": "MedQA-3", "scenario_index": 3, "dataset": "MedQA", "mode": "text",
    "inferences_used": 4, "support_llm_calls": 9, "support_llm_cost_usd": 0.04,
    "dialogue": [
        {"role": "doctor", "inference": 1, "kind": "question", "text": "What hurts?"},
        {"role": "patient", "text": "My head."},
        {"role": "doctor", "inference": 2, "kind": "test", "payload": "CBC",
         "text": "REQUEST TEST: CBC", "latency_ms": 900},
        {"role": "measurement", "text": "WBC 12.1"},
        {"role": "doctor", "inference": 3, "kind": "diagnosis", "payload": "Migraine",
         "text": "DIAGNOSIS READY: Migraine"},
    ],
}

CLINIC_VOICE_CASE = {
    **CLINIC_TEXT_CASE, "mode": "voice",
    "voice": {"room": "bench-2", "conversation_id": "conv-7",
              "latencies_ms": [700, 820],
              "turns": [
                  {"inference": 1, "latency_ms": 700,
                   "signals": [{"signal": "hesitation", "p": 0.5}],
                   "user_metadata": [{"emotion": "worried", "intent": "describe"}]},
                  {"inference": 2, "latency_ms": 820, "signals": [],
                   "user_metadata": []},
              ]},
}

CLINIC_META = {"agent_id": "a1", "base": "https://x/bot", "seed": 7, "sample": "random",
               "limit": 5, "dataset": "MedQA", "protocol": "markers",
               "judge_provider": "whissle", "judge_independent": False,
               "total_inferences": 20}


def test_clinic_text_mode_availability():
    env = clinic_diag.build(CLINIC_TEXT_CASE, meta=CLINIC_META)
    assert_shared_shape(env)
    assert env["signals"]["available"] is False
    assert env["signals"]["turns"] is None
    assert env["flow"]["available"] is False
    assert "agent-turn" in env["flow"]["reason"]
    assert env["provenance"]["seed"] == 7
    assert env["provenance"]["stratum"]["dataset"] == "MedQA"
    assert env["cost"]["judge_calls"] == 9


def test_clinic_voice_mode_captures_signals_and_names_the_flow_gap():
    env = clinic_diag.build(CLINIC_VOICE_CASE, meta={**CLINIC_META, "mode": "voice"})
    assert_shared_shape(env)
    assert env["signals"]["available"] is True
    assert env["signals"]["summary"]["hesitation_turns"] == [1]
    assert env["metadata_sidecar"]["summary"]["emotions_seen"] == {"worried": 1}
    # Bench voice runs the harness's doctor prompt, not the deployed agent's flow.
    # That is a real limitation and the record says which one it is.
    assert env["flow"]["available"] is False
    assert "real=false" in env["flow"]["reason"]


def test_clinic_tool_calls_pair_actions_with_their_results():
    env = clinic_diag.build(CLINIC_TEXT_CASE, meta=CLINIC_META)
    names = [c["name"] for c in env["tools"]["calls"]]
    assert names == ["request_test", "give_diagnosis"]
    test_call = env["tools"]["calls"][0]
    assert test_call["arguments"]["request"] == "CBC"
    assert test_call["result"] == "WBC 12.1"
    # A plain question is not a tool call and must not inflate the count.
    assert env["tools"]["summary"]["n_calls"] == 2


def test_clinic_voice_subset_slice_is_the_seeded_head():
    from tau2.health.agentclinic.run import voice_subset

    chosen = list(range(10))
    assert voice_subset(chosen, 3) == [0, 1, 2]
    assert voice_subset(chosen, 0) == []
    assert voice_subset(chosen, 99) == chosen


def test_clinic_voice_subset_cases_are_labelled_so_they_are_never_averaged_in():
    env = clinic_diag.build({**CLINIC_VOICE_CASE, "voice_subset": True},
                            meta={**CLINIC_META, "mode": "voice"})
    assert env["provenance"]["voice_subset"] is True
    assert env["mode"] == "voice"


# ── the consistency claim itself ────────────────────────────────────────────────

def test_all_three_benchmarks_emit_the_same_sections():
    """One reader, three benchmarks. If this fails, the shared report tool breaks."""
    envelopes = [
        pab_diag.build(case_id="c", mode="harness", transcript=TEXT_TRANSCRIPT,
                       raw_tool_calls=TEXT_TOOL_CALLS),
        med_diag.build(_med_result(said=False, emitted=1, landed=1)),
        clinic_diag.build(CLINIC_TEXT_CASE, meta=CLINIC_META),
    ]
    for env in envelopes:
        assert_shared_shape(env)
    shapes = [set(e["availability"]) for e in envelopes]
    assert shapes[0] == shapes[1] == shapes[2]
    assert {e["schema"] for e in envelopes} == {diag.SCHEMA}
