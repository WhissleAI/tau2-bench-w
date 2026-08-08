# Copyright Sierra
"""MedAgentBench → the shared per-case diagnostic envelope.

MedAgentBench is the WRITE benchmark, so its tool forensics carry the distinction
the other two do not have to make: what the agent *said* it did, what it actually
*emitted* as a POST, and what the real FHIR server *accepted and kept*. That
three-way split already exists on the run (``integrity.IntegrityReport``); this
module lifts it into the shared ``tools.writes`` block so one reader sees it the
same way it sees AgentClinic's test orders.

Transport reality, recorded rather than implied: every MedAgentBench round is a
``POST /api/bench/agent-turn`` — a stateless brain call that runs no flow engine
and mints no conversation. There is therefore no flow trace and no voice signal to
capture for this benchmark today, and the envelope says exactly that instead of
emitting empty arrays.
"""
from __future__ import annotations

from typing import Any, Optional

from tau2.health import diagnostics as diag


def _observation_failed(observation: Optional[str]) -> bool:
    """Did the environment reject this action? Upstream signals both GET and POST
    failures in the observation text."""
    text = (observation or "").strip().lower()
    return text.startswith("error") or "invalid" in text[:40]


def tool_calls_from_turns(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One normalized tool record per GET/POST the agent issued.

    The arguments are the RESOLVED ones — the actual URL and the actual JSON body
    the agent produced, not a schema name — because "called POST" is not a
    debuggable fact and "POSTed this Observation body to this MRN" is."""
    out: list[dict[str, Any]] = []
    for turn in turns or []:
        if not isinstance(turn, dict):
            continue
        kind = (turn.get("action_kind") or "").upper()
        if kind not in ("GET", "POST"):
            continue
        observation = turn.get("observation")
        out.append(diag.tool_call(
            kind,
            arguments={"url": turn.get("url"), "payload": turn.get("payload")},
            result=observation,
            ok=not _observation_failed(observation),
            error=observation if _observation_failed(observation) else None,
            turn=turn.get("round"),
            latency_ms=turn.get("latency_ms"),
        ))
    return out


def writes_block(integrity: dict[str, Any]) -> dict[str, Any]:
    """The said / emitted / landed block, preserved verbatim from the integrity
    report plus a plain-language verdict.

    ``said_not_emitted`` is the headline: the agent told the clinician the order
    was placed and no POST ever left. Collapsing that into "tools called: 0" — the
    only thing the old record could express — is precisely the failure this block
    exists to keep visible."""
    return {
        "available": True,
        "reason": None,
        "said_action": integrity.get("said_action"),
        "said_evidence": integrity.get("said_evidence"),
        "emitted_writes": integrity.get("emitted_writes"),
        "attempted_writes": integrity.get("attempted_writes"),
        "accepted_by_ehr": integrity.get("accepted_by_ehr"),
        "rejected_by_ehr": integrity.get("rejected_by_ehr"),
        # "Landed" = the resource demonstrably exists in the EHR (created id, and a
        # read-back GET where the check mode allows one). Not "the POST returned 2xx".
        "landed_writes": integrity.get("verified_writes"),
        "nonconformant_writes": integrity.get("nonconformant_writes"),
        "write_check_mode": integrity.get("write_check_mode"),
        "said_not_emitted": integrity.get("said_not_emitted"),
        "emitted_not_said": integrity.get("emitted_not_said"),
        "emitted_not_accepted": integrity.get("emitted_not_accepted"),
        "emitted_nonconformant": integrity.get("emitted_nonconformant"),
        "attempts": integrity.get("write_attempts"),
        "verdict": _verdict(integrity),
    }


def _verdict(integrity: dict[str, Any]) -> str:
    if not integrity.get("is_action_category"):
        return "not a write task"
    if integrity.get("said_not_emitted"):
        return "SAID but never EMITTED — claimed a chart action that was never issued"
    if integrity.get("emitted_not_accepted"):
        return "EMITTED but NOT ACCEPTED — the EHR rejected the write"
    if integrity.get("emitted_writes") and not integrity.get("verified_writes"):
        return ("EMITTED, acceptance not verified — write-check mode did not confirm "
                "the resource landed")
    if integrity.get("verified_writes"):
        return "EMITTED and LANDED — confirmed present in the EHR"
    return "no write emitted"


def build(
    result_dict: dict[str, Any], *, run_meta: Optional[dict[str, Any]] = None,
    run_dir: Optional[str] = None,
) -> dict[str, Any]:
    """The shared envelope for one graded MedAgentBench task.

    ``result_dict`` is ``TaskResult.as_dict()`` — everything the run already knows
    about the task. ``run_meta`` is the run-level provenance block
    (``run.py``'s ``run_meta``), copied down per case so a case file travels alone.
    """
    meta = run_meta or {}
    integrity = result_dict.get("integrity") or {}
    turns = result_dict.get("turns") or []

    return diag.build(
        benchmark="medagentbench",
        case_id=str(result_dict.get("task_id")),
        mode="text",
        # No flow engine behind /api/bench/agent-turn — stated, not implied by an
        # empty step list.
        flow=diag.flow_unavailable(diag.REASON_BENCH_ENDPOINT),
        signals=diag.signals_unavailable(diag.REASON_TEXT_MODE),
        metadata_sidecar=diag.metadata_unavailable(diag.REASON_TEXT_MODE),
        tools=diag.tools_section(
            tool_calls_from_turns(turns),
            source="harness-executed FHIR actions (GET/POST) parsed from agent replies",
            writes=writes_block(integrity),
        ),
        provenance=diag.provenance(
            "medagentbench",
            mode="text",
            transport_endpoint=meta.get("endpoint") or "/api/bench/agent-turn",
            agent_id=meta.get("agent_id"),
            base_url=meta.get("base"),
            # MedAgentBench selects by category quota, not a seeded random draw —
            # there is no seed to record, and saying so beats a null that reads like
            # an omission.
            seed=None,
            stratum={"category": result_dict.get("category"),
                     "is_action_category": integrity.get("is_action_category"),
                     "selection": "category quota (deterministic), not seeded sampling"},
            judge=None,  # deterministic graders — see REASON_NO_JUDGE
            run_dir=run_dir,
            extra={
                "grader": meta.get("grader"),
                "system_mode": meta.get("system_mode"),
                "model": meta.get("model"),
                "max_round": meta.get("max_round"),
                "write_check": meta.get("write_check"),
                "fhir_api_base": meta.get("fhir_api_base"),
                "attempt": result_dict.get("attempt"),
            },
        ),
        cost=diag.cost_section(
            reason=diag.REASON_NO_JUDGE,
            agent_calls=len(turns),
        ),
        turns=turns,
    )
