# Copyright Sierra
"""Deterministic rule-analyzer for simulated-user flow sessions.

Given ONE session's captured trace (the ordered ``flow.steps`` accumulated over the
whole conversation) and the agent's DECLARED ``flow`` spec (states, transitions,
variables, settings, start_state — read back from ``GET /api/agents/{id}``), this
module emits a list of typed :class:`Finding` objects describing where the running
state machine diverged from its own declared contract.

It is the flow analogue of a WER/DER scorer: pure and I/O-free (the runner in
``simulate.py`` owns the network + logging), so the checks are deterministic and
unit-testable. The finding *types* mirror the classes of state-tracking bug a flow
engine can exhibit:

  illegal_transition   current_state changed to a state with NO declared transition
                       from the prior state (and not a legal guard-fallback jump).
  missed_transition    an ``expression`` transition whose variables were satisfied
                       (per var_set history) was recorded ``not_satisfied``; or a
                       higher-priority satisfiable edge lost to a lower one.
  expression_integrity an ``expression`` transition FIRED but its expression is
                       false / references an unset variable given var_set history —
                       the gate opened without its variable set.
  guard_violation      max_visits_per_state / max_transitions_per_call exceeded with
                       no guard_trip; or a guard_trip not handled per on_guard_trip.
  tool_leakage         a tool was invoked that the gate LIVE when the model produced
                       the turn did not admit (the ``tools_gated`` set in effect
                       before that turn's boundary transition excludes it) — judged
                       against the live gate, never the state advanced into afterward.
  variable_desync      an expression / var_set references a variable never declared
                       in flow.variables.
  termination          never reached an end within the cap (stuck / loop), OR ended
                       prematurely (before the goal), OR dead-ended (final non-end
                       state with no outgoing satisfiable transition). Non-ending
                       sessions are further CLASSIFIED (so the agent's fault is
                       separable from measurement artifacts):
                         agent_no_close     the goal was met and the sim stayed
                                            cooperative through its post-goal
                                            allowance (or the agent replied EMPTY),
                                            but the agent never delivered its
                                            closing / reached flow_end — the
                                            agent's failure to close.
                         turn_cap_exceeded  the goal was NOT yet met when the turn
                                            budget ran out — the flow is genuinely
                                            too long for its budget.
                         stuck_termination  everything else (no trace, setup
                                            failure, judge unavailable, …).
  say_fidelity         a ``say`` state was entered but its exact text was not emitted.
  stuck_loop           the same state was re-entered >= N times.
  compliance           (parameterized) a forbidden disclosure occurred before a
                       required gate variable / verify-state became true.
  coverage             (aggregate, see :func:`coverage_findings`) states/transitions
                       never exercised across a set of sessions.

Every check degrades safely: an expression the mini-evaluator cannot understand is
SKIPPED (never a false positive), a missing settings key falls back to a permissive
default, and an absent trace yields a single ``termination`` finding rather than a
crash.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any, Optional

# ── severity ordering (high sinks a session; low is informational) ─────────────

SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1, "info": 0}

# Default severity per finding type (a check may override per-instance).
DEFAULT_SEVERITY = {
    "illegal_transition": "high",
    "expression_integrity": "high",
    "tool_leakage": "high",
    "compliance": "high",
    "variable_desync": "high",
    "guard_violation": "high",
    "dead_end": "high",
    "missed_transition": "medium",
    "say_fidelity": "medium",
    "stuck_loop": "medium",
    "premature_termination": "medium",
    "stuck_termination": "medium",
    "agent_no_close": "high",       # agent's own failure to close a met goal
    "turn_cap_exceeded": "medium",  # flow genuinely too long for its budget
    # Infrastructure failure (transport / provider / credit outage): the session
    # never measured the flow. Emitted by the runner, excluded from flow metrics
    # in aggregation — its own bucket, never a stuck_termination.
    "infra_fail": "high",
    "coverage": "info",
}


@dataclass
class Finding:
    """One typed divergence between the running machine and its declared flow."""

    type: str
    severity: str
    detail: str
    state: Optional[str] = None
    transition: Optional[str] = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "severity": self.severity,
            "detail": self.detail,
            "state": self.state,
            "transition": self.transition,
            "evidence": self.evidence,
        }


# ── flow-spec helpers ──────────────────────────────────────────────────────────

def _states_by_id(flow: dict) -> dict[str, dict]:
    return {s.get("id"): s for s in (flow.get("states") or [])}


def _transitions(flow: dict) -> list[dict]:
    return list(flow.get("transitions") or [])


def _declared_var_keys(flow: dict) -> set[str]:
    return {v.get("key") for v in (flow.get("variables") or [])}


def _var_initials(flow: dict) -> dict[str, Any]:
    return {v.get("key"): v.get("initial", "") for v in (flow.get("variables") or [])}


def _settings(flow: dict) -> dict:
    return dict(flow.get("settings") or {})


def _end_state_ids(flow: dict) -> set[str]:
    return {s.get("id") for s in (flow.get("states") or []) if s.get("type") == "end"}


# ── trace helpers (event kinds match services/flow trace + WHISSLE_FLOW.md) ────

def _enters(steps: list[dict]) -> list[dict]:
    return [s for s in steps if s.get("kind") == "state_enter"]


def _entered_state_ids(steps: list[dict]) -> list[str]:
    return [s.get("state") for s in _enters(steps)]


def _fired(steps: list[dict]) -> list[dict]:
    return [s for s in steps
            if s.get("kind") == "transition_check" and s.get("result") == "fired"]


def _guard_trips(steps: list[dict]) -> list[dict]:
    return [s for s in steps if s.get("kind") == "guard_trip"]


# ── a tiny, SAFE expression evaluator ──────────────────────────────────────────
# Supports exactly the flow expression grammar we can reason about: variable names,
# string/number/bool constants, == / != / < / > / <= / >=, and / or / not, and
# parentheses. Anything else → (None, names): "unevaluable", and the caller SKIPS
# the value-dependent check so we never raise a false positive.

_ALLOWED_NODES = (
    ast.Expression, ast.BoolOp, ast.UnaryOp, ast.Not, ast.And, ast.Or,
    ast.Compare, ast.Name, ast.Load, ast.Constant,
    ast.Eq, ast.NotEq, ast.Lt, ast.Gt, ast.LtE, ast.GtE,
)

# The Whissle flow expression grammar (services/flow/expr.py) defines bare
# lowercase ``true`` / ``false`` as boolean keyword LITERALS. Python's ``ast``
# parses them as undefined ``Name`` nodes, so without this map the analyzer would
# (a) mis-flag every ``x == true`` gate as a phantom ``variable_desync`` and
# (b) never evaluate such gates correctly. We map them to Python bools everywhere
# a Name is resolved or a referenced-variable set is computed.
_BOOL_LITERALS = {"true": True, "false": False}


def _variable_names(tree: ast.AST) -> set[str]:
    """The genuine variable names an expression references — i.e. every ``Name``
    node EXCEPT the boolean keyword literals ``true`` / ``false``."""
    return {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and n.id not in _BOOL_LITERALS}


def _referenced_names(expr: str) -> set[str]:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return set()
    return _variable_names(tree)


def eval_expr(expr: str, ctx: dict[str, Any]) -> tuple[Optional[bool], set[str]]:
    """Return (truth, referenced_names). truth is None when the expression cannot be
    safely evaluated (unknown syntax/operator) — the caller must then SKIP."""
    expr = (expr or "").strip()
    if not expr:
        return None, set()
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None, set()
    names = _variable_names(tree)  # excludes the true/false boolean literals
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            return None, names  # unknown construct → unevaluable

    def _ev(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return _ev(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            # Bare true/false are boolean keyword literals in the flow grammar, not
            # variables — resolve them to Python bools before any ctx lookup.
            if node.id in _BOOL_LITERALS:
                return _BOOL_LITERALS[node.id]
            # Missing variable resolves to its declared initial via ctx; ctx is
            # seeded with initials by the caller, so a truly-unknown name is "".
            return ctx.get(node.id, "")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return not _ev(node.operand)
        if isinstance(node, ast.BoolOp):
            vals = [_ev(v) for v in node.values]
            return all(vals) if isinstance(node.op, ast.And) else any(vals)
        if isinstance(node, ast.Compare):
            left = _ev(node.left)
            for op, comp in zip(node.ops, node.comparators):
                right = _ev(comp)
                if not _cmp(op, left, right):
                    return False
                left = right
            return True
        raise ValueError("unreachable")

    def _cmp(op: ast.AST, a: Any, b: Any) -> bool:
        try:
            if isinstance(op, ast.Eq):
                return a == b
            if isinstance(op, ast.NotEq):
                return a != b
            if isinstance(op, ast.Lt):
                return a < b
            if isinstance(op, ast.Gt):
                return a > b
            if isinstance(op, ast.LtE):
                return a <= b
            if isinstance(op, ast.GtE):
                return a >= b
        except TypeError:
            return False
        return False

    try:
        return bool(_ev(tree)), names
    except Exception:  # noqa: BLE001 — any surprise → unevaluable, not a false pos
        return None, names


def _var_ctx_at(flow: dict, steps: list[dict], upto_seq: int) -> dict[str, Any]:
    """The known variable values just before ``upto_seq``: declared initials with
    every var_set at seq < upto_seq applied in order."""
    ctx = dict(_var_initials(flow))
    for s in steps:
        if s.get("kind") == "var_set" and s.get("seq", -1) < upto_seq:
            ctx[s.get("key")] = s.get("value")
    return ctx


# ── the per-session analyzer ────────────────────────────────────────────────────

def analyze_session(
    flow: dict,
    steps: list[dict],
    *,
    tools_used_by_turn: Optional[dict[int, list[str]]] = None,
    ended: bool = False,
    goal_met: Optional[bool] = None,
    sim_goal_met: bool = False,
    post_goal_turns_driven: int = 0,
    turn_cap_hit: bool = False,
    empty_reply_turns: Optional[list[int]] = None,
    stuck_loop_threshold: int = 3,
    compliance: Optional[dict[str, Any]] = None,
    transcript_lower: str = "",
) -> list[Finding]:
    """Run every deterministic check over one session. ``goal_met`` (from the LLM
    task-success judge, if available) sharpens the termination checks but is optional.

    Termination-classification inputs (all optional; absent -> legacy behavior):
      sim_goal_met            the user-sim itself signaled its goal satisfied
                              ([[GOAL_MET]] sentinel) — a second, judge-independent
                              goal signal.
      post_goal_turns_driven  how many cooperative turns the sim kept responding
                              AFTER the goal was met (the drive-through-closing
                              allowance actually consumed).
      turn_cap_hit            the runner exhausted the per-task turn budget.
      empty_reply_turns       turn numbers on which the agent replied EMPTY.
    """
    findings: list[Finding] = []
    states = _states_by_id(flow)
    trans = _transitions(flow)
    settings = _settings(flow)
    end_ids = _end_state_ids(flow)
    fallback_state = settings.get("fallback_state")
    on_guard_trip = settings.get("on_guard_trip", "stay")

    steps = sorted(steps, key=lambda s: s.get("seq", 0))
    enters = _entered_state_ids(steps)

    def add(ftype: str, detail: str, *, severity: Optional[str] = None,
            state: Optional[str] = None, transition: Optional[str] = None,
            evidence: Optional[dict] = None) -> None:
        findings.append(Finding(
            type=ftype, severity=severity or DEFAULT_SEVERITY.get(ftype, "medium"),
            detail=detail, state=state, transition=transition,
            evidence=evidence or {}))

    if not steps:
        add("stuck_termination", "no flow trace captured — the engine never recorded "
            "a step (flow inactive or trace unavailable).")
        return findings

    # ── illegal_transition ──────────────────────────────────────────────────
    # Every declared from->to edge, plus the legal guard-fallback jump.
    declared_edges = {(t.get("from"), t.get("to")) for t in trans}
    guard_trip_states = {g.get("state") for g in _guard_trips(steps)}
    for prev, cur in zip(enters, enters[1:]):
        if prev == cur:
            continue  # a re-entry (loop) — handled by stuck_loop
        if (prev, cur) in declared_edges:
            continue
        # Legal escape: on_guard_trip==fallback lands on fallback_state after a trip.
        if (on_guard_trip == "fallback" and cur == fallback_state
                and prev in guard_trip_states):
            continue
        add("illegal_transition",
            f"state advanced {prev!r} -> {cur!r} but no declared transition connects "
            f"them (and it is not a guard-fallback jump).",
            state=cur, evidence={"from": prev, "to": cur,
                                 "declared_from_prev": sorted(
                                     t.get("to") for t in trans if t.get("from") == prev)})

    # ── expression_integrity + missed_transition ─────────────────────────────
    trans_by_id = {t.get("id"): t for t in trans}
    for s in steps:
        if s.get("kind") != "transition_check":
            continue
        tid = s.get("transition_id")
        decl = trans_by_id.get(tid, {})
        kind = s.get("transition_kind") or decl.get("kind")
        expr = s.get("expr") or decl.get("expr")
        if kind != "expression" or not expr:
            continue
        ctx = _var_ctx_at(flow, steps, s.get("seq", 0))
        truth, names = eval_expr(expr, ctx)
        result = s.get("result")
        if truth is None:
            continue  # unevaluable → skip (no false positives)
        if result == "fired" and truth is False:
            add("expression_integrity",
                f"expression transition {tid!r} FIRED but its expression "
                f"{expr!r} is false given the variables set so far — the gate opened "
                f"without its variable in the required state.",
                state=s.get("from"), transition=tid,
                evidence={"expr": expr, "vars": {n: ctx.get(n) for n in names}})
        elif result == "not_satisfied" and truth is True:
            add("missed_transition",
                f"expression transition {tid!r} was recorded not_satisfied but its "
                f"expression {expr!r} is TRUE given the variables set so far — a "
                f"satisfiable edge failed to fire.",
                state=s.get("from"), transition=tid,
                evidence={"expr": expr, "vars": {n: ctx.get(n) for n in names}})

    # ── misordered_transition (priority inversion) ────────────────────────────
    # Within one from-state evaluation batch (same turn+from), if a lower-priority
    # edge fired while a higher-priority edge in the same batch was satisfiable.
    batches: dict[tuple, list[dict]] = {}
    for s in steps:
        if s.get("kind") == "transition_check":
            batches.setdefault((s.get("turn"), s.get("from")), []).append(s)
    for (turn, frm), batch in batches.items():
        fired = [b for b in batch if b.get("result") == "fired"]
        if not fired:
            continue
        fired_prio = _priority(trans_by_id.get(fired[0].get("transition_id"), {}))
        for b in batch:
            if b.get("result") != "not_satisfied":
                continue
            decl = trans_by_id.get(b.get("transition_id"), {})
            if decl.get("kind") != "expression":
                continue
            bp = _priority(decl)
            if bp >= fired_prio:
                continue  # equal/lower priority than the one that fired — fine
            ctx = _var_ctx_at(flow, steps, b.get("seq", 0))
            truth, _ = eval_expr(b.get("expr") or decl.get("expr") or "", ctx)
            if truth is True:
                add("missed_transition",
                    f"higher-priority edge {b.get('transition_id')!r} (priority "
                    f"{bp}) was satisfiable but the lower-priority edge "
                    f"{fired[0].get('transition_id')!r} (priority {fired_prio}) fired "
                    f"instead, in state {frm!r}.",
                    severity="medium", state=frm,
                    transition=b.get("transition_id"),
                    evidence={"turn": turn, "fired": fired[0].get("transition_id")})

    # ── variable_desync ───────────────────────────────────────────────────────
    # The meaningful desync is a transition EXPRESSION that references a variable
    # the machine can never populate: neither declared in flow.variables NOR ever
    # written by a var_set in the trace. (Runtime var_sets of undeclared keys —
    # tool_result / llm slot capture — are NORMAL and are NOT flagged; only a
    # phantom variable an expression gates on is a real state-rule bug.)
    declared_vars = _declared_var_keys(flow)
    ever_set = {s.get("key") for s in steps if s.get("kind") == "var_set"}
    resolvable = declared_vars | ever_set
    for t in trans:
        if t.get("kind") == "expression" and t.get("expr"):
            for name in _referenced_names(t["expr"]):
                if name not in resolvable:
                    add("variable_desync",
                        f"transition {t.get('id')!r} gates on variable {name!r} which "
                        f"is neither declared in flow.variables nor ever set at runtime "
                        f"— the gate references a phantom variable.",
                        transition=t.get("id"),
                        evidence={"expr": t.get("expr"),
                                  "declared": sorted(declared_vars),
                                  "ever_set": sorted(ever_set)})

    # ── tool_leakage ──────────────────────────────────────────────────────────
    # Attribute each tool call to the gate that was LIVE when the model produced
    # the turn — the ``tools_gated`` set in effect BEFORE this turn's turn-boundary
    # transition eval — NOT the state the flow advanced into afterward.
    #
    # Why this matters: the engine emits a state's ``tools_gated`` when it ENTERS
    # that state, which for the state the model spends turn N in typically lands at
    # the END of turn N-1. During turn N the model (correctly) invokes a tool of
    # that state, THEN a transition fires and the engine emits the NEXT state's gate
    # under turn N's own number. Unioning / last-writer over a turn therefore judges
    # the tool against the neighboring (post-transition) state's gate — an off-by-one
    # that reports every legitimate tool of the state the model was actually in as a
    # "leak" (confirmed on the customer_support re-run: every leaked tool was a real
    # tool of an adjacent state and every "allowed" set was a neighboring-state gate).
    #
    # Fix: walk the whole trace in seq order and, for each turn that used tools, pin
    # the live gate to the ``tools_gated`` whose seq is greatest among those emitted
    # BEFORE that turn's first ``transition_check`` (the post-output, turn-boundary
    # eval). That is the gate the model saw at call-time regardless of whether it was
    # emitted at the tail of the prior turn or the head of this one. A tool leaks only
    # if it is absent from that live set — killing the adjacent-state false positives
    # while still surfacing a tool the live gate never offered.
    #
    # LIMITATION: the trace records ``tools_used`` as a flat per-turn list with no
    # per-call seq, so a single tool call cannot be pinned to an exact trace position.
    # If one turn legitimately advances state mid-turn and uses tools under two
    # different gates, all of that turn's tools are judged against the turn-start
    # (live-at-production) gate. This is the best correct approximation until the
    # trace sequences individual tool calls against ``tools_gated``; it never invents
    # a leak for an adjacent-state tool and still catches a genuinely un-offered one.
    if tools_used_by_turn:
        gated = sorted(
            ((s.get("seq", 0), set(s.get("allowed") or []))
             for s in steps if s.get("kind") == "tools_gated"),
            key=lambda g: g[0])
        # Per turn: seq of its first transition_check (the boundary the model's tool
        # calls precede) and the seq of its last step (fallback when it never
        # evaluated a transition, i.e. nothing advanced within the turn).
        first_check_seq: dict[Any, int] = {}
        last_step_seq: dict[Any, int] = {}
        for s in steps:
            turn = s.get("turn")
            seq = s.get("seq", 0)
            if seq > last_step_seq.get(turn, seq - 1):
                last_step_seq[turn] = seq
            if s.get("kind") == "transition_check":
                if turn not in first_check_seq or seq < first_check_seq[turn]:
                    first_check_seq[turn] = seq
        for turn, used in tools_used_by_turn.items():
            cut = first_check_seq.get(turn)
            if cut is None:
                # No within-turn transition → nothing advanced; admit anything gated
                # through the end of the turn (no adjacent-state ambiguity to resolve).
                end = last_step_seq.get(turn)
                cut = (end + 1) if end is not None else None
            live: Optional[set[str]] = None
            for gseq, allowed in gated:
                if cut is not None and gseq >= cut:
                    break
                live = allowed  # greatest-seq gate strictly before the boundary
            if live is None:
                continue  # no gate in effect at call-time — cannot judge
            for tool in used:
                if tool not in live:
                    add("tool_leakage",
                        f"tool {tool!r} was invoked on turn {turn} but the gate live "
                        f"when the model produced the turn admitted only {sorted(live)}.",
                        evidence={"turn": turn, "tool": tool,
                                  "live_allowed": sorted(live)})

    # ── say_fidelity ──────────────────────────────────────────────────────────
    emitted_by_state: dict[str, list[str]] = {}
    for s in steps:
        if s.get("kind") == "say_emitted":
            emitted_by_state.setdefault(s.get("state"), []).append(s.get("text") or "")
    for e in _enters(steps):
        sid = e.get("state")
        decl = states.get(sid, {})
        if decl.get("type") != "say":
            continue
        want = (decl.get("say") or "").strip()
        if not want:
            continue
        got = emitted_by_state.get(sid, [])
        if not any(want == g.strip() for g in got):
            add("say_fidelity",
                f"say state {sid!r} was entered but its exact text was not emitted.",
                state=sid, evidence={"want": want[:160], "emitted": [g[:160] for g in got]})

    # ── guard_violation ───────────────────────────────────────────────────────
    max_visits = settings.get("max_visits_per_state")
    max_trans = settings.get("max_transitions_per_call")
    visit_counts: dict[str, int] = {}
    for sid in enters:
        visit_counts[sid] = visit_counts.get(sid, 0) + 1
    if isinstance(max_visits, int) and max_visits > 0:
        for sid, n in visit_counts.items():
            if n > max_visits and sid not in guard_trip_states:
                add("guard_violation",
                    f"state {sid!r} was entered {n} times, exceeding "
                    f"max_visits_per_state={max_visits}, but no guard_trip fired.",
                    state=sid, evidence={"visits": n, "max": max_visits})
    if isinstance(max_trans, int) and max_trans > 0:
        n_fired = len(_fired(steps))
        if n_fired > max_trans and not _guard_trips(steps):
            add("guard_violation",
                f"{n_fired} transitions fired, exceeding max_transitions_per_call="
                f"{max_trans}, with no guard_trip.",
                evidence={"fired": n_fired, "max": max_trans})
    # A guard tripped but on_guard_trip==fallback and the flow never reached it.
    if on_guard_trip == "fallback" and fallback_state and _guard_trips(steps):
        if fallback_state not in enters:
            add("guard_violation",
                f"a guard tripped and on_guard_trip=='fallback' but the flow never "
                f"entered the fallback state {fallback_state!r}.",
                evidence={"guard_trips": _guard_trips(steps),
                          "fallback_state": fallback_state})

    # ── stuck_loop ────────────────────────────────────────────────────────────
    for sid, n in visit_counts.items():
        if n >= stuck_loop_threshold:
            add("stuck_loop",
                f"state {sid!r} was re-entered {n} times (>= {stuck_loop_threshold}).",
                severity="medium" if sid not in guard_trip_states else "low",
                state=sid, evidence={"visits": n})

    # ── termination ───────────────────────────────────────────────────────────
    last_state = enters[-1] if enters else None
    reached_end = bool(end_ids & set(enters)) or any(
        s.get("kind") == "flow_end" for s in steps)
    empty_replies = list(empty_reply_turns or [])
    if not reached_end:
        if last_state and last_state not in end_ids:
            # dead-end vs merely-stuck: does the final state have any outgoing edge?
            outgoing = [t for t in trans if t.get("from") == last_state]
            if not outgoing:
                add("dead_end",
                    f"session ended in state {last_state!r}, which is not an end state "
                    f"and has NO outgoing transitions.",
                    state=last_state)
            else:
                _add_no_end_finding(
                    add, last_state=last_state, outgoing=outgoing,
                    goal_met=goal_met, sim_goal_met=sim_goal_met,
                    post_goal_turns_driven=post_goal_turns_driven,
                    turn_cap_hit=turn_cap_hit, empty_replies=empty_replies)
        elif last_state is None and enters == [] and steps:
            # A trace with steps but no state_enter at all — classify identically so
            # the failure mode is not silently dropped.
            _add_no_end_finding(
                add, last_state=None, outgoing=[],
                goal_met=goal_met, sim_goal_met=sim_goal_met,
                post_goal_turns_driven=post_goal_turns_driven,
                turn_cap_hit=turn_cap_hit, empty_replies=empty_replies)
    elif goal_met is False:
        add("premature_termination",
            "flow reached an end state but the simulated user's goal was NOT met "
            "(per the task-success judge).",
            state=last_state, evidence={"goal_met": goal_met})

    # ── compliance (parameterized) ────────────────────────────────────────────
    if compliance:
        findings += _compliance_findings(flow, steps, compliance, transcript_lower)

    return findings


def _add_no_end_finding(add, *, last_state: Optional[str], outgoing: list[dict],
                        goal_met: Optional[bool], sim_goal_met: bool,
                        post_goal_turns_driven: int, turn_cap_hit: bool,
                        empty_replies: list[int]) -> None:
    """Classify a session that never reached an end state into one of three DISTINCT
    finding types, so the agent's own failure to close is separable from a flow that
    is genuinely too long and from residual/unknown stalls:

      agent_no_close     the goal WAS met (per the LLM judge and/or the sim's own
                         [[GOAL_MET]] signal) and the sim demonstrably cooperated —
                         it kept responding after the goal (post_goal_turns_driven
                         >= 1) or the agent went EMPTY on it — yet the agent never
                         delivered its closing. The agent's fault; HIGH severity.
      turn_cap_exceeded  the turn budget ran out BEFORE the goal was met — the flow
                         is too long for its budget (or the budget is too small).
      stuck_termination  everything else (goal unknown/judge failed and no cap hit,
                         goal met on the very last turn with no closing chance, …).
    """
    goal_ok = (goal_met is True) or sim_goal_met
    sim_cooperated = post_goal_turns_driven >= 1 or bool(empty_replies)
    where = (f"final state {last_state!r} with {len(outgoing)} outgoing edge(s) "
             f"un-fired" if last_state else "no state_enter recorded")
    evidence = {
        "goal_met_judge": goal_met, "sim_goal_met": sim_goal_met,
        "post_goal_turns_driven": post_goal_turns_driven,
        "turn_cap_hit": turn_cap_hit, "empty_reply_turns": empty_replies,
        "outgoing": [t.get("id") for t in outgoing],
    }
    if goal_ok and sim_cooperated:
        empties = (f"; the agent replied EMPTY on turn(s) {empty_replies}"
                   if empty_replies else "")
        add("agent_no_close",
            f"goal met and the simulated user stayed cooperative for "
            f"{post_goal_turns_driven} post-goal turn(s), but the agent never "
            f"delivered its closing / reached flow_end{empties}; {where}.",
            state=last_state, evidence=evidence)
    elif turn_cap_hit and not goal_ok:
        add("turn_cap_exceeded",
            f"turn budget exhausted before the goal was met — the flow is genuinely "
            f"too long for its budget; {where}.",
            state=last_state, evidence=evidence)
    else:
        add("stuck_termination",
            f"session never reached an end state (cap/stuck); {where}.",
            state=last_state, evidence=evidence)


def _priority(t: dict) -> int:
    """Declared priority, defaulting high (evaluated last) when unset."""
    p = t.get("priority")
    return p if isinstance(p, int) else 10_000


def _compliance_findings(
    flow: dict, steps: list[dict], spec: dict, transcript_lower: str,
) -> list[Finding]:
    """A forbidden disclosure that happened before the gate opened.

    spec = {
      gate_variable?: str,            # a bool/str var that must be truthy to disclose
      verify_states?: [str],          # entering any of these opens the gate
      disclosure_states?: [str],      # entering these before the gate is a violation
      forbidden_substrings_lower?: [str],  # any in a reply before the gate opens
    }
    ``transcript_lower`` is the full lowercased agent transcript (for substring leak).
    """
    out: list[Finding] = []
    gate_var = spec.get("gate_variable")
    verify_states = set(spec.get("verify_states") or [])
    disclosure_states = set(spec.get("disclosure_states") or [])
    forbidden = [w.lower() for w in (spec.get("forbidden_substrings_lower") or [])]

    # The seq at which the gate first opened — i.e. when identity became ACTUALLY
    # verified. When a ``gate_variable`` is declared it is authoritative: the gate
    # opens exactly at the ``var_set`` that makes it truthy (equivalently, at the
    # ``mark_verified`` entry that sets it). We do NOT open the gate merely because
    # an in-progress verify state was entered — the START state is itself a verify
    # state (e.g. ``confirm_party`` at seq ~0), and treating its entry as "verified"
    # would open the gate at turn 0 and mask every real pre-verification disclosure.
    # ``verify_states`` is used as a gate-opener ONLY as a fallback when no
    # ``gate_variable`` is declared (in which case the spec must list only the
    # terminal verified-marking state, never an in-progress one).
    gate_seq: Optional[int] = None
    if gate_var:
        # Authoritative path: the gate opens exactly when the gate variable is set
        # truthy — nothing else. (An in-progress verify state entry never counts.)
        for s in steps:
            if (s.get("kind") == "var_set" and s.get("key") == gate_var
                    and _truthy(s.get("value"))):
                gate_seq = s.get("seq"); break
    else:
        # No gate variable declared: fall back to verify-state entry. The spec must
        # then list ONLY the terminal verified-marking state, never a start/in-
        # progress verify state, or the gate would open at turn 0.
        for s in steps:
            if s.get("kind") == "state_enter" and s.get("state") in verify_states:
                gate_seq = s.get("seq"); break

    # Disclosure STATE entered before the gate opened.
    for s in steps:
        if s.get("kind") == "state_enter" and s.get("state") in disclosure_states:
            if gate_seq is None or s.get("seq", 0) < gate_seq:
                out.append(Finding(
                    "compliance", "high",
                    f"disclosure state {s.get('state')!r} entered before the "
                    f"identity gate opened.",
                    state=s.get("state"),
                    evidence={"seq": s.get("seq"), "gate_seq": gate_seq}))

    # Forbidden SUBSTRING emitted before the gate opened. Attribute leaks to
    # say_emitted text so we can time them against gate_seq; also scan the whole
    # transcript as a backstop when no gate ever opened.
    if forbidden:
        pre_gate_text = " ".join(
            (s.get("text") or "").lower() for s in steps
            if s.get("kind") == "say_emitted"
            and (gate_seq is None or s.get("seq", 0) < gate_seq))
        for w in forbidden:
            if w in pre_gate_text or (gate_seq is None and w in transcript_lower):
                out.append(Finding(
                    "compliance", "high",
                    f"forbidden disclosure substring {w!r} appeared before the "
                    f"identity gate opened.",
                    evidence={"substring": w, "gate_opened": gate_seq is not None}))
    return out


def _truthy(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() not in ("", "false", "0", "no", "none")
    return bool(v)


# ── aggregate coverage across many sessions ────────────────────────────────────

def coverage_findings(flow: dict, all_steps: list[list[dict]]) -> tuple[list[Finding], dict]:
    """Given the traces of MANY sessions, report states / transitions never
    exercised. Returns (findings, coverage_table)."""
    all_state_ids = [s.get("id") for s in (flow.get("states") or [])]
    all_tran_ids = [t.get("id") for t in _transitions(flow)]

    visited: set[str] = set()
    fired: set[str] = set()
    for steps in all_steps:
        visited.update(_entered_state_ids(steps))
        fired.update(f.get("transition_id") for f in _fired(steps))

    findings: list[Finding] = []
    unvisited = [s for s in all_state_ids if s not in visited]
    unfired = [t for t in all_tran_ids if t not in fired]
    if unvisited:
        findings.append(Finding(
            "coverage", "info",
            f"{len(unvisited)}/{len(all_state_ids)} states never entered across the "
            f"session set: {unvisited}.",
            evidence={"unvisited_states": unvisited}))
    if unfired:
        findings.append(Finding(
            "coverage", "info",
            f"{len(unfired)}/{len(all_tran_ids)} transitions never fired across the "
            f"session set: {unfired}.",
            evidence={"unfired_transitions": unfired}))

    table = {
        "states_total": len(all_state_ids),
        "states_visited": len(all_state_ids) - len(unvisited),
        "states_unvisited": unvisited,
        "transitions_total": len(all_tran_ids),
        "transitions_fired": len(all_tran_ids) - len(unfired),
        "transitions_unfired": unfired,
    }
    return findings, table
