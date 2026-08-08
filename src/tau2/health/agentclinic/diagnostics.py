# Copyright Sierra
"""AgentClinic → the shared per-case diagnostic envelope.

AgentClinic is the one health benchmark that already runs over the REAL voice
pipeline (``--mode voice``), so it is the one place where the full signal set
genuinely exists — and the place where conflating it with a text run would do the
most damage. The two modes therefore produce visibly different envelopes:

``--mode text``   ``POST /api/bench/agent-turn``. Stateless brain call: no flow
                  engine, no conversation, no audio. Flow / signals / metadata are
                  marked unavailable with their reasons; tool forensics are full
                  (the harness runs the measurement agent, so every test order and
                  its result is ours to record).
``--mode voice``  LiveKit bench room. Per-turn signals and whissle-large metadata
                  frames arrive on the data channel and ARE captured. The flow
                  section is still honest about a subtlety: bench voice connects
                  with ``real=False`` so the pipeline runs the harness's doctor
                  prompt rather than the deployed agent, and the deployed agent's
                  state machine is not what is under test. Where a
                  ``conversation_id`` came back we still attempt the trace fetch
                  and record whatever the backend actually has.
"""
from __future__ import annotations

from typing import Any, Optional

from tau2.health import diagnostics as diag

REASON_BENCH_VOICE_NO_FLOW = (
    "bench voice session (real=false): the pipeline runs the harness's doctor prompt "
    "and delegated tools, not the deployed agent's conversation flow, so no flow "
    "state machine ran for this case"
)

# The three clinic actions, named as the tools they are.
_ACTION_TOOL = {
    "test": "request_test",
    "image": "request_image",
    "diagnosis": "give_diagnosis",
}


def tool_calls_from_dialogue(dialogue: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One normalized tool record per doctor ACTION, paired with the result the
    harness handed back.

    The doctor's actions are its tools whether it spoke them as markers or emitted
    them as delegated tool calls, and the result line that follows in the dialogue
    is the tool's return value. Pairing them here is what turns "3 tests ordered"
    into "ordered a CBC and got back these values"."""
    out: list[dict[str, Any]] = []
    rows = [d for d in (dialogue or []) if isinstance(d, dict)]
    for i, row in enumerate(rows):
        if row.get("role") != "doctor":
            continue
        kind = row.get("kind")
        name = _ACTION_TOOL.get(kind)
        if not name:
            continue  # a plain question is not a tool call
        # The reader's / system's answer is the next non-doctor line.
        result = None
        for follow in rows[i + 1:]:
            if follow.get("role") == "doctor":
                break
            if follow.get("role") in ("measurement", "system"):
                result = follow.get("text")
                break
        out.append(diag.tool_call(
            name,
            arguments={"request": row.get("payload") or row.get("text")},
            result=result,
            ok=result is not None,
            error=None if result is not None else "no result was returned to the doctor",
            turn=row.get("inference"),
            via=row.get("via"),
            latency_ms=row.get("latency_ms"),
        ))
    return out


def _voice_turns(case: dict[str, Any]) -> Optional[list[dict[str, Any]]]:
    """The per-turn voice records, or None when this case did not run over voice.

    None (not ``[]``) is the point: it is what makes the sections downstream read
    "not measured" instead of "measured as nothing"."""
    voice = case.get("voice")
    if not isinstance(voice, dict):
        return None
    turns = voice.get("turns")
    return turns if isinstance(turns, list) else []


def build(
    case: dict[str, Any], *, meta: Optional[dict[str, Any]] = None,
    run_dir: Optional[str] = None,
    trace_client: Optional[diag.TraceClient] = None,
) -> dict[str, Any]:
    """The shared envelope for one AgentClinic case record."""
    meta = meta or {}
    mode = case.get("mode") or meta.get("mode") or "text"
    voice_turns = _voice_turns(case)
    voice_block = case.get("voice") if isinstance(case.get("voice"), dict) else {}
    conversation_id = voice_block.get("conversation_id")

    # ── flow ────────────────────────────────────────────────────────────────
    if mode != "voice":
        flow = diag.flow_unavailable(diag.REASON_BENCH_ENDPOINT)
    elif conversation_id and trace_client is not None:
        payload = trace_client.flow_trace(meta.get("agent_id") or "", conversation_id)
        flow = diag.flow_from_trace_response(
            payload, source="GET /api/agents/{id}/flow/trace",
            conversation_id=conversation_id,
            fetch_error=trace_client.last_error)
        if not flow.get("available"):
            # Nothing persisted is the EXPECTED outcome for a bench-mode room; say
            # which of the two it was rather than leaving a bare fetch error.
            flow["reason"] = f"{REASON_BENCH_VOICE_NO_FLOW} ({flow['reason']})"
    else:
        flow = diag.flow_unavailable(REASON_BENCH_VOICE_NO_FLOW)

    # ── voice signals + metadata sidecar ────────────────────────────────────
    if voice_turns is None:
        signals = diag.signals_unavailable(diag.REASON_TEXT_MODE)
        metadata = diag.metadata_unavailable(diag.REASON_TEXT_MODE)
    else:
        src = "LiveKit data channel (bench voice room)"
        signals = diag.signals_section(voice_turns, source=src)
        metadata = diag.metadata_section(voice_turns, source=src)

    # ── tools ───────────────────────────────────────────────────────────────
    tools = diag.tools_section(
        tool_calls_from_dialogue(case.get("dialogue") or []),
        source=("delegated tool calls over the voice data channel"
                if mode == "voice" else
                "doctor actions parsed from /api/bench/agent-turn replies"),
    )

    judge = {k: meta[k] for k in
             ("judge_provider", "judge_model", "judge_endpoint", "judge_independent",
              "judge_independence_note")
             if k in meta} or None

    return diag.build(
        benchmark="agentclinic",
        case_id=str(case.get("scenario_id")),
        mode=mode,
        flow=flow,
        signals=signals,
        metadata_sidecar=metadata,
        tools=tools,
        provenance=diag.provenance(
            "agentclinic",
            mode=mode,
            transport_endpoint=("POST /api/bench/voice/start (LiveKit)"
                                if mode == "voice" else "POST /api/bench/agent-turn"),
            agent_id=meta.get("agent_id"),
            base_url=meta.get("base"),
            seed=meta.get("seed"),
            stratum={"dataset": case.get("dataset") or meta.get("dataset"),
                     "scenario_index": case.get("scenario_index"),
                     "selection": meta.get("sample"),
                     "limit": meta.get("limit")},
            judge=judge,
            run_dir=run_dir,
            extra={
                "protocol": meta.get("protocol"),
                "history": meta.get("history"),
                "prompt_mode": meta.get("prompt_mode"),
                "vision": case.get("vision") or meta.get("vision"),
                "agent_type": meta.get("agent_type"),
                "total_inferences": meta.get("total_inferences"),
                "inferences_used": case.get("inferences_used"),
                "support_llm": case.get("support_llm") or meta.get("support_llm"),
                "patient_bias": meta.get("patient_bias"),
                "doctor_bias": meta.get("doctor_bias"),
                # Set on the slice of a text run re-driven through the real voice
                # pipeline (--voice-subset), so the two populations can never be
                # averaged together by accident.
                "voice_subset": case.get("voice_subset", False),
            },
        ),
        cost=diag.cost_section(
            judge_calls=case.get("support_llm_calls"),
            judge_cost_usd=case.get("support_llm_cost_usd"),
            agent_calls=case.get("inferences_used"),
        ),
        turns=voice_turns if voice_turns is not None else case.get("doctor_turns"),
        audio=case.get("audio"),
    )
