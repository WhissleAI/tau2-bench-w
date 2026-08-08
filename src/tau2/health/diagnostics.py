# Copyright Sierra
"""One diagnostic-artifact shape for every healthcare benchmark adapter.

WHY THIS EXISTS
---------------
The three health adapters (PatientAgentBench, MedAgentBench, AgentClinic) each
persisted a per-case record carrying a transcript, a tool list and a score. At
three cases that is enough to eyeball. At ~100 cases each it is not: a bare score
with no trace cannot be debugged, and three different record shapes mean three
different readers. The flow-sim harness (``tau2/flow/simulate.py``) already
produces the forensics we want — the engine's own step trace, per-turn voice
signals, latency rollups, honest availability markers — into its
``<task>_<ts>.session.json`` sidecar. This module is that same discipline,
factored so all three adapters emit ONE shape.

WHAT LANDS ON A CASE
--------------------
Every per-case record grows a single ``"diagnostics"`` key holding a
:data:`SCHEMA`-stamped envelope. Existing keys are untouched, so every current
reader and report path keeps working; a new reader keys off ``["diagnostics"]``
and works across all three benchmarks. Sections:

``flow``              the in-call state machine's own account of the run —
                      state_enter / transition_check (with the ``reason``
                      rationale) / say_emitted / tools_gated / var_set (with its
                      ``source``) / guard_trip / state_divergence / flow_end,
                      plus derived rollups.
``signals``           per-turn VOICE-pipeline signals: hesitation, shadow/eager
                      reply activity, speculative tools, turn completeness,
                      emotion/intent distributions, barge-in, response latency.
``metadata_sidecar``  the whissle-large per-interim metadata frames
                      (emotion/intent/age/gender + probabilities).
``tools``             resolved arguments, result, ok/error per call — and, where
                      the benchmark writes, whether the write actually LANDED.
``provenance``        agent id, base URL, transport endpoint, mode, seed,
                      sampling stratum, judge provider + independence, harness
                      commit — so one case file is self-describing.
``cost``              judge/support-LLM calls and USD attributable to this case.

HONESTY RULE — ABSENCE IS NOT A MEASUREMENT
-------------------------------------------
Signals and metadata are produced by the VOICE pipeline. A text run does not
have them — it does not have them *at zero*, it does not have them at all. Every
section is therefore either

    {"available": true,  "reason": null, "source": "...", ...payload...}

or

    {"available": false, "reason": "<why>", "source": null,
     "turns": null, "summary": null}

with every payload field explicitly ``null``. A reader that sees ``turns: null``
cannot mistake it for "no signals fired"; a reader that saw ``turns: []`` could.
The flat :func:`availability` block mirrors the same booleans (``signals_available``
etc.) beside their reasons, so the question "was this measured?" is answerable
with one key lookup and never by counting an empty list.

The canonical reasons live in this module (:data:`REASON_TEXT_MODE`,
:data:`REASON_BENCH_ENDPOINT`, …) so three adapters cannot drift into three
different phrasings of the same gap.

WHICH SIGNALS EXIST ON WHICH TRANSPORT
--------------------------------------
=========================  ==========  ===========  =============  ==========
transport                  flow trace  voice sigs   md sidecar     tool args
=========================  ==========  ===========  =============  ==========
POST /api/bench/agent-turn  no [1]      no           no             yes [2]
POST /api/agents/{id}/chat/turn + GET /flow/trace
                            YES         no           no             yes
LiveKit voice (bench/voice/start)
                            yes [3]     YES [4]      YES [4]        yes
=========================  ==========  ===========  =============  ==========

[1] ``/api/bench/agent-turn`` is a STATELESS brain call. It assembles the real
    system prompt and calls the LLM; it runs no ``FlowRuntime``, mints no
    ``conversations`` row, and returns only ``reply``/``tool_calls``/``content``/
    ``stop_reason``. There is therefore no flow block to read and no trace to
    fetch — verified against the backend route, not assumed. All three adapters
    drive this endpoint in their default text mode, so all three record
    :data:`REASON_BENCH_ENDPOINT` rather than an empty trace.
[2] The harness executes the tools, so arguments/results are ours to record and
    are fully captured.
[3] Persisted since PR #613 when ``/api/bench/voice/start`` returns a
    ``conversation_id``; read back with ``GET /api/agents/{id}/flow/trace``.
[4] Captured live off the LiveKit data channel (``{kind:"signal"}`` /
    ``{kind:"metadata"}`` frames). ``GET /api/calls/{call_id}/trace`` (PR #636)
    serves the same two sections for a call that has a persisted ``calls`` row;
    a bench voice room has no such row (nothing calls the session-save path), so
    the data channel is the primary source here and the HTTP fetch is the
    fallback offered by :meth:`TraceClient.call_trace` for runs that do have a
    call id. ``source`` on the section says which one produced it.
"""
from __future__ import annotations

import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

SCHEMA = "tau2.health.diagnostics/v1"

# ── canonical availability reasons ──────────────────────────────────────────────

REASON_TEXT_MODE = (
    "text mode — these are VOICE-pipeline signals and do not exist for a text run "
    "(absence, not zero)"
)
REASON_BENCH_ENDPOINT = (
    "driven over POST /api/bench/agent-turn, a stateless brain call: it runs no flow "
    "engine and mints no conversation row, so no flow trace exists for this case"
)
REASON_NO_FLOW = (
    "the agent under test has no conversation flow attached, so the state machine "
    "never ran"
)
REASON_NO_CONVERSATION_ID = (
    "no conversation id was returned for this session, so the accumulated flow trace "
    "could not be addressed"
)
REASON_FETCH_FAILED = "flow-trace fetch failed"
REASON_TRACE_EMPTY = (
    "the trace endpoint answered but returned no steps (the backend may not persist a "
    "trace for this transport yet)"
)
REASON_NOT_A_WRITE_BENCHMARK = (
    "this benchmark issues no writes, so there is no said-vs-emitted-vs-landed "
    "distinction to record"
)
REASON_NO_JUDGE = "this benchmark grades deterministically — no judge LLM is called"

# Step kinds the flow engine emits. Counted explicitly so a kind that stops being
# emitted shows up as a 0 next to its peers rather than silently vanishing.
FLOW_STEP_KINDS = (
    "state_enter",
    "transition_check",
    "say_emitted",
    "tools_gated",
    "var_set",
    "guard_trip",
    "state_divergence",
    "flow_end",
)

# Where a flow variable's value came from — the engine stamps this on ``var_set``.
VAR_SOURCES = ("tool_result", "extraction", "goal_complete")

# Bound any single captured value so one fat tool result cannot balloon 100 case
# files. Mirrors services/call_trace.VALUE_MAX_CHARS on the backend.
VALUE_MAX_CHARS = 2000


def _clip(value: Any, max_chars: int = VALUE_MAX_CHARS) -> Any:
    """Bound one captured value. Scalars pass through; strings truncate; containers
    are kept as-is unless their rendered form is oversized, in which case the
    truncated rendering is kept (a truncated string is honest; a dropped field is
    not)."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= max_chars else value[: max_chars - 1] + "…"
    try:
        rendered = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001 — capture never raises
        return str(value)[:max_chars]
    if len(rendered) <= max_chars:
        return value
    return {"_truncated": True, "_rendered": rendered[: max_chars - 1] + "…"}


def _pctl(values: list, q: float) -> Optional[float]:
    """Nearest-rank percentile of a numeric list (None on empty)."""
    xs = sorted(v for v in values if isinstance(v, (int, float)))
    if not xs:
        return None
    return xs[min(len(xs) - 1, max(0, round(q * (len(xs) - 1))))]


# ── section builders ────────────────────────────────────────────────────────────


def unavailable(reason: str, **payload_keys: Any) -> dict[str, Any]:
    """An explicitly-absent section.

    Every payload field is ``None``, never ``[]``/``0``/``{}`` — see the module
    docstring's honesty rule. ``payload_keys`` lets a caller null out the extra
    fields its section normally carries."""
    section: dict[str, Any] = {
        "available": False,
        "reason": reason,
        "source": None,
        "turns": None,
        "summary": None,
    }
    section.update({k: None for k in payload_keys})
    return section


def flow_section(
    steps: Optional[list[dict[str, Any]]],
    *,
    source: str,
    current_state: Optional[str] = None,
    flow_spec: Optional[dict[str, Any]] = None,
    conversation_id: Optional[str] = None,
) -> dict[str, Any]:
    """The flow-trace section from an accumulated step list.

    ``steps`` is the raw ``GET /api/agents/{id}/flow/trace`` ``steps`` array (or
    the concatenation of per-turn ``flow.steps`` blocks). Empty/missing yields an
    unavailable section stamped :data:`REASON_TRACE_EMPTY` — a flow that ran and a
    trace that was never persisted must not look alike.

    Beyond the verbatim steps we derive the things a reader actually asks:
    which states were visited, which transition FIRED and with what ``reason``
    (the rationale added in #650), every variable write with its ``source``, the
    tool gates, the guard trips, the divergences, and whether the flow ended."""
    if not steps:
        return flow_unavailable(REASON_TRACE_EMPTY)

    valid = [s for s in steps if isinstance(s, dict)]
    valid.sort(key=lambda s: (s.get("seq") if isinstance(s.get("seq"), int) else 0))

    counts = {kind: 0 for kind in FLOW_STEP_KINDS}
    states: list[str] = []
    transitions: list[dict[str, Any]] = []
    var_sets: list[dict[str, Any]] = []
    guard_trips: list[dict[str, Any]] = []
    divergences: list[dict[str, Any]] = []
    tools_gated: list[dict[str, Any]] = []
    says: list[dict[str, Any]] = []
    ended = False
    end_step: Optional[dict[str, Any]] = None

    for step in valid:
        kind = step.get("kind")
        if kind in counts:
            counts[kind] += 1
        else:
            counts[str(kind)] = counts.get(str(kind), 0) + 1
        if kind == "state_enter" and isinstance(step.get("state"), str):
            states.append(step["state"])
        elif kind == "transition_check":
            transitions.append({
                "turn": step.get("turn"),
                "seq": step.get("seq"),
                "from": step.get("from") or step.get("state"),
                "to": step.get("to"),
                "result": step.get("result"),
                # The rationale the engine now records for WHY a transition did or
                # did not fire (#650). Without it a non-firing transition is an
                # unexplained dead end in the trace.
                "reason": step.get("reason"),
                "condition": _clip(step.get("condition")),
            })
        elif kind == "var_set":
            var_sets.append({
                "turn": step.get("turn"),
                "seq": step.get("seq"),
                "name": step.get("name") or step.get("var"),
                "value": _clip(step.get("value")),
                # tool_result | extraction | goal_complete — a variable the ENGINE
                # derived and one the CALLER stated are different evidence.
                "source": step.get("source"),
            })
        elif kind == "guard_trip":
            guard_trips.append({k: _clip(v) for k, v in step.items()})
        elif kind == "state_divergence":
            divergences.append({k: _clip(v) for k, v in step.items()})
        elif kind == "tools_gated":
            tools_gated.append({k: _clip(v) for k, v in step.items()})
        elif kind == "say_emitted":
            says.append({"turn": step.get("turn"), "seq": step.get("seq"),
                         "state": step.get("state"), "text": _clip(step.get("text"))})
        elif kind == "flow_end":
            ended = True
            end_step = {k: _clip(v) for k, v in step.items()}

    fired = [t for t in transitions if t.get("result") in (True, "fired", "taken")]
    declared_states = [s.get("id") for s in ((flow_spec or {}).get("states") or [])
                       if isinstance(s, dict)]
    unvisited = [s for s in declared_states if s not in set(states)]

    return {
        "available": True,
        "reason": None,
        "source": source,
        "conversation_id": conversation_id,
        "current_state": current_state,
        "start_state": (flow_spec or {}).get("start_state"),
        "final_state": states[-1] if states else current_state,
        "ended": ended,
        "flow_end": end_step,
        "num_steps": len(valid),
        "step_counts_by_kind": counts,
        "states_visited": states,
        "states_declared": declared_states or None,
        "states_unvisited": unvisited if declared_states else None,
        "transitions": transitions,
        "transitions_fired": fired,
        "var_sets": var_sets,
        "var_sources": dict(Counter(v.get("source") for v in var_sets
                                    if v.get("source"))),
        "guard_trips": guard_trips,
        "state_divergences": divergences,
        "tools_gated": tools_gated,
        "says": says,
        "steps": valid,
    }


def flow_unavailable(reason: str) -> dict[str, Any]:
    """A flow section for a case whose transport runs no flow engine (or whose
    trace could not be read). Payload fields are ``None``, never empty lists."""
    return unavailable(
        reason,
        conversation_id=None, current_state=None, start_state=None,
        final_state=None, ended=None, flow_end=None, num_steps=None,
        step_counts_by_kind=None, states_visited=None, states_declared=None,
        states_unvisited=None, transitions=None, transitions_fired=None,
        var_sets=None, var_sources=None, guard_trips=None, state_divergences=None,
        tools_gated=None, says=None, steps=None,
    )


def _signal_frames(turn: dict[str, Any]) -> list[dict[str, Any]]:
    frames = turn.get("signals")
    return [f for f in frames if isinstance(f, dict)] if isinstance(frames, list) else []


def signals_section(
    turns: Optional[list[dict[str, Any]]], *, source: str,
) -> dict[str, Any]:
    """The per-turn VOICE-signal section.

    ``turns`` is a list of ``{"turn": n, "signals": [...], ...}`` — either the
    harness's per-turn capture off the LiveKit data channel, or the ``signals.turns``
    array from ``GET /api/calls/{call_id}/trace``. Both are passed through verbatim
    (clipped) and rolled up.

    An EMPTY list is not "no signals": over voice it is the tell that the signal
    emitter or the metadata GPU is not live. So an empty capture stays
    ``available: true`` with a zeroed summary and an explicit
    ``emitted_nothing: true`` — a real measurement of nothing, distinct from the
    unavailable section a text run gets."""
    if turns is None:
        return signals_unavailable(REASON_TEXT_MODE)

    rows: list[dict[str, Any]] = []
    by_kind: Counter = Counter()
    hesitation_turns: list[Any] = []
    barge_ins: list[Any] = []
    latencies: list[float] = []
    completeness: list[float] = []
    shadow_turns: list[Any] = []
    speculative_tools: Counter = Counter()
    emotions: Counter = Counter()
    intents: Counter = Counter()
    total = 0
    turns_with = 0

    for turn in turns:
        if not isinstance(turn, dict):
            continue
        n = turn.get("turn", turn.get("n", turn.get("inference")))
        frames = _signal_frames(turn)
        if frames:
            turns_with += 1
        for frame in frames:
            total += 1
            kind = frame.get("signal") or frame.get("kind") or frame.get("type")
            if kind:
                by_kind[str(kind)] += 1
            if kind == "hesitation":
                hesitation_turns.append(n)
            if kind in ("shadow", "eager_reply", "shadow_reply"):
                shadow_turns.append(n)
            if kind in ("speculative", "speculative_tool"):
                name = frame.get("tool") or frame.get("name")
                if name:
                    speculative_tools[str(name)] += 1

        # Snapshot-shaped fields (the /api/calls/{id}/trace per-turn record, and the
        # equivalents the data channel stamps on a turn).
        for key in ("response_latency_ms", "latency_ms"):
            if isinstance(turn.get(key), (int, float)):
                latencies.append(float(turn[key]))
                break
        for key in ("turn_completeness", "completeness", "completeness_prob"):
            if isinstance(turn.get(key), (int, float)):
                completeness.append(float(turn[key]))
                break
        if turn.get("barge_in") or turn.get("barge_in_count"):
            barge_ins.append(n)
        final = turn.get("metadata_final")
        final = final if isinstance(final, dict) else {}
        emotion = turn.get("emotion") or final.get("emotion")
        if emotion:
            emotions[str(emotion)] += 1
        intent = turn.get("intent") or final.get("intent")
        if intent:
            intents[str(intent)] += 1

        rows.append({k: _clip(v) for k, v in turn.items()})

    return {
        "available": True,
        "reason": None,
        "source": source,
        "turns": rows,
        "summary": {
            "frames_total": total,
            "by_kind": dict(by_kind),
            "turns_captured": len(rows),
            "turns_with_signals": turns_with,
            "hesitation_turns": [t for t in hesitation_turns if t is not None],
            "shadow_turns": [t for t in shadow_turns if t is not None],
            "speculative_tools": dict(speculative_tools),
            "barge_in_turns": [t for t in barge_ins if t is not None],
            "response_latency_ms": {"p50": _pctl(latencies, 0.50),
                                    "p95": _pctl(latencies, 0.95),
                                    "n": len(latencies)},
            "turn_completeness": {"p50": _pctl(completeness, 0.50),
                                  "n": len(completeness)},
            "emotions_seen": dict(emotions),
            "intents_seen": dict(intents),
            # An available-but-silent capture over voice is a finding about the
            # signal pipeline, not about the agent. Say so, don't imply it.
            "emitted_nothing": total == 0,
        },
    }


def signals_unavailable(reason: str = REASON_TEXT_MODE) -> dict[str, Any]:
    return unavailable(reason)


def metadata_section(
    turns: Optional[list[dict[str, Any]]], *, source: str,
) -> dict[str, Any]:
    """The whissle-large metadata-sidecar section: per-interim emotion/intent (plus
    whatever else the head reports) captured per turn, and the settled final frame.

    Same availability rule as :func:`signals_section` — a text run has no acoustic
    metadata at all, and says so."""
    if turns is None:
        return metadata_unavailable(REASON_TEXT_MODE)

    rows: list[dict[str, Any]] = []
    frames_total = 0
    turns_with = 0
    emotions: Counter = Counter()
    intents: Counter = Counter()

    for turn in turns:
        if not isinstance(turn, dict):
            continue
        n = turn.get("turn", turn.get("n", turn.get("inference")))
        frames = turn.get("user_metadata")
        frames = [f for f in frames if isinstance(f, dict)] if isinstance(frames, list) else []
        if frames:
            turns_with += 1
        frames_total += len(frames)
        final = frames[-1] if frames else None
        if isinstance(final, dict):
            if final.get("emotion"):
                emotions[str(final["emotion"])] += 1
            if final.get("intent"):
                intents[str(final["intent"])] += 1
        rows.append({"turn": n, "frames": [_clip(f) for f in frames],
                     "final": _clip(final)})

    return {
        "available": True,
        "reason": None,
        "source": source,
        "turns": rows,
        "summary": {
            "frames_total": frames_total,
            "turns_captured": len(rows),
            "turns_with_metadata": turns_with,
            "emotions_seen": dict(emotions),
            "intents_seen": dict(intents),
            "emitted_nothing": frames_total == 0,
        },
    }


def metadata_unavailable(reason: str = REASON_TEXT_MODE) -> dict[str, Any]:
    return unavailable(reason)


def tool_call(
    name: Optional[str], *, arguments: Any = None, result: Any = None,
    ok: Optional[bool] = None, error: Optional[str] = None,
    turn: Any = None, call_id: Optional[str] = None, **extra: Any,
) -> dict[str, Any]:
    """One normalized tool record. ``ok`` defaults to "no error was reported"."""
    return {
        "turn": turn,
        "id": call_id,
        "name": name,
        "arguments": _clip(arguments if arguments is not None else {}),
        "result": _clip(result),
        "ok": (error is None) if ok is None else bool(ok),
        "error": error,
        **{k: _clip(v) for k, v in extra.items()},
    }


def tools_section(
    calls: list[dict[str, Any]], *, source: str,
    writes: Optional[dict[str, Any]] = None,
    writes_reason: str = REASON_NOT_A_WRITE_BENCHMARK,
) -> dict[str, Any]:
    """Tool forensics: what was called, with which resolved arguments, and what came
    back — plus, for a benchmark that WRITES, whether the write actually landed.

    ``writes`` is the said/emitted/landed block (MedAgentBench's ``IntegrityReport``
    fits it directly). A benchmark that issues no writes passes ``None`` and gets an
    explicit unavailable marker rather than a misleading zeroed block."""
    rows = [c for c in calls if isinstance(c, dict)]
    names = Counter(str(c.get("name")) for c in rows if c.get("name"))
    failed = [c for c in rows if c.get("ok") is False]
    return {
        "available": True,
        "reason": None,
        "source": source,
        "calls": rows,
        "summary": {
            "n_calls": len(rows),
            "by_name": dict(names),
            "n_ok": sum(1 for c in rows if c.get("ok") is True),
            "n_error": len(failed),
            "errors": [{"name": c.get("name"), "error": c.get("error")} for c in failed],
        },
        "writes": writes if writes is not None else {
            "available": False, "reason": writes_reason,
        },
    }


# ── provenance + cost ───────────────────────────────────────────────────────────


def _harness_commit() -> Optional[str]:
    """The harness commit this case ran at. Best-effort; None outside a git tree."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=5, check=False,
        )
        return out.stdout.strip() or None
    except Exception:  # noqa: BLE001 — provenance is best-effort
        return None


def provenance(
    benchmark: str, *, mode: str, transport_endpoint: str,
    agent_id: Optional[str] = None, base_url: Optional[str] = None,
    seed: Any = None, stratum: Optional[dict[str, Any]] = None,
    judge: Optional[dict[str, Any]] = None, run_id: Optional[str] = None,
    run_dir: Optional[str] = None, extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Per-case provenance, so a single case file is self-describing.

    Most of this also lives in the run's ``summary.json``; duplicating it onto the
    case is deliberate. A case file gets copied into a bug report, an issue or a
    slide on its own, and at that point "which agent, which judge, was the judge
    independent, which stratum was this case sampled into" must travel WITH it.

    ``judge`` is the adapter's judge block (``model_router.judge_provenance``
    output). A benchmark that grades deterministically passes ``None`` and the
    field records :data:`REASON_NO_JUDGE` instead of implying an ungraded run."""
    jud = dict(judge) if judge else None
    return {
        "benchmark": benchmark,
        "mode": mode,
        "transport_endpoint": transport_endpoint,
        "agent_id": agent_id,
        "base_url": base_url,
        "seed": seed,
        # The stratum this case was sampled INTO — the join key for "is the failure
        # concentrated in one severity band?", unanswerable from a flat score list.
        "stratum": stratum,
        "judge": jud or {"available": False, "reason": REASON_NO_JUDGE},
        "judge_provider": (jud or {}).get("judge_provider"),
        "judge_independent": (jud or {}).get("judge_independent"),
        "run_id": run_id,
        "run_dir": run_dir,
        "harness_commit": _harness_commit(),
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **(extra or {}),
    }


def cost_section(
    *, judge_calls: Optional[int] = None, judge_cost_usd: Optional[float] = None,
    agent_calls: Optional[int] = None, reason: Optional[str] = None,
    **extra: Any,
) -> dict[str, Any]:
    """Per-case spend. ``reason`` marks a benchmark that spends nothing on judging
    (deterministic graders) rather than reporting a misleading $0.00."""
    if reason:
        return {"available": False, "reason": reason, "judge_calls": None,
                "judge_cost_usd": None, "agent_calls": agent_calls}
    return {
        "available": True,
        "reason": None,
        "judge_calls": judge_calls,
        "judge_cost_usd": (round(float(judge_cost_usd), 6)
                           if judge_cost_usd is not None else None),
        "agent_calls": agent_calls,
        **extra,
    }


# ── the envelope ────────────────────────────────────────────────────────────────


def availability(sections: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Flat ``<name>_available`` / ``<name>_reason`` mirror of every section.

    The point is a one-lookup answer to "was this measured?" — the exact question a
    zeroed field would answer wrongly. ``signals_available: false`` beside
    ``signals_reason: "text mode …"`` cannot be misread as "no signals fired"."""
    flat: dict[str, Any] = {}
    for name, section in sections.items():
        flat[f"{name}_available"] = bool((section or {}).get("available"))
        flat[f"{name}_reason"] = (section or {}).get("reason")
    return flat


def build(
    *, benchmark: str, case_id: str, mode: str,
    flow: dict[str, Any], signals: dict[str, Any],
    metadata_sidecar: dict[str, Any], tools: dict[str, Any],
    provenance: dict[str, Any], cost: dict[str, Any],
    turns: Optional[list[dict[str, Any]]] = None,
    audio: Optional[dict[str, Any]] = None,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Assemble the :data:`SCHEMA` envelope one reader can consume across all three
    benchmarks."""
    sections = {"flow": flow, "signals": signals,
                "metadata_sidecar": metadata_sidecar, "tools": tools}
    return {
        "schema": SCHEMA,
        "benchmark": benchmark,
        "case_id": case_id,
        "mode": mode,
        "availability": availability(sections),
        "provenance": provenance,
        "cost": cost,
        "flow": flow,
        "signals": signals,
        "metadata_sidecar": metadata_sidecar,
        "tools": tools,
        "turns": turns,
        "audio": audio,
        **(extra or {}),
    }


def attach(record: dict[str, Any], diagnostics: dict[str, Any]) -> dict[str, Any]:
    """Put the envelope on a per-case record under ``"diagnostics"``.

    Additive on purpose: every existing key (``case_id``/``status``/``rubric_scores``/
    … or each benchmark's equivalent) is left exactly where it was, so the current
    report paths keep working while a new reader keys off one shared block."""
    record["diagnostics"] = diagnostics
    return record


def write_case(directory: str, case_id: str, record: dict[str, Any]) -> str:
    """Write one per-case record. Case ids can carry path separators; the filename
    is flattened. Returns the path."""
    os.makedirs(directory, exist_ok=True)
    safe = str(case_id).replace(os.sep, "_").replace("/", "_")
    path = os.path.join(directory, f"{safe}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, default=str, ensure_ascii=False)
    return path


# ── trace fetching ──────────────────────────────────────────────────────────────


class TraceClient:
    """Read the two server-side diagnostic surfaces.

    * ``GET /api/agents/{id}/flow/trace?conversation_id=…`` — the accumulated flow
      step trace. Populated for the TEXT channel (``/chat/turn``) and, since PR
      #613, for a voice session whose ``/api/bench/voice/start`` returned a
      ``conversation_id``. NOT populated for ``/api/bench/agent-turn``, which mints
      no conversation.
    * ``GET /api/calls/{call_id}/trace`` — per-turn flow state AND per-turn voice
      signals for a finished call (PR #636). Requires a persisted ``calls`` row,
      which a bench voice room does not create, so this is the fallback for runs
      that DO carry a call id; the primary voice source is the data channel.

    Every method returns ``None`` rather than raising, and the caller records the
    corresponding unavailable reason — a diagnostics fetch must never sink a case.
    """

    def __init__(self, base: Optional[str] = None, api_key: Optional[str] = None,
                 timeout: float = 30.0) -> None:
        self.base = (base or os.getenv("WHISSLE_BASE")
                     or "https://aws-gateway-backend.whissle.ai/bot").rstrip("/")
        self.api_key = api_key or os.getenv("WHISSLE_API_KEY") or ""
        self.timeout = timeout
        self.last_error: Optional[str] = None

    def _get(self, path: str) -> Optional[dict[str, Any]]:
        import requests

        if not self.api_key:
            self.last_error = "WHISSLE_API_KEY not set"
            return None
        try:
            r = requests.get(
                f"{self.base}{path}",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout,
            )
        except Exception as exc:  # noqa: BLE001 — diagnostics never sink a case
            self.last_error = f"{type(exc).__name__}: {exc}"
            return None
        if r.status_code >= 300:
            self.last_error = f"HTTP {r.status_code}: {r.text[:200]}"
            return None
        try:
            return r.json()
        except ValueError as exc:
            self.last_error = f"non-JSON trace response: {exc}"
            return None

    def flow_trace(self, agent_id: str,
                   conversation_id: str) -> Optional[dict[str, Any]]:
        self.last_error = None
        return self._get(
            f"/api/agents/{agent_id}/flow/trace?conversation_id={conversation_id}")

    def call_trace(self, call_id: str) -> Optional[dict[str, Any]]:
        self.last_error = None
        return self._get(f"/api/calls/{call_id}/trace")


def flow_from_trace_response(
    payload: Optional[dict[str, Any]], *, source: str,
    conversation_id: Optional[str] = None,
    flow_spec: Optional[dict[str, Any]] = None,
    fetch_error: Optional[str] = None,
) -> dict[str, Any]:
    """Turn a ``/flow/trace`` response into a flow section, degrading honestly."""
    if payload is None:
        return flow_unavailable(
            f"{REASON_FETCH_FAILED}: {fetch_error}" if fetch_error
            else REASON_FETCH_FAILED)
    return flow_section(
        list(payload.get("steps") or []),
        source=source,
        current_state=payload.get("current_state"),
        flow_spec=flow_spec,
        conversation_id=conversation_id,
    )


def sections_from_call_trace(
    payload: Optional[dict[str, Any]], *, fetch_error: Optional[str] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """``GET /api/calls/{call_id}/trace`` → ``(flow_section, signals_section)``.

    The backend's own sections carry ``available`` flags and degrade independently,
    so a call with signals but no flow (or vice versa) maps straight through."""
    if payload is None:
        reason = (f"call-trace fetch failed: {fetch_error}" if fetch_error
                  else "call-trace fetch failed")
        return flow_unavailable(reason), signals_unavailable(reason)

    src = "GET /api/calls/{call_id}/trace"
    flow_block = payload.get("flow") or {}
    sig_block = payload.get("signals") or {}

    if flow_block.get("available"):
        flow = flow_section(
            [step for turn in (flow_block.get("turns") or [])
             for step in (turn.get("steps") or [])] or None,
            source=src, current_state=flow_block.get("current_state"))
        # The backend pre-groups by turn; keep its grouping alongside our rollups.
        flow["turns"] = flow_block.get("turns")
    else:
        flow = flow_unavailable(
            "the call has no persisted flow trace (flowless agent, or a call that "
            "predates flow tracing)")

    if sig_block.get("available"):
        signals = signals_section(list(sig_block.get("turns") or []), source=src)
    else:
        signals = signals_unavailable(
            "the call has no persisted per-turn signals (a call that predates signal "
            "capture, or a run with the signal emitter off)")
    return flow, signals
