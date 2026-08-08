# Copyright Sierra
"""PatientAgentBench → the shared per-case diagnostic envelope.

PatientAgentBench runs inside upstream's own harness, so this adapter never sees a
turn loop it controls — it sees a serialized conversation afterwards. Everything
diagnostic therefore has to survive the round trip through their message objects,
which is why the voice agent stamps its per-turn signals onto
``response_metadata`` (``voice_agent.invoke``) and ``collect.extract_transcript``
lifts them back off.

Availability by ``--mode``:

``harness`` / ``native``  ``POST /api/bench/agent-turn``. Stateless brain call —
                          no flow engine, no conversation row, no audio. Flow,
                          signals and metadata are marked unavailable with their
                          reasons; tool calls are fully captured because the
                          harness executes the 15 sandbox tools itself.
``voice``                 LiveKit room. Per-turn signals and whissle-large
                          metadata frames ARE captured. A ``conversation_id``
                          comes back from ``/api/bench/voice/start``, so where one
                          exists the accumulated flow trace is fetched too.
"""
from __future__ import annotations

from typing import Any, Optional

from tau2.health import diagnostics as diag

# The upstream mode labels, mapped to the transport that actually carried the run.
MODE_TRANSPORT = {
    "harness": "POST /api/bench/agent-turn",
    "native": "POST /api/bench/agent-turn",
    "voice": "POST /api/bench/voice/start (LiveKit)",
}


def voice_turns(transcript: list[dict[str, Any]]) -> Optional[list[dict[str, Any]]]:
    """The per-turn voice records lifted out of the transcript, or None when this
    case never ran over voice.

    None, not ``[]`` — a text case has no voice turns to be empty of, and the
    difference is the whole point of the availability contract."""
    rows = [e.get("voice") for e in (transcript or [])
            if isinstance(e, dict) and isinstance(e.get("voice"), dict)]
    if not rows:
        return None
    # The greeting entry carries kind="greeting" and no turn number; keep it, it is a
    # real spoken turn, but number it 0 so the rollups can order.
    out = []
    for i, row in enumerate(rows):
        turn = row.get("turn")
        out.append({**row, "turn": turn if isinstance(turn, int) else 0})
    return out


def tool_calls(raw_calls: list[dict[str, Any]],
               transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize the harness's tool records and pair each with its RESULT.

    ``collect.extract_transcript`` already separates the calls; what it could not
    say was what came back. The sandbox returns each result as a ``tool``-role
    message, so pairing by position recovers the half of the record that makes a
    workflow-accuracy score debuggable — "called ``book_appointment``" is not
    evidence, "called it with this slot and got back this error" is."""
    results: list[dict[str, Any]] = [
        e for e in (transcript or [])
        if isinstance(e, dict) and str(e.get("role")).lower() in ("tool", "function")
    ]
    out: list[dict[str, Any]] = []
    for i, call in enumerate(raw_calls or []):
        if not isinstance(call, dict):
            continue
        result = results[i].get("content") if i < len(results) else None
        text = str(result or "").lower()
        failed = bool(result) and ("error" in text[:80] or "not found" in text[:80])
        out.append(diag.tool_call(
            call.get("name"),
            arguments=call.get("args") or call.get("arguments") or {},
            result=result,
            ok=not failed if result is not None else None,
            error=result if failed else None,
            turn=call.get("turn_index"),
            call_id=call.get("id"),
        ))
    return out


def build(
    *, case_id: str, mode: str, transcript: list[dict[str, Any]],
    raw_tool_calls: list[dict[str, Any]],
    scenario: Optional[dict[str, Any]] = None,
    judge: Optional[dict[str, Any]] = None,
    sampling: Optional[dict[str, Any]] = None,
    provenance_extra: Optional[dict[str, Any]] = None,
    run_dir: Optional[str] = None,
    n_cases: int = 1,
    trace_client: Optional[diag.TraceClient] = None,
) -> dict[str, Any]:
    """The shared envelope for one PatientAgentBench case."""
    vturns = voice_turns(transcript)
    is_voice = vturns is not None
    conversation_id = next(
        (t.get("conversation_id") for t in (vturns or []) if t.get("conversation_id")),
        None)

    # ── flow ────────────────────────────────────────────────────────────────
    if not is_voice:
        flow = diag.flow_unavailable(diag.REASON_BENCH_ENDPOINT)
    elif conversation_id and trace_client is not None:
        payload = trace_client.flow_trace(
            (provenance_extra or {}).get("agent_id") or "", conversation_id)
        flow = diag.flow_from_trace_response(
            payload, source="GET /api/agents/{id}/flow/trace",
            conversation_id=conversation_id,
            fetch_error=trace_client.last_error)
    elif conversation_id is None:
        flow = diag.flow_unavailable(diag.REASON_NO_CONVERSATION_ID)
    else:
        flow = diag.flow_unavailable(
            "no trace client was configured for this run, so the accumulated flow "
            "trace was not fetched")

    # ── signals + metadata sidecar ──────────────────────────────────────────
    if not is_voice:
        signals = diag.signals_unavailable(diag.REASON_TEXT_MODE)
        metadata = diag.metadata_unavailable(diag.REASON_TEXT_MODE)
    else:
        src = "LiveKit data channel (voice session)"
        signals = diag.signals_section(vturns, source=src)
        metadata = diag.metadata_section(vturns, source=src)

    # Per-case share of the run's judge spend. The jury grades every rubric of every
    # session, so the run total divided by N is the honest per-case figure — labelled
    # as an allocation, not measured per case, so nobody reads it as exact.
    jud = judge or {}
    judge_calls = jud.get("judge_calls")
    judge_cost = jud.get("judge_cost_usd")
    n = max(1, int(n_cases or 1))

    stratum = None
    if scenario:
        stratum = {k: scenario.get(k) for k in
                   ("task_type", "severity_level", "scenario_complexity",
                    "condition_name", "personality", "preferred_care_option")
                   if k in scenario}
    if sampling:
        stratum = {**(stratum or {}),
                   "strata_keys": sampling.get("strata_keys"),
                   "n_selected": sampling.get("n_selected"),
                   "n_population": sampling.get("n_population")}

    return diag.build(
        benchmark="patientagentbench",
        case_id=case_id,
        mode=mode,
        flow=flow,
        signals=signals,
        metadata_sidecar=metadata,
        tools=diag.tools_section(
            tool_calls(raw_tool_calls, transcript),
            source=("the agent's own tools over the live voice session" if is_voice
                    else "PatientAgentBench's 15 sandbox tools, executed by the harness"),
        ),
        provenance=diag.provenance(
            "patientagentbench",
            mode=mode,
            transport_endpoint=MODE_TRANSPORT.get(mode, mode),
            agent_id=(provenance_extra or {}).get("agent_id"),
            base_url=(provenance_extra or {}).get("base_url"),
            seed=(sampling or {}).get("seed"),
            stratum=stratum,
            judge=judge,
            run_dir=run_dir,
            extra={k: v for k, v in (provenance_extra or {}).items()
                   if k not in ("agent_id", "base_url")},
        ),
        cost=diag.cost_section(
            judge_calls=(round(judge_calls / n, 2)
                         if isinstance(judge_calls, (int, float)) else None),
            judge_cost_usd=(judge_cost / n
                            if isinstance(judge_cost, (int, float)) else None),
            allocation=("run total ÷ N cases — the jury grades every rubric of every "
                        "session, so per-case spend is an allocation, not a "
                        "measurement"),
            run_judge_calls=judge_calls,
            run_judge_cost_usd=judge_cost,
            n_cases=n,
        ),
        turns=vturns,
    )
