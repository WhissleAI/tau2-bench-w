# Copyright Sierra
"""Unit tests for the flow user-simulator's call-ending protocol (F1).

The measurement confound: the sim used to hang up ([[END]]) the moment its goal was
met, so the agent's closing never got a response and the flow could not reach
flow_end — deflating "ended cleanly". The fix splits the signal in two:

  [[GOAL_MET]]  goal satisfied — call stays OPEN, sim keeps cooperating
  [[END]]       the call is actually over (agent closed / refused / abandoned)

These tests drive UserSimulator against a stub model (no network) and pin the
sentinel semantics, plus the per-task turn-budget resolution in Task.from_dict.
"""
import json
from pathlib import Path

from tau2.flow.usersim import (
    END_SENTINEL,
    GOAL_SENTINEL,
    Task,
    UserSimulator,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class StubModel:
    """Feeds scripted sim utterances; records the message lists it was given."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def chat(self, messages, **_):
        self.seen.append(messages)
        return self.replies.pop(0)


def _task(**over):
    base = dict(id="t", agent_type="dental_receptionist", persona="p", goal="g")
    base.update(over)
    return Task(**base)


# ── sentinel semantics ──────────────────────────────────────────────────────────

def test_goal_sentinel_keeps_call_open():
    """[[GOAL_MET]] marks the goal but must NOT end the call, and must be stripped
    from the utterance."""
    sim = UserSimulator(task=_task(), model=StubModel(
        [f"Great, that's all booked then. {GOAL_SENTINEL}"]))
    text = sim.first_utterance()
    assert sim.goal_met is True
    assert sim.done is False                       # the call is still open
    assert GOAL_SENTINEL not in text and text == "Great, that's all booked then."


def test_end_sentinel_ends_call():
    sim = UserSimulator(task=_task(), model=StubModel(
        [f"Thanks, bye! {END_SENTINEL}"]))
    text = sim.first_utterance()
    assert sim.done is True
    assert END_SENTINEL not in text and text == "Thanks, bye!"


def test_both_sentinels_strip_and_set_both_flags():
    """Returning the goodbye on the same utterance the goal completes: both flags
    set, both tokens stripped."""
    sim = UserSimulator(task=_task(), model=StubModel(
        [f"Perfect, goodbye! {GOAL_SENTINEL} {END_SENTINEL}"]))
    text = sim.first_utterance()
    assert sim.goal_met is True and sim.done is True
    assert text == "Perfect, goodbye!"


def test_goal_met_sim_keeps_answering_wrapup():
    """After goal-met the sim must keep producing utterances (the drive-through-
    closing behavior) — next_utterance still works and only [[END]] finishes."""
    sim = UserSimulator(task=_task(), model=StubModel([
        f"Booked, thanks. {GOAL_SENTINEL}",
        "No, that's everything, thanks.",
        f"Goodbye! {END_SENTINEL}",
    ]))
    sim.first_utterance()
    assert sim.goal_met and not sim.done
    t2 = sim.next_utterance("Is there anything else I can help you with?")
    assert t2 == "No, that's everything, thanks." and not sim.done
    sim.next_utterance("Thanks for calling, goodbye!")
    assert sim.done is True


def test_empty_agent_reply_surfaces_as_silence():
    """An EMPTY agent reply (the agent stalled) must not be forwarded as empty
    content — the sim is told about the silence so it can prompt instead of hang."""
    model = StubModel(["Hello, I'd like to book.", "Hello? Are you still there?"])
    sim = UserSimulator(task=_task(), model=model)
    sim.first_utterance()
    sim.next_utterance("")                          # agent replied EMPTY
    incoming = model.seen[-1][-1]                   # the mapped "agent" message
    assert incoming["role"] == "user"
    assert "silence" in incoming["content"].lower()


def test_system_prompt_carries_both_sentinels_and_closing_rules():
    sim = UserSimulator(task=_task(), model=StubModel([]))
    sysmsg = sim._system()["content"]
    assert GOAL_SENTINEL in sysmsg and END_SENTINEL in sysmsg
    assert "do NOT go silent" in sysmsg             # drive-through-closing rule


# ── per-task turn budgets ───────────────────────────────────────────────────────

def test_task_budget_resolution_most_specific_wins():
    defaults = {"max_turns": 18, "post_goal_turns": 4}
    t = Task.from_dict("dental_receptionist",
                       {"id": "x", "persona": "p", "goal": "g", "max_turns": 10,
                        "post_goal_turns": 2}, defaults)
    assert t.max_turns == 10 and t.post_goal_turns == 2
    t = Task.from_dict("dental_receptionist",
                       {"id": "x", "persona": "p", "goal": "g"}, defaults)
    assert t.max_turns == 18 and t.post_goal_turns == 4
    t = Task.from_dict("dental_receptionist",
                       {"id": "x", "persona": "p", "goal": "g"}, {})
    assert t.max_turns == 14 and t.post_goal_turns == 4


def test_fixture_declares_per_flow_budgets():
    """The shipped fixture sizes budgets to flow length: headache_enrollment (10-state
    spoken intake) > dental_receptionist, with scenario-level overrides, resolved the
    same way load_tasks resolves them (task > type block > top level)."""
    d = json.loads((REPO_ROOT / "data/flow/sim_tasks.json").read_text())

    def resolved(agent_type, task_id):
        block = d["types"][agent_type]
        defaults = {"max_turns": block.get("max_turns", d.get("max_turns", 24)),
                    "post_goal_turns": block.get("post_goal_turns",
                                                 d.get("post_goal_turns", 4))}
        task = next(t for t in block["tasks"] if t["id"] == task_id)
        return Task.from_dict(agent_type, task, defaults)

    assert d["post_goal_turns"] == 4
    assert resolved("headache_enrollment", "hx_happy_full").max_turns == 28
    assert resolved("headache_enrollment", "hx_keep_it_quick").max_turns == 18
    assert resolved("headache_enrollment", "hx_red_flag_urgent").max_turns == 14
    assert resolved("dental_receptionist", "dental_happy_book").max_turns == 18
    assert resolved("dental_receptionist", "dental_hours_only").max_turns == 10
    # Types with no block-level budget fall back to the top-level default.
    assert resolved("customer_support", "cs_resolvable_login").max_turns == 24
    # The allowance flows through everywhere.
    assert resolved("headache_enrollment", "hx_happy_full").post_goal_turns == 4
