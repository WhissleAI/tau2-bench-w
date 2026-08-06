# Copyright Sierra
"""Flow-edit sensitivity mutations — PURE generation + assertion logic.

Given an agent's DECLARED flow spec (the JSON the studio edits and the runtime
executes), this module generates a **mutation matrix**: one targeted, minimal edit
per step-kind of the flow contract, each paired with a short scripted PROBE
conversation and a deterministic CHECK that decides whether the live conversation
actually picked the edit up.

The point of the suite (see ``mutation_suite.py`` for the runner) is to prove the
STUDIO EDIT → PUBLISH → RUNTIME chain end-to-end: an edit made via the same API the
flow-designer UI uses (``PATCH /api/agents/{id}`` — optionally staged with
``?target=draft`` and promoted with ``POST /publish``) must manifest in the actual
conversation. Each mutation therefore plants an unambiguous SENTINEL — a phrase, a
magic word, a routing change, a tool-gate change — that the probe can detect in the
transcript and/or the flow step-trace.

Mutation kinds (one generator per step-kind of the contract):

  say            replace a ``say`` state's text with a sentinel phrase
                 → the sentinel must appear in the agent's reply / say_emitted.
  conversation   replace a ``conversation`` state's goal with "ask for their
                 favorite color" → the agent must ask it.
  transition     (a) tighten an ``llm_condition`` to fire only on a magic word
                 → routing must NOT advance on a normal "yes, ready", and MUST
                 advance on the magic word; (b) retarget the edge → the flow must
                 route to the new target and never enter the old one.
  tool_gate      (a) empty a state's ``allowed_tools`` → the gate must exclude the
                 tool and it must never be invoked; (b) add a tool to a bare state
                 → the gate must include it.
  state_remove   delete a mid-flow state and rewire its inbound edges to its
                 forward target → the session must skip it.
  variable       insert a ``set_variable`` state + a declared variable + an
                 ``expression`` transition keyed on it → the var_set must appear
                 in the trace and the expression edge must fire.

Everything here is pure and I/O-free (the runner owns HTTP + reporting), so the
generators and checks are unit-testable against a fixture flow — see
``tests/test_flow_mutations.py``.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# ── sentinels (unambiguous, never plausibly present in a real flow) ─────────────

SAY_SENTINEL = "SENTINEL ALPHA — this greeting was updated by the flow mutation suite."
SAY_SENTINEL_LOOSE = "sentinel alpha"     # fuzzy form for voice re-ASR matching
GOAL_SENTINEL_PHRASE = "favorite color"
GOAL_SENTINEL_TEXT = (
    "Ask the caller exactly one short question: 'What is your favorite color?' "
    "Nothing else — do not ask about anything besides their favorite color. "
    "After they answer, thank them and repeat the color back."
)
MAGIC_WORD = "pineapple"
MAGIC_CONDITION = (
    "the caller's most recent message contains the exact word 'pineapple'. Fire "
    "ONLY when the word pineapple was actually said; a caller merely agreeing, "
    "saying yes, or saying they are ready must NOT fire this transition."
)
PROBE_VAR = "mutation_probe"
PROBE_VAR_VALUE = "armed"
PROBE_SET_STATE = "mut_set_probe"
PROBE_EXPR_EDGE = "mut_t_expr"
PROBE_SET_EDGE = "mut_t_after_set"

# ── default scripted probe lines (overridable per agent type) ───────────────────

DEFAULT_PROBE_LINES = {
    # Opens the call on the text channel (the user speaks first there).
    "opener": "Hello?",
    # Satisfies a generic "ready to start" advance condition.
    "ready": "Yes, I'm ready to start. Let's begin.",
    # A detail-rich answer for a data-collection state (drives tool usage).
    "detail": ("Sure — the main reason I'm reaching out is my recurring headaches. "
               "I was born on March 3rd, 1990, and I'm female."),
    # Neutral cooperative filler.
    "ack": "Okay.",
}


# ── probe capture + check outcome ───────────────────────────────────────────────

@dataclass
class ProbeResult:
    """What one scripted probe conversation captured, transport-agnostic.

    ``turns`` are per-turn records ``{n, user_msg, reply, current_state, tools_used,
    steps, ended}`` — over voice ``current_state``/``steps`` are absent (None/[])
    and the accumulated ``trace`` (fetched post-session) is the authority.
    ``greeting`` is the agent's opening line (voice answers first). ``bot_reasr``
    is the independent re-ASR of the captured bot audio (voice only).
    """

    turns: list[dict[str, Any]] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    greeting: str = ""
    bot_reasr: Optional[str] = None

    @property
    def agent_text(self) -> str:
        """Everything the agent said, greeting first (transcript surface)."""
        parts = [self.greeting] if self.greeting else []
        parts += [(t.get("reply") or "") for t in self.turns]
        return "\n".join(parts)

    @property
    def states_entered(self) -> list[str]:
        return [s.get("state") for s in self.trace if s.get("kind") == "state_enter"]

    def state_after_turn(self, n: int) -> Optional[str]:
        """current_state reported after probe turn ``n`` (1-based; text only)."""
        for t in self.turns:
            if t.get("n") == n:
                return t.get("current_state")
        return None

    @property
    def all_tools_used(self) -> list[str]:
        out: list[str] = []
        for t in self.turns:
            out.extend(t.get("tools_used") or [])
        return out

    @property
    def ended(self) -> bool:
        return any(t.get("ended") for t in self.turns) or any(
            s.get("kind") == "flow_end" for s in self.trace)


@dataclass
class CheckResult:
    """One assertion verdict: what was expected vs what was actually observed."""

    passed: bool
    expected: str
    observed: str


# ── trace helpers (shared by checks; pure) ──────────────────────────────────────

def _norm(s: str) -> str:
    """Lowercase and strip everything but word characters — the fuzzy form used to
    find a sentinel in re-ASR output (ASR drops punctuation/dashes/case)."""
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())


def transition_checks(trace: list[dict], transition_id: str) -> list[dict]:
    return [s for s in trace
            if s.get("kind") == "transition_check"
            and s.get("transition_id") == transition_id]


def tools_gated_steps(trace: list[dict], state: str) -> list[dict]:
    return [s for s in trace
            if s.get("kind") == "tools_gated" and s.get("state") == state]


def var_set_steps(trace: list[dict], key: str) -> list[dict]:
    return [s for s in trace if s.get("kind") == "var_set" and s.get("key") == key]


def say_emitted_texts(trace: list[dict]) -> list[str]:
    return [s.get("text") or "" for s in trace if s.get("kind") == "say_emitted"]


# ── the mutation record ─────────────────────────────────────────────────────────

@dataclass
class Mutation:
    """One targeted flow edit + its probe script + its detection check.

    ``apply`` is pure: baseline flow → NEW mutated flow (deep-copied, baseline
    untouched). ``check`` judges a published-probe capture; ``draft_check`` (the
    inverse — the sentinel must be ABSENT) judges a draft-phase probe, proving a
    staged-but-unpublished edit does NOT reach the live conversation.
    """

    id: str
    kind: str
    target: str                       # state / transition id the edit touches
    description: str
    expected_signal: str              # human-readable expected observable
    probe: list[str]                  # scripted user lines, in order
    apply: Callable[[dict], dict]
    check: Callable[[ProbeResult], CheckResult]
    draft_probe: Optional[list[str]] = None   # short probe for draft inertness
    draft_check: Optional[Callable[[ProbeResult], CheckResult]] = None
    voice_spot: bool = False          # in the one-per-kind voice spot-check subset


@dataclass
class Skip:
    """A mutation kind that could not be generated for this flow, with the reason
    (reported, never silently dropped)."""

    kind: str
    reason: str


# ── flow anchor resolution (which states/edges to mutate) ───────────────────────

def _states_by_id(flow: dict) -> dict[str, dict]:
    return {s.get("id"): s for s in (flow.get("states") or [])
            if isinstance(s, dict)}

def _out_edges(flow: dict, state_id: str) -> list[dict]:
    edges = [t for t in (flow.get("transitions") or [])
             if isinstance(t, dict) and t.get("from") == state_id]
    return sorted(edges, key=lambda t: t.get("priority", 10**9))

def _in_edges(flow: dict, state_id: str) -> list[dict]:
    return [t for t in (flow.get("transitions") or [])
            if isinstance(t, dict) and t.get("to") == state_id]


@dataclass
class FlowAnchors:
    """The concrete states/edges each mutation kind latches onto, resolved once by
    walking the flow from its start state."""

    say_state: Optional[str] = None          # first say on the entry path
    conv1: Optional[str] = None              # first conversation state
    advance_edge: Optional[str] = None       # llm_condition conv1 → conv2
    conv2: Optional[str] = None              # its target (second conversation state)
    conv2_forward: Optional[str] = None      # conv2's own forward conversation target
    close_state: Optional[str] = None        # shared closing say (→ end)
    toolful_state: Optional[str] = None      # first conversation state WITH tools
    toolful_tool: Optional[str] = None       # a tool allowed there
    toolless_state: Optional[str] = None     # first conversation state with NO tools
    entry_edge: Optional[str] = None         # the always-edge out of say_state


def resolve_anchors(flow: dict) -> FlowAnchors:
    a = FlowAnchors()
    states = _states_by_id(flow)
    start = flow.get("start_state")

    # Walk auto-advancing entry chain (say / set_variable) to the first
    # conversation state, noting the entry say state + its always-edge.
    seen: set[str] = set()
    cur = start
    while cur in states and cur not in seen:
        seen.add(cur)
        st = states[cur]
        stype = st.get("type")
        if stype == "say" and a.say_state is None:
            a.say_state = cur
        if stype == "conversation":
            a.conv1 = cur
            break
        outs = _out_edges(flow, cur)
        always = [t for t in outs if t.get("kind") == "always"]
        if a.say_state == cur and always:
            a.entry_edge = always[0].get("id")
        cur = (always[0].get("to") if always else
               (outs[0].get("to") if outs else None))

    if a.say_state is None:  # no say on the entry path — any say state at all
        for sid, st in states.items():
            if st.get("type") == "say":
                a.say_state = sid
                break

    # The advance edge: highest-priority llm_condition out of conv1 whose target
    # is another conversation state.
    if a.conv1:
        for t in _out_edges(flow, a.conv1):
            if (t.get("kind") == "llm_condition"
                    and states.get(t.get("to"), {}).get("type") == "conversation"):
                a.advance_edge = t.get("id")
                a.conv2 = t.get("to")
                break

    # conv2's forward conversation target (for state_remove rewiring).
    if a.conv2:
        for t in _out_edges(flow, a.conv2):
            if (t.get("kind") == "llm_condition"
                    and states.get(t.get("to"), {}).get("type") == "conversation"
                    and t.get("to") != a.conv1):
                a.conv2_forward = t.get("to")
                break

    # The shared closing say state: a say with an edge to an `end` state; when
    # several exist, the most-referenced one (the flow's common goodbye).
    candidates: list[tuple[int, str]] = []
    for sid, st in states.items():
        if st.get("type") != "say":
            continue
        outs = _out_edges(flow, sid)
        if any(states.get(t.get("to"), {}).get("type") == "end" for t in outs):
            candidates.append((len(_in_edges(flow, sid)), sid))
    if candidates:
        a.close_state = max(candidates)[1]

    # Tool anchors, walked in declared state order for determinism.
    for st in flow.get("states") or []:
        if st.get("type") != "conversation":
            continue
        tools = st.get("allowed_tools") or []
        if tools and a.toolful_state is None:
            a.toolful_state = st.get("id")
            a.toolful_tool = tools[0]
        if not tools and a.toolless_state is None:
            a.toolless_state = st.get("id")
    return a


# ── mutation builders ───────────────────────────────────────────────────────────

def build_mutations(
    flow: dict,
    probe_lines: Optional[dict[str, str]] = None,
) -> tuple[list[Mutation], list[Skip]]:
    """The full mutation matrix for one declared flow. Pure; the baseline flow is
    never modified (every ``apply`` deep-copies). Kinds that don't apply to this
    flow's shape come back as :class:`Skip` records with the reason."""
    lines = {**DEFAULT_PROBE_LINES, **(probe_lines or {})}
    a = resolve_anchors(flow)
    muts: list[Mutation] = []
    skips: list[Skip] = []

    opener, ready, detail, ack = (lines["opener"], lines["ready"],
                                  lines["detail"], lines["ack"])

    # 1 ── say sentinel ─────────────────────────────────────────────────────────
    if a.say_state:
        sid = a.say_state

        def apply_say(f: dict, _sid=sid) -> dict:
            f = copy.deepcopy(f)
            _states_by_id(f)[_sid]["say"] = SAY_SENTINEL
            return f

        def check_say(pr: ProbeResult, _sid=sid) -> CheckResult:
            in_transcript = SAY_SENTINEL_LOOSE in _norm(pr.agent_text)
            in_trace = any(SAY_SENTINEL_LOOSE in _norm(t)
                           for t in say_emitted_texts(pr.trace))
            in_reasr = (pr.bot_reasr is not None
                        and SAY_SENTINEL_LOOSE in _norm(pr.bot_reasr))
            where = [w for w, hit in (("transcript", in_transcript),
                                      ("say_emitted trace", in_trace),
                                      ("bot audio re-ASR", in_reasr)) if hit]
            return CheckResult(
                passed=bool(where),
                expected=f"sentinel phrase {SAY_SENTINEL!r} spoken by the agent",
                observed=("sentinel found in: " + ", ".join(where)) if where
                else "sentinel NOT found in transcript, trace, or re-ASR",
            )

        def draft_check_say(pr: ProbeResult) -> CheckResult:
            present = SAY_SENTINEL_LOOSE in _norm(pr.agent_text)
            return CheckResult(
                passed=not present,
                expected="draft-only sentinel ABSENT from the live conversation",
                observed=("sentinel LEAKED into the live conversation before "
                          "publish" if present else "sentinel absent (draft inert)"),
            )

        muts.append(Mutation(
            id=f"say_sentinel_{sid}", kind="say", target=sid,
            description=f"replace say text of state '{sid}' with a sentinel phrase",
            expected_signal="sentinel phrase appears in the agent's opening reply "
                            "(and in voice, in the bot audio re-ASR)",
            probe=[opener], apply=apply_say, check=check_say,
            draft_probe=[opener], draft_check=draft_check_say, voice_spot=True))
    else:
        skips.append(Skip("say", "flow has no say state"))

    # 2 ── conversation goal sentinel ───────────────────────────────────────────
    if a.conv1:
        sid = a.conv1

        def apply_goal(f: dict, _sid=sid) -> dict:
            f = copy.deepcopy(f)
            _states_by_id(f)[_sid]["goal"] = GOAL_SENTINEL_TEXT
            return f

        def check_goal(pr: ProbeResult) -> CheckResult:
            asked = GOAL_SENTINEL_PHRASE in _norm(pr.agent_text)
            asked_reasr = (pr.bot_reasr is not None
                           and GOAL_SENTINEL_PHRASE in _norm(pr.bot_reasr))
            return CheckResult(
                passed=asked or asked_reasr,
                expected="the agent asks for the caller's favorite color "
                         "(sentinel goal)",
                observed="agent asked for the favorite color" if (asked or asked_reasr)
                else "agent never mentioned the favorite color",
            )

        def draft_check_goal(pr: ProbeResult) -> CheckResult:
            present = GOAL_SENTINEL_PHRASE in _norm(pr.agent_text)
            return CheckResult(
                passed=not present,
                expected="draft-only goal edit ABSENT from the live conversation",
                observed="sentinel goal LEAKED before publish" if present
                else "sentinel goal absent (draft inert)",
            )

        muts.append(Mutation(
            id=f"conversation_goal_{sid}", kind="conversation", target=sid,
            description=f"replace the goal of conversation state '{sid}' with a "
                        f"sentinel datum request (favorite color)",
            expected_signal="the agent asks for the caller's favorite color",
            probe=[opener, "Hmm, why do you ask? Okay — go ahead."],
            apply=apply_goal, check=check_goal,
            draft_probe=[opener, ack], draft_check=draft_check_goal,
            voice_spot=True))
    else:
        skips.append(Skip("conversation", "flow has no conversation state"))

    # 3 ── transition condition tightened to a magic word ───────────────────────
    if a.advance_edge and a.conv1 and a.conv2:
        tid, s_from, s_to = a.advance_edge, a.conv1, a.conv2

        def apply_cond(f: dict, _tid=tid) -> dict:
            f = copy.deepcopy(f)
            for t in f.get("transitions") or []:
                if t.get("id") == _tid:
                    t["condition"] = MAGIC_CONDITION
            return f

        def check_cond(pr: ProbeResult, _tid=tid, _from=s_from,
                       _to=s_to) -> CheckResult:
            checks = transition_checks(pr.trace, _tid)
            fired = [c for c in checks if c.get("result") == "fired"]
            held = [c for c in checks if c.get("result") == "not_satisfied"]
            # Strong per-turn form (text): still in `from` after the READY turn,
            # advanced only after the magic word.
            ready_state = pr.state_after_turn(2)
            held_on_ready = (ready_state == _from) if ready_state is not None else \
                bool(held and fired and min(s.get("seq", 0) for s in held)
                     < min(s.get("seq", 0) for s in fired))
            advanced = bool(fired) and _to in pr.states_entered
            return CheckResult(
                passed=held_on_ready and advanced,
                expected=f"edge '{_tid}' must NOT fire on a plain 'ready' turn and "
                         f"MUST fire on the magic word '{MAGIC_WORD}'",
                observed=f"held_on_ready={held_on_ready} (state after ready turn: "
                         f"{ready_state!r}), fired_after_magic_word={advanced} "
                         f"(checks: {len(held)} not_satisfied, {len(fired)} fired)",
            )

        muts.append(Mutation(
            id=f"transition_condition_{tid}", kind="transition", target=tid,
            description=f"tighten llm_condition '{tid}' ({s_from}→{s_to}) to fire "
                        f"only on the magic word '{MAGIC_WORD}'",
            expected_signal="routing holds on a normal 'ready' and advances only "
                            "on the magic word",
            probe=[opener, ready, f"{MAGIC_WORD.capitalize()}."],
            apply=apply_cond, check=check_cond, voice_spot=True))
    else:
        skips.append(Skip("transition",
                          "no llm_condition edge between conversation states"))

    # 4 ── transition retarget (routing must change) ────────────────────────────
    if a.advance_edge and a.conv2 and a.close_state and a.close_state != a.conv2:
        tid, old_to, new_to = a.advance_edge, a.conv2, a.close_state

        def apply_retarget(f: dict, _tid=tid, _new=new_to) -> dict:
            f = copy.deepcopy(f)
            for t in f.get("transitions") or []:
                if t.get("id") == _tid:
                    t["to"] = _new
            return f

        def check_retarget(pr: ProbeResult, _old=old_to, _new=new_to,
                           _tid=tid) -> CheckResult:
            entered_new = _new in pr.states_entered
            skipped_old = _old not in pr.states_entered
            fired_to = [c.get("to") for c in transition_checks(pr.trace, _tid)
                        if c.get("result") == "fired"]
            return CheckResult(
                passed=entered_new and skipped_old,
                expected=f"edge '{_tid}' routes to '{_new}' (never '{_old}')",
                observed=f"entered {_new}={entered_new}, skipped {_old}="
                         f"{skipped_old}, edge fired to={fired_to}, ended={pr.ended}",
            )

        muts.append(Mutation(
            id=f"transition_retarget_{tid}", kind="transition", target=tid,
            description=f"retarget edge '{tid}' from '{old_to}' to '{new_to}'",
            expected_signal=f"after the ready turn the flow enters '{new_to}' and "
                            f"never enters '{old_to}'",
            probe=[opener, ready, ack],
            apply=apply_retarget, check=check_retarget))
    else:
        skips.append(Skip("transition_retarget",
                          "no distinct closing state to retarget onto"))

    # 5 ── tool gate: remove ────────────────────────────────────────────────────
    if a.toolful_state and a.toolful_tool:
        sid, tool = a.toolful_state, a.toolful_tool

        def apply_tool_rm(f: dict, _sid=sid) -> dict:
            f = copy.deepcopy(f)
            _states_by_id(f)[_sid]["allowed_tools"] = []
            return f

        def check_tool_rm(pr: ProbeResult, _sid=sid, _tool=tool) -> CheckResult:
            gates = tools_gated_steps(pr.trace, _sid)
            entered = _sid in pr.states_entered
            gate_clean = bool(gates) and all(
                _tool not in (g.get("allowed") or []) for g in gates)
            not_invoked = _tool not in pr.all_tools_used
            if not entered:
                return CheckResult(
                    passed=False,
                    expected=f"state '{_sid}' gates out tool '{_tool}'",
                    observed=f"probe never reached state '{_sid}' — inconclusive",
                )
            return CheckResult(
                passed=gate_clean and not_invoked,
                expected=f"tools_gated for '{_sid}' excludes '{_tool}' and it is "
                         f"never invoked",
                observed=f"gate_excludes_tool={gate_clean} "
                         f"(gates: {[g.get('allowed') for g in gates]}), "
                         f"never_invoked={not_invoked} "
                         f"(tools_used: {pr.all_tools_used})",
            )

        muts.append(Mutation(
            id=f"tool_gate_remove_{sid}", kind="tool_gate", target=sid,
            description=f"empty allowed_tools of state '{sid}' "
                        f"(was allowing '{tool}')",
            expected_signal=f"the '{sid}' gate excludes '{tool}' and the tool is "
                            f"never invoked there",
            probe=[opener, ready, detail],
            apply=apply_tool_rm, check=check_tool_rm, voice_spot=True))
    else:
        skips.append(Skip("tool_gate", "no conversation state with allowed_tools"))

    # 6 ── tool gate: add ───────────────────────────────────────────────────────
    if a.toolless_state and a.toolful_tool:
        sid, tool = a.toolless_state, a.toolful_tool

        def apply_tool_add(f: dict, _sid=sid, _tool=tool) -> dict:
            f = copy.deepcopy(f)
            _states_by_id(f)[_sid]["allowed_tools"] = [_tool]
            return f

        def check_tool_add(pr: ProbeResult, _sid=sid, _tool=tool) -> CheckResult:
            gates = tools_gated_steps(pr.trace, _sid)
            opened = any(_tool in (g.get("allowed") or []) for g in gates)
            return CheckResult(
                passed=opened,
                expected=f"tools_gated for '{_sid}' includes '{_tool}'",
                observed=f"gates seen for '{_sid}': "
                         f"{[g.get('allowed') for g in gates] or 'none'}",
            )

        muts.append(Mutation(
            id=f"tool_gate_add_{sid}", kind="tool_gate", target=sid,
            description=f"add tool '{tool}' to previously tool-less state '{sid}'",
            expected_signal=f"the '{sid}' gate now admits '{tool}'",
            probe=[opener, ready],
            apply=apply_tool_add, check=check_tool_add))
    else:
        skips.append(Skip("tool_gate_add",
                          "no tool-less conversation state (or no tool to add)"))

    # 7 ── state removal (mid-stage skipped) ────────────────────────────────────
    if a.conv2 and a.conv2_forward and a.advance_edge:
        gone, fwd = a.conv2, a.conv2_forward

        def apply_rm(f: dict, _gone=gone, _fwd=fwd) -> dict:
            f = copy.deepcopy(f)
            f["states"] = [s for s in f.get("states") or []
                           if s.get("id") != _gone]
            rewired = []
            for t in f.get("transitions") or []:
                if t.get("from") == _gone:
                    continue                      # drop its outbound edges
                if t.get("to") == _gone:
                    t = {**t, "to": _fwd}         # rewire inbound to the forward hop
                rewired.append(t)
            f["transitions"] = rewired
            return f

        def check_rm(pr: ProbeResult, _gone=gone, _fwd=fwd) -> CheckResult:
            skipped = _gone not in pr.states_entered
            reached = _fwd in pr.states_entered
            return CheckResult(
                passed=skipped and reached,
                expected=f"removed state '{_gone}' is never entered; flow goes "
                         f"straight to '{_fwd}'",
                observed=f"states entered: {pr.states_entered} "
                         f"(skipped={skipped}, reached_forward={reached})",
            )

        muts.append(Mutation(
            id=f"state_remove_{gone}", kind="state_remove", target=gone,
            description=f"remove mid-flow state '{gone}', rewiring its inbound "
                        f"edges to '{fwd}'",
            expected_signal=f"the session skips '{gone}' and reaches '{fwd}' "
                            f"directly",
            probe=[opener, ready, ack],
            apply=apply_rm, check=check_rm, voice_spot=True))
    else:
        skips.append(Skip("state_remove",
                          "no removable mid-state with a forward target"))

    # 8 ── set_variable + expression edge ───────────────────────────────────────
    if a.conv1 and a.entry_edge and a.close_state:
        conv1, entry_edge, close_state = a.conv1, a.entry_edge, a.close_state

        def apply_var(f: dict, _conv1=conv1, _edge=entry_edge,
                      _close=close_state) -> dict:
            f = copy.deepcopy(f)
            f.setdefault("variables", []).append(
                {"key": PROBE_VAR, "type": "string", "initial": ""})
            f["states"].append({"id": PROBE_SET_STATE, "type": "set_variable",
                                "key": PROBE_VAR, "value": PROBE_VAR_VALUE})
            for t in f.get("transitions") or []:
                if t.get("id") == _edge:
                    t["to"] = PROBE_SET_STATE   # detour the entry through the setter
            f["transitions"].append({
                "id": PROBE_SET_EDGE, "from": PROBE_SET_STATE, "to": _conv1,
                "kind": "always", "priority": 10})
            f["transitions"].append({
                "id": PROBE_EXPR_EDGE, "from": _conv1, "to": _close,
                "kind": "expression",
                "expr": f"{PROBE_VAR} == '{PROBE_VAR_VALUE}'", "priority": 1})
            return f

        def check_var(pr: ProbeResult, _close=close_state) -> CheckResult:
            sets = var_set_steps(pr.trace, PROBE_VAR)
            set_ok = any(s.get("value") == PROBE_VAR_VALUE for s in sets)
            fired = [c for c in transition_checks(pr.trace, PROBE_EXPR_EDGE)
                     if c.get("result") == "fired"]
            routed = _close in pr.states_entered
            return CheckResult(
                passed=set_ok and bool(fired) and routed,
                expected=f"var_set {PROBE_VAR}={PROBE_VAR_VALUE!r} appears, the "
                         f"expression edge fires, and the flow routes to "
                         f"'{_close}'",
                observed=f"var_set_seen={set_ok} (steps: {len(sets)}), "
                         f"expr_edge_fired={bool(fired)}, "
                         f"routed_to_close={routed}, ended={pr.ended}",
            )

        muts.append(Mutation(
            id="set_variable_expression", kind="variable", target=PROBE_SET_STATE,
            description=f"insert a set_variable state ({PROBE_VAR}="
                        f"{PROBE_VAR_VALUE!r}) on the entry path plus an "
                        f"expression edge '{PROBE_EXPR_EDGE}' keyed on it",
            expected_signal="var_set appears in the trace and the expression edge "
                            "routes the very next turn to the closing state",
            probe=[opener, ack],
            apply=apply_var, check=check_var, voice_spot=True))
    else:
        skips.append(Skip("variable",
                          "no entry always-edge / closing state to hook the "
                          "set_variable detour onto"))

    return muts, skips


def voice_spot_subset(mutations: list[Mutation]) -> list[Mutation]:
    """One mutation per step-kind for the voice spot-check pass (the owner's
    'voice-bench picks it up' proof without running the whole matrix over audio)."""
    return [m for m in mutations if m.voice_spot]
