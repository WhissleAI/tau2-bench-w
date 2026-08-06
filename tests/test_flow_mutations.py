# Copyright Sierra
"""Offline unit tests for the flow-edit sensitivity suite.

Covers the PURE half (``tau2.flow.mutations``: anchor resolution, mutation
generation, apply-purity, and every detection check against synthetic probe
captures) and the RUNNER half (``tau2.flow.mutation_suite.run_mutation``) against
a mock FlowClient that emulates a healthy draft/publish backend — plus a BROKEN
backend where a draft-target PATCH leaks straight to live, which the suite must
flag. No network anywhere.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tau2.flow.client import TurnResult
from tau2.flow.mutations import (
    GOAL_SENTINEL_PHRASE,
    MAGIC_WORD,
    PROBE_EXPR_EDGE,
    PROBE_SET_STATE,
    PROBE_VAR,
    PROBE_VAR_VALUE,
    SAY_SENTINEL,
    ProbeResult,
    build_mutations,
    resolve_anchors,
    voice_spot_subset,
)
from tau2.flow.mutation_suite import run_mutation

FIXTURE = Path(__file__).resolve().parent.parent / \
    "data" / "flow" / "headache_enrollment.flow.json"


@pytest.fixture()
def flow() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _by_id(muts):
    return {m.id: m for m in muts}


# ── anchor resolution ───────────────────────────────────────────────────────────

def test_anchors_headache(flow):
    a = resolve_anchors(flow)
    assert a.say_state == "greet"
    assert a.entry_edge == "t1"
    assert a.conv1 == "consent"
    assert a.advance_edge == "t3"
    assert a.conv2 == "about_you"
    assert a.conv2_forward == "headache_profile"
    assert a.close_state == "close"          # the shared goodbye, not `urgent`
    assert a.toolful_state == "about_you"
    assert a.toolful_tool == "save_contact_field"
    assert a.toolless_state == "consent"


# ── matrix generation ───────────────────────────────────────────────────────────

def test_full_matrix_generated(flow):
    muts, skips = build_mutations(flow)
    ids = set(_by_id(muts))
    assert ids == {
        "say_sentinel_greet",
        "conversation_goal_consent",
        "transition_condition_t3",
        "transition_retarget_t3",
        "tool_gate_remove_about_you",
        "tool_gate_add_consent",
        "state_remove_about_you",
        "set_variable_expression",
    }
    assert skips == []
    # One voice spot-check per step-kind group.
    spot_kinds = {m.kind for m in voice_spot_subset(muts)}
    assert spot_kinds == {"say", "conversation", "transition", "tool_gate",
                          "state_remove", "variable"}
    # Probes are short (2-6 turn probes, never full intakes).
    assert all(1 <= len(m.probe) <= 6 for m in muts)


def test_apply_is_pure_and_targeted(flow):
    before = copy.deepcopy(flow)
    muts, _ = build_mutations(flow)
    m = _by_id(muts)

    say = m["say_sentinel_greet"].apply(flow)
    assert flow == before, "apply must never modify the baseline"
    greet = next(s for s in say["states"] if s["id"] == "greet")
    assert greet["say"] == SAY_SENTINEL

    goal = m["conversation_goal_consent"].apply(flow)
    consent = next(s for s in goal["states"] if s["id"] == "consent")
    assert GOAL_SENTINEL_PHRASE in consent["goal"].lower()

    cond = m["transition_condition_t3"].apply(flow)
    t3 = next(t for t in cond["transitions"] if t["id"] == "t3")
    assert MAGIC_WORD in t3["condition"]
    assert t3["to"] == "about_you"           # condition edit does not retarget

    ret = m["transition_retarget_t3"].apply(flow)
    t3r = next(t for t in ret["transitions"] if t["id"] == "t3")
    assert t3r["to"] == "close"

    rm_tool = m["tool_gate_remove_about_you"].apply(flow)
    about = next(s for s in rm_tool["states"] if s["id"] == "about_you")
    assert about["allowed_tools"] == []       # explicit empty — gates to nothing

    add_tool = m["tool_gate_add_consent"].apply(flow)
    cons = next(s for s in add_tool["states"] if s["id"] == "consent")
    assert cons["allowed_tools"] == ["save_contact_field"]

    rm = m["state_remove_about_you"].apply(flow)
    ids = {s["id"] for s in rm["states"]}
    assert "about_you" not in ids
    assert all(t.get("from") != "about_you" and t.get("to") != "about_you"
               for t in rm["transitions"])
    t3rm = next(t for t in rm["transitions"] if t["id"] == "t3")
    assert t3rm["to"] == "headache_profile"   # inbound edge rewired forward

    var = m["set_variable_expression"].apply(flow)
    assert any(v["key"] == PROBE_VAR for v in var["variables"])
    setter = next(s for s in var["states"] if s["id"] == PROBE_SET_STATE)
    assert setter == {"id": PROBE_SET_STATE, "type": "set_variable",
                      "key": PROBE_VAR, "value": PROBE_VAR_VALUE}
    t1 = next(t for t in var["transitions"] if t["id"] == "t1")
    assert t1["to"] == PROBE_SET_STATE        # entry detours through the setter
    expr = next(t for t in var["transitions"] if t["id"] == PROBE_EXPR_EDGE)
    assert expr["kind"] == "expression" and expr["from"] == "consent" \
        and expr["to"] == "close"


# ── detection checks (synthetic probe captures) ────────────────────────────────

def _turn(n, reply, state=None, tools=None, ended=False, user="..."):
    return {"n": n, "user_msg": user, "reply": reply, "current_state": state,
            "tools_used": tools or [], "steps": [], "ended": ended}


def test_check_say_detects_sentinel(flow):
    m = _by_id(build_mutations(flow)[0])["say_sentinel_greet"]
    hit = ProbeResult(
        turns=[_turn(1, f"{SAY_SENTINEL} Ready to start?", "consent")],
        trace=[{"seq": 0, "kind": "say_emitted", "state": "greet",
                "text": SAY_SENTINEL}])
    assert m.check(hit).passed
    miss = ProbeResult(
        turns=[_turn(1, "Hi, I'm Ember. Ready to start?", "consent")],
        trace=[{"seq": 0, "kind": "say_emitted", "state": "greet",
                "text": "Hi, I'm Ember."}])
    assert not m.check(miss).passed
    # Voice: sentinel recovered only via bot-audio re-ASR still counts.
    reasr = ProbeResult(turns=[_turn(1, "")],
                        bot_reasr="sentinel alpha this greeting was updated")
    assert m.check(reasr).passed
    # Draft-phase inverse: sentinel must be ABSENT.
    assert m.draft_check(miss).passed
    assert not m.draft_check(hit).passed


def test_check_conversation_goal(flow):
    m = _by_id(build_mutations(flow)[0])["conversation_goal_consent"]
    assert m.check(ProbeResult(
        turns=[_turn(1, "Quick one: what is your favorite color?")])).passed
    assert not m.check(ProbeResult(
        turns=[_turn(1, "Are you ready to start the intake?")])).passed


def _tc(seq, tid, result, to=None, frm="consent"):
    d = {"seq": seq, "kind": "transition_check", "from": frm,
         "transition_id": tid, "transition_kind": "llm_condition",
         "result": result}
    if to:
        d["to"] = to
    return d


def test_check_transition_condition(flow):
    m = _by_id(build_mutations(flow)[0])["transition_condition_t3"]
    good = ProbeResult(
        turns=[_turn(1, "hi", "consent"), _turn(2, "great", "consent"),
               _turn(3, "thanks", "about_you")],
        trace=[_tc(0, "t3", "not_satisfied"),
               _tc(1, "t3", "fired", to="about_you"),
               {"seq": 2, "kind": "state_enter", "state": "about_you",
                "state_type": "conversation"}])
    assert m.check(good).passed
    # Runtime advanced on the plain "ready" turn → the tightened condition was
    # NOT picked up.
    leaked = ProbeResult(
        turns=[_turn(1, "hi", "consent"), _turn(2, "great", "about_you"),
               _turn(3, "thanks", "about_you")],
        trace=[_tc(0, "t3", "fired", to="about_you"),
               {"seq": 1, "kind": "state_enter", "state": "about_you",
                "state_type": "conversation"}])
    assert not m.check(leaked).passed
    # Voice degrade: no per-turn states — order of trace checks decides.
    voice_good = ProbeResult(
        turns=[_turn(1, "hi"), _turn(2, "great"), _turn(3, "thanks")],
        trace=good.trace)
    assert m.check(voice_good).passed


def test_check_transition_retarget(flow):
    m = _by_id(build_mutations(flow)[0])["transition_retarget_t3"]
    good = ProbeResult(turns=[_turn(1, "hi", "consent"),
                              _turn(2, "bye", "close", ended=True)],
                       trace=[_tc(0, "t3", "fired", to="close"),
                              {"seq": 1, "kind": "state_enter", "state": "close",
                               "state_type": "say"},
                              {"seq": 2, "kind": "flow_end", "state": "done",
                               "reason": "end_state"}])
    assert m.check(good).passed
    stale = ProbeResult(turns=[_turn(1, "hi", "consent"),
                               _turn(2, "ok", "about_you")],
                        trace=[_tc(0, "t3", "fired", to="about_you"),
                               {"seq": 1, "kind": "state_enter",
                                "state": "about_you",
                                "state_type": "conversation"}])
    assert not m.check(stale).passed


def test_check_tool_gate_remove(flow):
    m = _by_id(build_mutations(flow)[0])["tool_gate_remove_about_you"]
    good = ProbeResult(
        turns=[_turn(1, "a", "consent"), _turn(2, "b", "about_you"),
               _turn(3, "c", "about_you")],
        trace=[{"seq": 0, "kind": "state_enter", "state": "about_you",
                "state_type": "conversation"},
               {"seq": 1, "kind": "tools_gated", "state": "about_you",
                "allowed": []}])
    assert m.check(good).passed
    stale_gate = ProbeResult(
        turns=[_turn(1, "a", "consent"),
               _turn(2, "b", "about_you", tools=["save_contact_field"])],
        trace=[{"seq": 0, "kind": "state_enter", "state": "about_you",
                "state_type": "conversation"},
               {"seq": 1, "kind": "tools_gated", "state": "about_you",
                "allowed": ["save_contact_field"]}])
    assert not m.check(stale_gate).passed
    never_reached = ProbeResult(turns=[_turn(1, "a", "consent")], trace=[])
    res = m.check(never_reached)
    assert not res.passed and "inconclusive" in res.observed


def test_check_tool_gate_add(flow):
    m = _by_id(build_mutations(flow)[0])["tool_gate_add_consent"]
    assert m.check(ProbeResult(
        turns=[_turn(1, "a", "consent")],
        trace=[{"seq": 0, "kind": "tools_gated", "state": "consent",
                "allowed": ["save_contact_field"]}])).passed
    assert not m.check(ProbeResult(
        turns=[_turn(1, "a", "consent")],
        trace=[{"seq": 0, "kind": "tools_gated", "state": "consent",
                "allowed": []}])).passed


def test_check_state_remove(flow):
    m = _by_id(build_mutations(flow)[0])["state_remove_about_you"]
    good = ProbeResult(
        turns=[_turn(1, "a", "consent"), _turn(2, "b", "headache_profile")],
        trace=[{"seq": 0, "kind": "state_enter", "state": "consent",
                "state_type": "conversation"},
               {"seq": 1, "kind": "state_enter", "state": "headache_profile",
                "state_type": "conversation"}])
    assert m.check(good).passed
    stale = ProbeResult(
        turns=[_turn(1, "a", "consent"), _turn(2, "b", "about_you")],
        trace=[{"seq": 0, "kind": "state_enter", "state": "about_you",
                "state_type": "conversation"}])
    assert not m.check(stale).passed


def test_check_set_variable_expression(flow):
    m = _by_id(build_mutations(flow)[0])["set_variable_expression"]
    good = ProbeResult(
        turns=[_turn(1, "a", "consent"), _turn(2, "b", "close", ended=True)],
        trace=[{"seq": 0, "kind": "var_set", "key": PROBE_VAR,
                "value": PROBE_VAR_VALUE, "source": "entry_action"},
               {"seq": 1, "kind": "transition_check", "from": "consent",
                "transition_id": PROBE_EXPR_EDGE,
                "transition_kind": "expression", "result": "fired",
                "to": "close", "expr": f"{PROBE_VAR} == '{PROBE_VAR_VALUE}'"},
               {"seq": 2, "kind": "state_enter", "state": "close",
                "state_type": "say"}])
    assert m.check(good).passed
    no_var = ProbeResult(
        turns=[_turn(1, "a", "consent"), _turn(2, "b", "consent")], trace=[])
    assert not m.check(no_var).passed


# ── runner against a mock draft/publish backend ────────────────────────────────

class MockBackend:
    """A FlowClient stand-in emulating the studio contract: agents carry a LIVE
    flow + a DRAFT overlay; the conversation runtime reads ONLY the live flow.
    ``leak_draft_to_live=True`` models the bug class the suite exists to catch."""

    def __init__(self, default_flow: dict, leak_draft_to_live: bool = False):
        self.default_flow = default_flow
        self.leak = leak_draft_to_live
        self.agents: dict[str, dict] = {}
        self.deleted: list[str] = []
        self._n = 0

    # — lifecycle —
    def create_typed_agent(self, name, agent_type, system_prompt):
        self._n += 1
        aid = f"agent-{self._n}"
        self.agents[aid] = {"id": aid, "name": name,
                            "flow": copy.deepcopy(self.default_flow),
                            "draft": None}
        return {"id": aid}

    def get_agent(self, agent_id, include=None):
        a = self.agents[agent_id]
        out = {"id": a["id"], "flow": copy.deepcopy(a["flow"])}
        if include == "draft":
            out["draft"] = copy.deepcopy(a["draft"])
            out["has_draft"] = a["draft"] is not None
        return out

    def set_flow(self, agent_id, flow, *, target="live"):
        a = self.agents[agent_id]
        if target == "draft":
            a["draft"] = {"flow": copy.deepcopy(flow)}
            if self.leak:                      # the bug: draft writes go live
                a["flow"] = copy.deepcopy(flow)
        else:
            a["flow"] = copy.deepcopy(flow)
        return self.get_agent(agent_id)

    def publish(self, agent_id):
        a = self.agents[agent_id]
        assert a["draft"], "publish without draft"
        a["flow"] = copy.deepcopy(a["draft"]["flow"])
        a["draft"] = None
        return self.get_agent(agent_id)

    def validate_flow(self, agent_id, flow):
        return {"valid": True, "errors": [], "warnings": []}

    def delete_agent(self, agent_id, *, confirm=False):
        assert confirm, "suite must delete with confirm=true"
        self.deleted.append(agent_id)
        self.agents.pop(agent_id)

    # — conversation: replies speak the LIVE flow's entry say text —
    def turn(self, agent_id, message, conversation_id=None):
        live = self.agents[agent_id]["flow"]
        say = next((s for s in live["states"] if s.get("type") == "say"), {})
        text = say.get("say", "")
        return TurnResult(
            reply=text, conversation_id="conv-1", tools_used=[],
            flow={"steps": [{"seq": 0, "turn": 1, "kind": "say_emitted",
                             "state": say.get("id"), "text": text}],
                  "current_state": "consent"},
            raw={"ended": False})

    def get_trace(self, agent_id, conversation_id):
        live = self.agents[agent_id]["flow"]
        say = next((s for s in live["states"] if s.get("type") == "say"), {})
        return {"steps": [
            {"seq": 0, "turn": 1, "kind": "state_enter",
             "state": say.get("id"), "state_type": "say"},
            {"seq": 1, "turn": 1, "kind": "say_emitted",
             "state": say.get("id"), "text": say.get("say", "")},
        ]}


def _say_mutation(flow):
    return _by_id(build_mutations(flow)[0])["say_sentinel_greet"]


def test_runner_healthy_backend_passes(flow, tmp_path):
    client = MockBackend(flow)
    res = run_mutation(client, _say_mutation(flow), "headache_enrollment",
                       "prompt", mode="text", draft_behavior_probe=True,
                       out_dir=tmp_path)
    ph = res["phases"]
    assert ph["draft_staged"]["passed"]
    assert ph["live_unchanged_while_draft"]["passed"]
    assert ph["draft_behavior_inert"]["passed"]
    assert ph["published"]["passed"]
    assert ph["behavior"]["passed"]
    assert res["passed"]
    assert res["agent_deleted"] and len(client.deleted) == 2, \
        "BOTH throwaway agents (draft-probe + main) must ALWAYS be deleted"
    assert not client.agents, "no agent may linger"
    assert (tmp_path / f"{res['mutation']}_{res['ts']}.json").exists()


def test_runner_flags_draft_leak(flow, tmp_path):
    client = MockBackend(flow, leak_draft_to_live=True)
    res = run_mutation(client, _say_mutation(flow), "headache_enrollment",
                       "prompt", mode="text", draft_behavior_probe=True,
                       out_dir=tmp_path)
    ph = res["phases"]
    assert not ph["live_unchanged_while_draft"]["passed"]
    assert not ph["draft_behavior_inert"]["passed"]
    assert not res["passed"], "a draft leaking to live must FAIL the mutation"
    assert client.deleted, "cleanup must run even on failure"


def test_runner_detects_unpropagated_edit(flow, tmp_path):
    """A backend that stages + publishes correctly but whose RUNTIME keeps
    serving the OLD flow (the exact product bug the owner wants caught)."""

    class StaleRuntime(MockBackend):
        def turn(self, agent_id, message, conversation_id=None):
            stale = next(s for s in self.default_flow["states"]
                         if s.get("type") == "say")
            return TurnResult(
                reply=stale.get("say", ""), conversation_id="conv-1",
                tools_used=[],
                flow={"steps": [], "current_state": "consent"},
                raw={"ended": False})

        def get_trace(self, agent_id, conversation_id):
            return {"steps": []}

    client = StaleRuntime(flow)
    res = run_mutation(client, _say_mutation(flow), "headache_enrollment",
                       "prompt", mode="text", out_dir=tmp_path)
    ph = res["phases"]
    assert ph["published"]["passed"], "the API accepted and stored the edit"
    assert not ph["behavior"]["passed"], \
        "but the conversation never picked it up — must FAIL"
    assert not res["passed"]
