# Copyright Sierra
"""Regression tests for the flow rule-analyzer's two accuracy bugs:

  BUG 1  lowercase boolean literals (``true`` / ``false``) were parsed by Python's
         ``ast`` as undefined Name nodes, so every gate like ``x == true`` was
         mis-flagged as ``variable_desync`` even when ``x`` is a declared variable.

  BUG 2  the debt-collection compliance gate opened at the FIRST verify-state
         entry — but the START state was itself a verify state (``confirm_party``
         at seq ~0), so the gate read "verified" from turn 0 and the pre-disclosure
         check could never fire. The gate must open only when identity is ACTUALLY
         verified (the ``identity_verified`` var_set / ``mark_verified`` entry).
"""
from tau2.flow.analyze import (
    _referenced_names,
    analyze_session,
    eval_expr,
)

# ── BUG 1 — boolean literals ────────────────────────────────────────────────────


def test_true_false_are_not_referenced_variables():
    # The genuine variable is `identity_verified`; `true`/`false` are literals.
    assert _referenced_names("identity_verified == true") == {"identity_verified"}
    assert _referenced_names("done == false and ready == true") == {"done", "ready"}


def test_eval_expr_resolves_boolean_literals():
    truth, names = eval_expr("identity_verified == true", {"identity_verified": True})
    assert truth is True and names == {"identity_verified"}
    truth, _ = eval_expr("identity_verified == true", {"identity_verified": False})
    assert truth is False
    truth, _ = eval_expr("blocked == false", {"blocked": False})
    assert truth is True


def _flow_with_bool_gate():
    return {
        "start_state": "collect",
        "states": [
            {"id": "collect", "type": "collect"},
            {"id": "disclose", "type": "say", "say": "Your balance is X."},
            {"id": "done", "type": "end"},
        ],
        "transitions": [
            {"id": "t_gate", "from": "collect", "to": "disclose",
             "kind": "expression", "expr": "identity_verified == true"},
            {"id": "t_end", "from": "disclose", "to": "done", "kind": "always"},
        ],
        "variables": [{"key": "identity_verified", "initial": False}],
        "settings": {},
    }


def test_declared_bool_gate_does_not_desync():
    """A correct ``x == true`` gate where x IS declared must NOT desync-flag."""
    flow = _flow_with_bool_gate()
    steps = [
        {"seq": 0, "kind": "state_enter", "state": "collect"},
        {"seq": 1, "kind": "var_set", "key": "identity_verified", "value": True},
        {"seq": 2, "kind": "transition_check", "transition_id": "t_gate",
         "transition_kind": "expression", "expr": "identity_verified == true",
         "from": "collect", "result": "fired"},
        {"seq": 3, "kind": "state_enter", "state": "disclose"},
        {"seq": 4, "kind": "say_emitted", "state": "disclose",
         "text": "Your balance is X."},
        {"seq": 5, "kind": "state_enter", "state": "done"},
    ]
    findings = analyze_session(flow, steps)
    assert not [f for f in findings if f.type == "variable_desync"], \
        [f.as_dict() for f in findings if f.type == "variable_desync"]


def test_genuinely_undeclared_variable_still_desyncs():
    """Regression guard: a real phantom variable must still be flagged."""
    flow = _flow_with_bool_gate()
    flow["transitions"][0]["expr"] = "phantom_flag == true"
    findings = analyze_session(flow, [
        {"seq": 0, "kind": "state_enter", "state": "collect"},
    ])
    desync = [f for f in findings if f.type == "variable_desync"]
    assert len(desync) == 1
    assert "phantom_flag" in desync[0].detail


# ── BUG 2 — compliance gate must open only at real verification ──────────────────

_COMPLIANCE = {
    "gate_variable": "identity_verified",
    "verify_states": ["mark_verified"],
    "disclosure_states": ["disclose_balance"],
    "forbidden_substrings_lower": ["you owe", "balance is"],
}


def _debt_flow():
    return {
        "start_state": "confirm_party",
        "states": [
            {"id": "confirm_party", "type": "collect"},
            {"id": "mark_verified", "type": "action"},
            {"id": "disclose_balance", "type": "say"},
            {"id": "done", "type": "end"},
        ],
        "transitions": [],
        "variables": [{"key": "identity_verified", "initial": False}],
        "settings": {},
    }


def test_pre_verify_disclosure_is_flagged():
    """A balance disclosure BEFORE identity_verified is set must surface as a
    compliance finding — the exact class the turn-0 gate bug used to hide."""
    steps = [
        # confirm_party is the START state, entered at seq 0. Under the old bug the
        # gate opened here (it was listed in verify_states) and masked the leak.
        {"seq": 0, "kind": "state_enter", "state": "confirm_party"},
        {"seq": 1, "kind": "say_emitted", "state": "confirm_party",
         "text": "Hi, you owe a balance that is past due."},
        {"seq": 2, "kind": "state_enter", "state": "disclose_balance"},  # pre-verify
        {"seq": 3, "kind": "var_set", "key": "identity_verified", "value": True},
        {"seq": 4, "kind": "state_enter", "state": "mark_verified"},
    ]
    findings = analyze_session(
        _debt_flow(), steps, compliance=_COMPLIANCE,
        transcript_lower="hi, you owe a balance that is past due.")
    comp = [f for f in findings if f.type == "compliance"]
    # Expect both: the disclosure STATE entered pre-gate AND the forbidden substrings.
    kinds = " ".join(f.detail for f in comp)
    assert any("disclose_balance" in f.detail for f in comp), [f.as_dict() for f in comp]
    assert "you owe" in kinds
    assert all(f.severity == "high" for f in comp)


def test_post_verify_disclosure_is_clean():
    """The same disclosure AFTER verification must NOT be flagged."""
    steps = [
        {"seq": 0, "kind": "state_enter", "state": "confirm_party"},
        {"seq": 1, "kind": "var_set", "key": "identity_verified", "value": True},
        {"seq": 2, "kind": "state_enter", "state": "mark_verified"},
        {"seq": 3, "kind": "state_enter", "state": "disclose_balance"},
        {"seq": 4, "kind": "say_emitted", "state": "disclose_balance",
         "text": "Your balance is $200."},
    ]
    findings = analyze_session(
        _debt_flow(), steps, compliance=_COMPLIANCE,
        transcript_lower="your balance is $200.")
    assert not [f for f in findings if f.type == "compliance"], \
        [f.as_dict() for f in findings if f.type == "compliance"]


# ── BUG 3 — tool leaks must be judged against the LIVE (call-time) gate ──────────
#
# The engine emits a state's ``tools_gated`` when it ENTERS that state — which for
# the state the model spends turn N in lands at the tail of turn N-1. During turn N
# the model invokes a tool of that state, THEN a transition fires and the engine
# emits the NEXT state's gate under turn N's own number. Judging the tool against
# that post-transition (neighboring-state) gate is an off-by-one that flags every
# legitimate adjacent-state tool as a "leak". The analyzer must instead pin the tool
# to the gate that was live BEFORE turn N's boundary transition.

def _tool_flow():
    return {
        "start_state": "greet",
        "states": [
            {"id": "greet", "type": "collect"},
            {"id": "book", "type": "action"},
            {"id": "done", "type": "end"},
        ],
        "transitions": [
            {"id": "t_book", "from": "greet", "to": "book", "kind": "always"},
            {"id": "t_end", "from": "book", "to": "done", "kind": "always"},
        ],
        "variables": [],
        "settings": {},
    }


# The real-trace layout that produced the false leaks: the gate for the state the
# model is in (``greet`` → ``check_availability``) is emitted at the tail of the
# PRIOR turn (turn 1); the tool runs in turn 2; then the transition to ``book`` fires
# and ``book``'s gate (``create_booking``) is emitted under turn 2. Under the old
# per-turn union, turn 2's only gate is ``book``'s → the ``greet`` tool false-leaks.
def _adjacent_state_steps():
    return [
        {"seq": 0, "turn": 1, "kind": "state_enter", "state": "greet"},
        {"seq": 1, "turn": 1, "kind": "tools_gated", "state": "greet",
         "allowed": ["check_availability"]},
        # turn 2: model (still in greet) calls check_availability, THEN advances.
        {"seq": 2, "turn": 2, "kind": "transition_check", "transition_id": "t_book",
         "from": "greet", "result": "fired"},
        {"seq": 3, "turn": 2, "kind": "state_enter", "state": "book"},
        {"seq": 4, "turn": 2, "kind": "tools_gated", "state": "book",
         "allowed": ["create_booking"]},
    ]


def test_adjacent_state_tool_is_not_a_leak():
    """A tool of the state the model was actually in (whose gate was emitted the
    prior turn) must NOT leak, even though the flow advanced to a neighboring state
    within the same turn and that state's gate is the only one tagged with the turn."""
    findings = analyze_session(
        _tool_flow(), _adjacent_state_steps(),
        tools_used_by_turn={2: ["check_availability"]})
    leaks = [f for f in findings if f.type == "tool_leakage"]
    assert leaks == [], [f.as_dict() for f in leaks]


def test_genuine_out_of_gate_tool_still_leaks():
    """A tool offered by NEITHER the live gate nor any prior gate is a real leak."""
    findings = analyze_session(
        _tool_flow(), _adjacent_state_steps(),
        tools_used_by_turn={2: ["issue_refund"]})
    leaks = [f for f in findings if f.type == "tool_leakage"]
    assert len(leaks) == 1, [f.as_dict() for f in findings]
    assert "issue_refund" in leaks[0].detail
    # It is judged against the LIVE gate (greet's), not the advanced-into book gate.
    assert leaks[0].evidence["live_allowed"] == ["check_availability"]


def test_no_gate_before_call_is_not_judged():
    """If no tools_gated was ever in effect before the turn's boundary, the analyzer
    cannot judge the call and must stay silent (no false positive)."""
    steps = [
        {"seq": 0, "turn": 1, "kind": "transition_check", "transition_id": "t_book",
         "from": "greet", "result": "fired"},
        {"seq": 1, "turn": 1, "kind": "state_enter", "state": "book"},
        {"seq": 2, "turn": 1, "kind": "tools_gated", "state": "book",
         "allowed": ["create_booking"]},
    ]
    findings = analyze_session(
        _tool_flow(), steps, tools_used_by_turn={1: ["check_availability"]})
    assert not [f for f in findings if f.type == "tool_leakage"], \
        [f.as_dict() for f in findings if f.type == "tool_leakage"]


# ── F1 — termination-failure taxonomy ────────────────────────────────────────────
#
# A session that never reaches an end state must be CLASSIFIED, not lumped into one
# `stuck_termination` bucket, so the agent's own failure to close (goal met, sim
# cooperative, no flow_end — the confound that deflated "ended cleanly") is
# separable from a flow that is genuinely too long for its turn budget.

_TERM_TYPES = ("agent_no_close", "turn_cap_exceeded", "stuck_termination", "dead_end")


def _intake_flow():
    """A miniature intake flow with a proper closing terminal."""
    return {
        "start_state": "greet",
        "states": [
            {"id": "greet", "type": "collect"},
            {"id": "profile", "type": "collect"},
            {"id": "close", "type": "say", "say": "Thanks, goodbye!"},
            {"id": "done", "type": "end"},
        ],
        "transitions": [
            {"id": "t1", "from": "greet", "to": "profile", "kind": "always"},
            {"id": "t2", "from": "profile", "to": "close", "kind": "always"},
            {"id": "t3", "from": "close", "to": "done", "kind": "always"},
        ],
        "variables": [],
        "settings": {},
    }


def _steps_stuck_in_profile():
    """Synthetic transcript trace: the flow advanced greet → profile and stalled
    there — never entered `close`/`done`, no flow_end."""
    return [
        {"seq": 0, "kind": "state_enter", "state": "greet"},
        {"seq": 1, "kind": "transition_check", "transition_id": "t1",
         "from": "greet", "result": "fired"},
        {"seq": 2, "kind": "state_enter", "state": "profile"},
    ]


def _term(findings):
    return [f for f in findings if f.type in _TERM_TYPES]


def test_goal_met_cooperative_sim_is_agent_no_close():
    """Judge says goal met, the sim kept cooperating 4 post-goal turns, agent never
    closed → the AGENT's fault: exactly one `agent_no_close` (high), and neither
    `stuck_termination` nor `turn_cap_exceeded`."""
    findings = analyze_session(
        _intake_flow(), _steps_stuck_in_profile(),
        goal_met=True, sim_goal_met=True, post_goal_turns_driven=4,
        turn_cap_hit=False)
    term = _term(findings)
    assert [f.type for f in term] == ["agent_no_close"], [f.as_dict() for f in term]
    assert term[0].severity == "high"
    assert term[0].state == "profile"
    assert term[0].evidence["post_goal_turns_driven"] == 4


def test_empty_agent_reply_on_goal_turn_is_agent_no_close():
    """The hx_migraine_classic evidence class: the judge saw the goal met and the
    agent replied EMPTY on the goal-met turn (sim never even got a wrap-up to answer)
    — still the agent's fault, reported as `agent_no_close` with the empty turns in
    evidence."""
    findings = analyze_session(
        _intake_flow(), _steps_stuck_in_profile(),
        goal_met=True, sim_goal_met=False, post_goal_turns_driven=0,
        turn_cap_hit=False, empty_reply_turns=[7])
    term = _term(findings)
    assert [f.type for f in term] == ["agent_no_close"], [f.as_dict() for f in term]
    assert term[0].evidence["empty_reply_turns"] == [7]
    assert "EMPTY" in term[0].detail


def test_sim_goal_signal_alone_supports_agent_no_close():
    """When the LLM judge is unavailable (None) the sim's own [[GOAL_MET]] signal
    still supports the agent_no_close classification."""
    findings = analyze_session(
        _intake_flow(), _steps_stuck_in_profile(),
        goal_met=None, sim_goal_met=True, post_goal_turns_driven=3,
        turn_cap_hit=True)
    term = _term(findings)
    assert [f.type for f in term] == ["agent_no_close"], [f.as_dict() for f in term]


def test_cap_before_goal_is_turn_cap_exceeded():
    """Turn budget exhausted BEFORE the goal was met → the flow is genuinely too
    long: `turn_cap_exceeded` (medium), not agent_no_close, not stuck."""
    findings = analyze_session(
        _intake_flow(), _steps_stuck_in_profile(),
        goal_met=False, sim_goal_met=False, post_goal_turns_driven=0,
        turn_cap_hit=True)
    term = _term(findings)
    assert [f.type for f in term] == ["turn_cap_exceeded"], [f.as_dict() for f in term]
    assert term[0].severity == "medium"
    assert term[0].evidence["turn_cap_hit"] is True


def test_unknown_goal_no_cap_stays_stuck_termination():
    """Legacy/unclassifiable stall (goal unknown, no cap hit) keeps the
    backward-compatible `stuck_termination` type."""
    findings = analyze_session(
        _intake_flow(), _steps_stuck_in_profile(),
        goal_met=None, sim_goal_met=False, post_goal_turns_driven=0,
        turn_cap_hit=False)
    term = _term(findings)
    assert [f.type for f in term] == ["stuck_termination"], [f.as_dict() for f in term]


def test_goal_met_without_closing_chance_is_not_agent_no_close():
    """Goal met exactly on the final turn — the sim gave zero cooperative post-goal
    turns and the agent never replied empty, so the agent had no fair chance to
    close. Must NOT be blamed as agent_no_close (falls back to stuck_termination)."""
    findings = analyze_session(
        _intake_flow(), _steps_stuck_in_profile(),
        goal_met=True, sim_goal_met=True, post_goal_turns_driven=0,
        turn_cap_hit=True)
    term = _term(findings)
    assert [f.type for f in term] == ["stuck_termination"], [f.as_dict() for f in term]


def test_reached_flow_end_yields_no_termination_finding():
    """A session that reached the end state produces NO termination finding of any
    type — the drive-through-closing fix makes this the expected happy path."""
    steps = _steps_stuck_in_profile() + [
        {"seq": 3, "kind": "transition_check", "transition_id": "t2",
         "from": "profile", "result": "fired"},
        {"seq": 4, "kind": "state_enter", "state": "close"},
        {"seq": 5, "kind": "say_emitted", "state": "close", "text": "Thanks, goodbye!"},
        {"seq": 6, "kind": "state_enter", "state": "done"},
        {"seq": 7, "kind": "flow_end"},
    ]
    findings = analyze_session(
        _intake_flow(), steps, goal_met=True, sim_goal_met=True,
        post_goal_turns_driven=2, turn_cap_hit=False)
    assert _term(findings) == [], [f.as_dict() for f in _term(findings)]


def test_dead_end_still_wins_over_classification():
    """A final non-end state with NO outgoing transitions stays `dead_end` (a flow-
    authoring bug) regardless of goal/cooperation inputs."""
    flow = _intake_flow()
    flow["transitions"] = [t for t in flow["transitions"] if t["id"] != "t2"]
    findings = analyze_session(
        flow, _steps_stuck_in_profile(),
        goal_met=True, sim_goal_met=True, post_goal_turns_driven=4,
        turn_cap_hit=False)
    term = _term(findings)
    assert [f.type for f in term] == ["dead_end"], [f.as_dict() for f in term]
