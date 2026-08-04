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
