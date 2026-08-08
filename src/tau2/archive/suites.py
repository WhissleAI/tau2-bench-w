# Copyright Sierra
"""One binding per suite: how each runner's locals map onto the shared record.

Every suite calls the same :func:`~tau2.archive.writer.export_run`, but each has to
answer the same questions from a different set of local variables — where the output
went, what the config was, which records are the cases, and, above all, which
modality it just drove. Putting that translation in the runners would mean six
copies of the same twenty lines drifting apart; putting it here means each runner's
diff is one call, and the suite-specific knowledge sits in one file next to the
writer it feeds.

**Every function here is failure-tolerant by construction.** They are called after a
benchmark has finished and written its own results. If archiving fails — a full disk,
a permissions problem, an unreachable backend for the environment probe — the run's
results under ``results/`` are already safe, and losing them to a copy step would be
absurd. So each binding goes through :func:`~tau2.archive.writer.export_hook`, which
catches everything and prints to stderr.

The modality argument is hardcoded per suite ONLY where the suite has exactly one
transport (MedAgentBench is text and cannot be anything else). Where a suite can be
driven either way — AgentClinic, PatientAgentBench, flow-sim, flow-mutation — it is
passed through from the runner's own mode flag, because the runner is the only thing
that knows what it just did.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .schema import (
    MODALITY_TEXT,
    MODALITY_TEXT_VISION,
    MODALITY_VOICE,
    MODALITY_VOICE_VISION,
    NOT_RECORDED,
    RequestedRecord,
)
from .writer import ArchiveResult, export_hook

Result = Optional[ArchiveResult]


def now() -> datetime:
    """The clock, for runners that want to stamp their own start time."""
    return datetime.now(timezone.utc)


def modality_for(mode: Optional[str], *, vision: Any = None) -> str:
    """Map a runner's own ``--mode`` flag onto the archive's modality vocabulary.

    Deliberately strict: an unrecognised mode raises rather than defaulting to text.
    Silently defaulting is how a voice run gets archived as text, which is the exact
    error this whole field exists to prevent.
    """
    from .schema import ArchiveError

    has_vision = bool(vision) and str(vision).lower() not in ("off", "none", "false")
    value = str(mode or "").strip().lower()
    if value in ("voice", "audio", "livekit"):
        return MODALITY_VOICE_VISION if has_vision else MODALITY_VOICE
    if value in ("text", "harness", "native", "harness_tools", "agent_tools",
                 "brain-parity", "chat"):
        return MODALITY_TEXT_VISION if has_vision else MODALITY_TEXT
    raise ArchiveError(
        f"cannot map runner mode {mode!r} onto a modality. Add it to "
        "tau2.archive.suites.modality_for rather than letting it default — a wrong "
        "default here is how a text run gets published as a voice result."
    )


# ── MedAgentBench ──────────────────────────────────────────────────────────────

_medagent = export_hook("medagentbench", MODALITY_TEXT)


def archive_medagent_run(
    *, run_dir: Path, summary: dict, results: list, ledger: Any,
    brain: Any, run_name: Optional[str] = None,
    started_at: Optional[datetime] = None,
) -> Result:
    """MedAgentBench is text-only: it drives ``POST /api/bench/agent-turn``, a
    stateless brain call with no audio path anywhere in the suite."""
    meta = summary.get("run") or {}
    overall = summary.get("overall") or {}
    filters = meta.get("filters") or {}
    return _medagent(
        arm=run_dir.name,
        run_dir=run_dir,
        config=meta,
        summary=summary,
        cases=[r.as_dict() for r in results if hasattr(r, "as_dict")],
        ledger=ledger,
        started_at=started_at,
        finished_at=now(),
        agent_id=meta.get("agent_id"),
        base_url=str(meta.get("base") or ""),
        headline={
            "key": "success_rate", "label": "Success rate",
            "value": overall.get("success_rate_pct"), "unit": "pct",
            "n": overall.get("n"),
            "formatted": f"{overall.get('success_rate_pct')}%",
        },
        requested=RequestedRecord(
            model=str(meta.get("model") or NOT_RECORDED),
            provider="whissle",
            judge_provider=str(meta.get("grader") or NOT_RECORDED),
            # Deterministic grading — the independence question does not arise.
            judge_independent=None,
        ),
        reproduce_command=(
            "python -m tau2.health.medagent.run run "
            f"--mode {summary.get('mode', 'brain-parity')} "
            f"--limit {filters.get('limit', '?')} "
            f"--write-check {meta.get('write_check', 'execute')} "
            f"--max-round {meta.get('max_round', 8)} "
            f"--system-mode {meta.get('system_mode', 'neutral')}"
            + (f" --model {meta['model']}" if meta.get("model")
               and meta["model"] != "(agent default)" else "")
            + (f" --run-name {run_name}" if run_name else "")
        ),
        reproduce_notes=[
            "Needs a MedAgentBench FHIR server at "
            f"`{meta.get('fhir_api_base', NOT_RECORDED)}`.",
            "Grading is deterministic; no judge model is involved.",
        ],
    )


# ── AgentClinic ────────────────────────────────────────────────────────────────

_agentclinic = export_hook("agentclinic", MODALITY_TEXT)


def archive_agentclinic_run(
    *, run_dir: Path, meta: dict, summary: dict, cases: list, ledger: Any,
    mode: str, started_at: Optional[datetime] = None,
    arm: Optional[str] = None,
) -> Result:
    """AgentClinic can run over text OR over a LiveKit voice room, so its modality
    comes from the runner's own ``--mode``, never from a default."""
    return _agentclinic(
        arm=arm or run_dir.name,
        modality=modality_for(mode, vision=summary.get("vision") or meta.get("vision")),
        run_dir=run_dir,
        config=meta,
        summary=summary,
        cases=cases,
        ledger=ledger,
        started_at=started_at,
        finished_at=now(),
        agent_id=summary.get("agent_id") or meta.get("agent_id"),
        base_url=str(summary.get("base") or meta.get("base") or ""),
        headline={
            "key": "accuracy", "label": "Diagnostic accuracy",
            "value": (summary.get("accuracy") or 0) * 100,
            "unit": "pct", "n": summary.get("n_cases_scored"),
            "formatted": f"{(summary.get('accuracy') or 0) * 100:.1f}%",
        },
        requested=RequestedRecord(
            model=str(meta.get("model") or NOT_RECORDED),
            provider="whissle",
            judge_model=str(summary.get("judge_model") or NOT_RECORDED),
            judge_provider=str(summary.get("judge_provider") or NOT_RECORDED),
            judge_independent=summary.get("judge_independent"),
        ),
        reproduce_command=(
            "python -m tau2.health.agentclinic.run "
            f"--dataset {summary.get('dataset', 'MedQA')} "
            f"--limit {summary.get('limit', '?')} "
            f"--sample {summary.get('sample', 'head')} "
            f"--seed {summary.get('seed', 42)} "
            f"--mode {mode} "
            f"--prompt-mode {summary.get('prompt_mode', 'override')} "
            f"--protocol {summary.get('protocol', 'markers')} "
            f"--history {summary.get('history', 'agentclinic')} "
            f"--total-inferences {summary.get('total_inferences', 20)} "
            f"--judge-provider {summary.get('judge_provider', 'whissle')}"
        ),
        reproduce_notes=[
            f"Upstream: {summary.get('upstream', NOT_RECORDED)}",
            summary.get("judge_independence_note") or "",
        ],
    )


# ── PatientAgentBench ──────────────────────────────────────────────────────────

_patientagent = export_hook("patientagentbench", MODALITY_TEXT)


def archive_patientagent_run(
    *, run_dir: Path, summary: dict, outcomes: list, ledger: Any,
    mode: str, judge: Optional[dict] = None,
    started_at: Optional[datetime] = None,
) -> Result:
    """PatientAgentBench runs ``harness``/``native`` over text and has a voice slice,
    so its modality is the runner's ``--mode``."""
    sampling = summary.get("sampling") or {}
    prov = summary.get("provenance") or {}
    judge = judge or summary.get("judge") or {}
    return _patientagent(
        arm=run_dir.name,
        modality=modality_for(mode),
        run_dir=run_dir,
        config={
            "mode": summary.get("mode"), "label": summary.get("label"),
            "sampling": sampling, "weights": summary.get("weights"),
            "pass_threshold": summary.get("pass_threshold"), "judge": judge,
        },
        summary=summary,
        cases=outcomes,
        ledger=ledger,
        started_at=started_at,
        finished_at=now(),
        agent_id=prov.get("whissle_agent_id"),
        base_url=str(prov.get("whissle_base") or ""),
        headline={
            "key": "aggregate", "label": "Weighted rubric aggregate",
            "value": summary.get("aggregate"), "unit": "score",
            "n": summary.get("n_scored"),
            "formatted": f"{summary.get('aggregate')}",
        },
        requested=RequestedRecord(
            model=str(summary.get("label") or NOT_RECORDED),
            provider="whissle",
            judge_model=str(judge.get("model") or NOT_RECORDED),
            judge_provider=str(judge.get("provider") or NOT_RECORDED),
            judge_independent=summary.get("judge_independent"),
        ),
        reproduce_command=(
            "python -m tau2.health.patientagent.cli run "
            f"--limit {sampling.get('n_requested', '?')} "
            f"--seed {sampling.get('seed', 42)} "
            f"--mode {mode} --name {run_dir.name}"
        ),
        reproduce_notes=[
            "PatientAgentBench needs its own virtualenv (langchain 1.x).",
            f"Excluded {summary.get('n_excluded', 0)} of {summary.get('n_total', 0)} "
            "case(s) — the headline describes the survivors.",
        ],
    )


# ── tau2 flow-sim ──────────────────────────────────────────────────────────────

_flow_sim = export_hook("tau2_flow", MODALITY_VOICE)


def archive_flow_sim_run(
    *, out_dir: Path, summary: dict, results: list, mode: str,
    agent_type: str, sessions: Any = None, base_url: str = "",
    user_sim: Any = None, started_at: Optional[datetime] = None,
) -> Result:
    """flow-sim is the ONE suite that really does drive audio, so its modality is
    load-bearing rather than a formality: a voice run here has `.wav` files beside
    every session and voice-pipeline signals in its trace."""
    ledger = None
    # The user simulator and the two judges bill through POST /api/models/chat, which
    # returns cost_usd directly — dollars, not tokens, so they go in as dollars.
    if user_sim is not None and getattr(user_sim, "total_cost_usd", 0):
        from .serving import SOURCE_MODELS_CHAT, ServingLedger

        ledger = ServingLedger(source=SOURCE_MODELS_CHAT)
        ledger.record_usd("user_simulator_and_judges", float(user_sim.total_cost_usd))

    ran = summary.get("sessions_ran") or summary.get("sessions") or 0
    success = summary.get("task_success") or 0
    return _flow_sim(
        arm=f"{out_dir.parent.name}-{agent_type}",
        modality=modality_for(mode),
        run_dir=out_dir,
        config={
            "agent_type": agent_type, "mode": mode,
            "sessions": sessions if sessions is not None else summary.get("sessions"),
            "coverage": summary.get("coverage"),
        },
        summary=summary,
        cases=results,
        ledger=ledger,
        started_at=started_at,
        finished_at=now(),
        base_url=base_url,
        headline={
            "key": "task_success", "label": "Task success",
            "value": (100.0 * success / ran) if ran else None, "unit": "pct", "n": ran,
            "formatted": f"{(100.0 * success / ran):.1f}%" if ran else "—",
        },
        requested=RequestedRecord(provider="whissle"),
        reproduce_command=(
            "python -m tau2.flow.simulate run "
            f"--agent-type {agent_type} --sessions {summary.get('sessions', '?')} "
            f"--mode {mode}"
        ),
        reproduce_notes=[
            "Voice sessions leave `.bot.wav` / `.caller.wav` / `.mix.wav` beside each "
            "session record — that audio is the evidence the run was really voice.",
        ],
    )


# ── flow-mutation ──────────────────────────────────────────────────────────────

_flow_mutation = export_hook("flow_mutation", MODALITY_TEXT)


def archive_flow_mutation_run(
    *, out_dir: Path, report: dict, results: list, mode: str,
    agent_type: str, base_url: str = "", started_at: Optional[datetime] = None,
) -> Result:
    total = report.get("mutations") or len(results)
    passed = report.get("passed")
    return _flow_mutation(
        arm=agent_type,
        modality=modality_for(mode),
        run_dir=out_dir,
        config={
            "agent_type": agent_type, "mode": mode, "mutations": total,
            "skipped_kinds": report.get("skipped_kinds"),
        },
        summary=report,
        cases=results,
        started_at=started_at,
        finished_at=now(),
        base_url=base_url,
        headline={
            "key": "mutations_detected", "label": "Mutations detected",
            "value": (100.0 * passed / total) if total and passed is not None else None,
            "unit": "pct", "n": total,
            "formatted": (f"{(100.0 * passed / total):.1f}%"
                          if total and passed is not None else "—"),
        },
        requested=RequestedRecord(provider="whissle"),
        reproduce_command=(
            "python -m tau2.flow.mutation_suite run "
            f"--agent-type {agent_type} --mode {mode}"
        ),
        reproduce_notes=[
            "A mutation suite measures SENSITIVITY: each mutation perturbs the flow "
            "and the run passes when the perturbation is detected. A high score means "
            "the harness notices changes, not that the agent performs well.",
        ],
    )


# ── flow-defaults ──────────────────────────────────────────────────────────────

_flow_defaults = export_hook("flow_defaults", MODALITY_TEXT)


def archive_flow_defaults_run(
    *, out_dir: Path, summary: dict, results: list,
    base_url: str = "", started_at: Optional[datetime] = None,
) -> Result:
    totals = summary.get("totals") or {}
    types = totals.get("types") or 0
    return _flow_defaults(
        arm="default-flow-conformance",
        run_dir=out_dir,
        config={"suite": "flow_defaults", "totals": totals},
        summary=summary,
        cases=results,
        started_at=started_at,
        finished_at=now(),
        base_url=base_url,
        headline={
            "key": "types_passing", "label": "Agent types passing",
            "value": (100.0 * (totals.get("pass") or 0) / types) if types else None,
            "unit": "pct", "n": types,
            "formatted": (f"{(100.0 * (totals.get('pass') or 0) / types):.1f}%"
                          if types else "—"),
        },
        requested=RequestedRecord(provider="whissle"),
        reproduce_command="python -m tau2.flow.defaults run",
        reproduce_notes=[
            "A conformance check over every agent type's DEFAULT flow: it asserts the "
            "flow attaches and drives, not that the agent is good.",
        ],
    )
