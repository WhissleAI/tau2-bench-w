# Copyright Sierra
"""Diagnostics regression: a bench turn must be joinable to the flow state it ran
in, and must report the tools that actually ran.

THE DEFECT. ``turns[].current_state`` / ``engine_turn`` / ``steps`` are read from
the per-turn transport response, which only the TEXT channel populated —
``voice_transport.py`` returned ``flow=None`` unconditionally, and ``tools_used=[]``
likewise. Across every recorded voice session all three flow fields are ``null`` on
all 156 turns, ``outcome.final_state`` is ``null``, and the goal-drift judge is
disabled outright; ``tools_used`` being empty on all 103 sessions produced a bogus
"44 sessions with zero tool calls" signal that had to be discarded after it wasted
an investigation. Both were artifacts of the harness, not of the agent.

Two independent fixes, both covered here:
  * LIVE — the transport reads the backend's ``flow-state`` and ``{kind:"tool"}``
    data-channel messages and puts them on the TurnResult.
  * BACKFILL — ``backfill_turn_states`` joins the end-of-session flow trace onto the
    turns, which works against ANY backend (including the one that recorded the
    existing sessions) because the trace already stamps every step with its turn.
"""
from __future__ import annotations

from tau2.flow.simulate import backfill_turn_states
from tau2.flow.voice_transport import _tool_names

from tests.test_flow_voice_transport import FakeProvider, make_vt


# ── the backfill join ───────────────────────────────────────────────────────────

def _trace():
    """A three-turn intake trace of the shape the engine really emits: the start
    state entered on turn 0, an advance on turn 2, nothing on turn 3."""
    return [
        {"seq": 0, "turn": 0, "kind": "state_enter", "state": "greet",
         "state_type": "say"},
        {"seq": 1, "turn": 0, "kind": "state_enter", "state": "consent",
         "state_type": "conversation"},
        {"seq": 2, "turn": 1, "kind": "transition_check", "from": "consent",
         "transition_id": "t3", "transition_kind": "llm_condition",
         "result": "not_satisfied", "reason": "no answer yet"},
        {"seq": 3, "turn": 2, "kind": "transition_check", "from": "consent",
         "transition_id": "t3", "transition_kind": "llm_condition",
         "result": "fired", "to": "about_you", "reason": "caller started answering"},
        {"seq": 4, "turn": 2, "kind": "state_enter", "state": "about_you",
         "state_type": "conversation"},
        {"seq": 5, "turn": 3, "kind": "var_set", "key": "main_reason",
         "value": "migraines", "source": "tool_result"},
    ]


def _turns(n=3):
    return [{"n": i, "user_msg": f"u{i}", "agent_reply": f"a{i}",
             "current_state": None, "engine_turn": None, "steps": None}
            for i in range(1, n + 1)]


def test_every_turn_gets_the_state_it_ran_in():
    turns = _turns()
    prov = backfill_turn_states(turns, _trace())

    assert [t["current_state"] for t in turns] == ["consent", "about_you", "about_you"]
    assert prov["joined_turns"] == 3
    assert prov["engine_turn_offset"] == 0


def test_a_turn_with_no_state_change_carries_the_previous_state():
    """Most turns fire no transition. Without carry-forward they would still be
    null — which is exactly as useless as before."""
    turns = _turns()
    backfill_turn_states(turns, _trace())
    assert turns[2]["current_state"] == "about_you"
    assert turns[2]["steps"], "turn 3 still gets its own steps (a var_set)"


def test_engine_turn_and_steps_are_populated():
    turns = _turns()
    backfill_turn_states(turns, _trace())
    assert [t["engine_turn"] for t in turns] == [1, 2, 3]
    assert [s["kind"] for s in turns[1]["steps"]] == ["transition_check", "state_enter"]


def test_turn_zero_steps_are_not_attributed_to_a_harness_turn():
    turns = _turns()
    backfill_turn_states(turns, _trace())
    assert all(s.get("turn") != 0 for t in turns for s in t["steps"])


def test_an_offset_engine_counter_still_aligns():
    """The controller advances its counter once per NEW caller utterance, so the
    first judged turn is not guaranteed to be engine turn 1."""
    trace = [dict(s, turn=(s["turn"] + 1 if s["turn"] else 0)) for s in _trace()]
    turns = _turns()
    prov = backfill_turn_states(turns, trace)
    assert prov["engine_turn_offset"] == 1
    assert [t["current_state"] for t in turns] == ["consent", "about_you", "about_you"]


def test_a_real_per_turn_value_is_never_overwritten():
    """Text turns (and voice against a backend that pushes flow-state) already carry
    the authoritative value — the backfill must not clobber it."""
    turns = _turns()
    turns[0]["current_state"] = "authoritative"
    turns[0]["steps"] = [{"seq": 99, "turn": 1, "kind": "state_enter",
                          "state": "authoritative"}]
    backfill_turn_states(turns, _trace())
    assert turns[0]["current_state"] == "authoritative"
    assert turns[0]["steps"][0]["seq"] == 99


def test_no_trace_degrades_honestly():
    turns = _turns()
    prov = backfill_turn_states(turns, [])
    assert all(t["current_state"] is None for t in turns)
    assert prov["joined_turns"] == 0 and prov["trace_steps"] == 0


def test_malformed_steps_never_raise():
    turns = _turns()
    backfill_turn_states(turns, [None, "nope", {"kind": "x"}, {"turn": "1"}])
    assert all(t["current_state"] is None for t in turns)


# ── tools actually used ─────────────────────────────────────────────────────────

def test_tool_names_prefers_results():
    events = [
        {"kind": "tool", "phase": "started", "function_name": "save_contact_field"},
        {"kind": "tool", "phase": "result", "function_name": "save_contact_field",
         "ok": True},
        {"kind": "tool", "phase": "result", "function_name": "book_appointment",
         "ok": True},
    ]
    assert _tool_names(events) == ["save_contact_field", "book_appointment"]


def test_tool_names_falls_back_to_started():
    """A result frame lost on the wire must not erase the fact that a tool ran."""
    events = [{"kind": "tool", "phase": "started", "function_name": "lookup_record"}]
    assert _tool_names(events) == ["lookup_record"]


def test_tool_names_is_empty_without_tool_frames():
    assert _tool_names([]) == []
    assert _tool_names([{"kind": "signal"}]) == []


# ── the live data-channel path ──────────────────────────────────────────────────

class ChannelProvider(FakeProvider):
    """FakeProvider that also serves scripted data-channel frames."""

    def __init__(self, frames):
        super().__init__()
        self._frames = list(frames)

    def events(self) -> list[dict]:
        return list(self._frames)


def _sm(data):
    return {"type": "server-message", "data": data}


def test_a_voice_turn_reports_the_tools_that_ran():
    """The regression the coordinator flagged: tools_used was hardcoded [], so a
    session with a known tool call reported none."""
    p = ChannelProvider([
        _sm({"kind": "tool", "phase": "started", "function_name": "save_contact_field"}),
        _sm({"kind": "tool", "phase": "result", "function_name": "save_contact_field",
             "ok": True}),
    ])
    vt = make_vt(p)
    try:
        _sigs, _meta, _flow, tools = vt._drain_channel()
    finally:
        vt._bg.stop()
    assert _tool_names(tools) == ["save_contact_field"]
    assert vt.tool_events == tools


def test_a_voice_turn_reports_its_flow_state():
    p = ChannelProvider([
        _sm({"t": "flow-state", "current_state": "about_you", "engine_turn": 4,
             "terminated": False,
             "steps": [{"seq": 9, "turn": 4, "kind": "state_enter",
                        "state": "about_you"}]}),
    ])
    vt = make_vt(p)
    try:
        _sigs, _meta, flow, _tools = vt._drain_channel()
    finally:
        vt._bg.stop()
    assert flow["current_state"] == "about_you"
    assert flow["engine_turn"] == 4
    assert [s["kind"] for s in flow["steps"]] == ["state_enter"]


def test_several_flow_state_frames_in_one_turn_keep_every_step():
    p = ChannelProvider([
        _sm({"t": "flow-state", "current_state": "consent", "engine_turn": 1,
             "steps": [{"seq": 1, "turn": 1, "kind": "transition_check"}]}),
        _sm({"t": "flow-state", "current_state": "about_you", "engine_turn": 1,
             "steps": [{"seq": 2, "turn": 1, "kind": "state_enter",
                        "state": "about_you"}]}),
    ])
    vt = make_vt(p)
    try:
        _sigs, _meta, flow, _tools = vt._drain_channel()
    finally:
        vt._bg.stop()
    assert flow["current_state"] == "about_you", "the LAST frame is the turn's state"
    assert len(flow["steps"]) == 2, "no step may be dropped"


def test_an_old_backend_pushes_nothing_and_degrades_to_none():
    vt = make_vt(FakeProvider())
    try:
        sigs, meta, flow, tools = vt._drain_channel()
    finally:
        vt._bg.stop()
    assert (sigs, meta, flow, tools) == ([], [], None, [])
