# Copyright Sierra
"""Bring the runs that already exist into the archive.

Every result under ``results/`` predates the writer, so each suite needs a small
reader that maps its private artifact shape onto the shared record. That is the
whole of this module: seven readers, one per shape, each answering the same four
questions — when did it run, how was it driven, what served it, what did it score.

TWO RULES THIS MODULE EXISTS TO ENFORCE
---------------------------------------
**The date comes from the artifact, never from the filesystem.** Each reader knows
which field in its own format carries the run's timestamp: ``generated_at`` for
MedAgentBench, ``ts`` for AgentClinic and the flow suites, ``provenance.generated_at``
for PatientAgentBench, ``timestamp`` for the tau2 core runs. mtime is never
consulted. The concrete reason is in this tree: ``retail_run1.json`` has an mtime of
4 Aug and an internal ``timestamp`` of 31 Jul — a copy or a checkout moved the mtime
and left the run behind it. Backfilling from mtime would have silently redated four
runs by four days.

**A fact that cannot be recovered is written as "not recorded".** Older runs did not
capture the served model, token usage or cost, because the backend did not return
them. The readers below do not estimate, infer from the arm name, or reuse a sibling
run's value. They write :data:`~tau2.archive.schema.NOT_RECORDED`, and the manifest
says so where a reader will see it.
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from .schema import (
    DATE_FROM_ARTIFACT,
    DATE_FROM_DIRNAME,
    MODALITY_NOT_RECORDED,
    MODALITY_TEXT,
    MODALITY_TEXT_VISION,
    MODALITY_VOICE,
    MODALITY_VOICE_VISION,
    NOT_RECORDED,
    DateProvenance,
    RequestedRecord,
)
from .serving import from_records
from .writer import ArchiveResult, export_run

#: ``20260808T092952Z`` — the stamp the runners put in ``ts`` fields and directory
#: names. Parsed rather than pattern-matched loosely, so a near-miss fails visibly.
_TS_COMPACT = re.compile(r"(\d{8})T(\d{6})Z")


def _load(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _parse_compact(value: str) -> Optional[datetime]:
    m = _TS_COMPACT.search(str(value))
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def _parse_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _date_from(
    payload: Any, keys: tuple[str, ...], source_file: str, run_dir: Path
) -> tuple[DateProvenance, Optional[datetime]]:
    """Recover a run's date from its own artifact, then its directory name, then
    give up. mtime is deliberately not a step in this chain."""
    if isinstance(payload, dict):
        for key in keys:
            cur: Any = payload
            for part in key.split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
            moment = _parse_iso(cur) or _parse_compact(str(cur or ""))
            if moment:
                return (
                    DateProvenance(
                        date=moment.strftime("%Y-%m-%d"),
                        timestamp=moment.isoformat(timespec="seconds"),
                        source=DATE_FROM_ARTIFACT,
                        source_detail=f"{source_file}:{key} = {cur!r}",
                    ),
                    moment,
                )
    moment = _parse_compact(run_dir.name)
    if moment:
        return (
            DateProvenance(
                date=moment.strftime("%Y-%m-%d"),
                timestamp=moment.isoformat(timespec="seconds"),
                source=DATE_FROM_DIRNAME,
                source_detail=f"run directory name {run_dir.name!r}",
            ),
            moment,
        )
    return DateProvenance(), None


def _cases(directory: Path) -> list[Any]:
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.json")):
        record = _load(path)
        if record is not None:
            out.append(record)
    return out


def _headline(label: str, value: Any, unit: str = "pct", n: Optional[int] = None) -> Optional[dict]:
    if not isinstance(value, (int, float)):
        return None
    return {
        "key": label.lower().replace(" ", "_"),
        "label": label,
        "value": round(float(value), 2),
        "unit": unit,
        "n": n,
        "formatted": f"{value:.1f}%" if unit == "pct" else f"{value:.2f}",
    }


# ── per-suite readers ──────────────────────────────────────────────────────────
#
# Each returns the kwargs for export_run, or None when the directory is not in fact
# a run of that suite.


def read_medagentbench(run_dir: Path) -> Optional[dict[str, Any]]:
    summary = _load(run_dir / "SUMMARY.json")
    if not isinstance(summary, dict) or summary.get("suite") != "medagentbench":
        return None
    date, moment = _date_from(summary, ("generated_at",), "SUMMARY.json", run_dir)
    run = summary.get("run") or {}
    overall = summary.get("overall") or {}
    cases = _cases(run_dir / "tasks")
    return {
        "suite": "medagentbench",
        "arm": run_dir.name,
        # The endpoint is recorded in the artifact and is an HTTP text turn-taker;
        # this suite has no audio path at all.
        "modality": MODALITY_TEXT,
        "run_dir": run_dir,
        "config": summary.get("run"),
        "summary": summary,
        "cases": cases,
        "date": date,
        "started_at": moment,
        "headline": _headline(
            "Success rate", overall.get("success_rate_pct"), n=overall.get("n")
        ),
        "requested": RequestedRecord(
            model=str(run.get("model") or NOT_RECORDED),
            provider="whissle",
            judge_provider=str(summary.get("grader") or run.get("grader") or NOT_RECORDED),
            judge_independent=None,
        ),
        "agent_id": run.get("agent_id"),
        "base_url": str(run.get("base") or ""),
        "reproduce_command": (
            "python -m tau2.health.medagent.run run "
            f"--mode {summary.get('mode', 'brain-parity')} "
            f"--limit {(run.get('filters') or {}).get('limit', '?')} "
            f"--write-check {run.get('write_check', 'execute')} "
            f"--max-round {run.get('max_round', 8)} "
            f"--system-mode {run.get('system_mode', 'neutral')} "
            f"--run-name {run_dir.name}"
        ),
        "reproduce_notes": [
            "Requires a MedAgentBench FHIR server at "
            f"`{run.get('fhir_api_base', NOT_RECORDED)}`.",
            "Grading is deterministic — no judge model is involved, so the judge "
            "independence question does not arise for this suite.",
        ],
    }


def read_agentclinic(
    run_dir: Path, arm_models: Optional[dict[str, str]] = None
) -> Optional[dict[str, Any]]:
    summary = _load(run_dir / "SUMMARY.json")
    if not isinstance(summary, dict) or "accuracy" not in summary:
        return None
    meta = _load(run_dir / "RUN.json") or {}
    date, moment = _date_from(summary, ("ts",), "SUMMARY.json", run_dir)

    # This suite is the one that already recorded its transport honestly.
    mode = str(summary.get("mode") or meta.get("mode") or "").lower()
    vision = str(summary.get("vision") or meta.get("vision") or "off").lower() != "off"
    if mode == "voice":
        modality = MODALITY_VOICE_VISION if vision else MODALITY_VOICE
    elif mode == "text":
        modality = MODALITY_TEXT_VISION if vision else MODALITY_TEXT
    else:
        modality = MODALITY_NOT_RECORDED

    cases = _cases(run_dir / "cases")
    # AgentClinic is the only suite that already reached for `usage` per turn (it
    # wrote null every time, because the deployed endpoint did not return one yet).
    # Recovering a ledger from the cases turns those nulls into an honest
    # "not recorded" instead of an absent section.
    ledger = from_records(cases) if cases else None

    arm = re.sub(r"^\d{8}T\d{6}Z-", "", run_dir.name)
    # AgentClinic never wrote the agent's model into any artifact — `judge_model` is
    # the GRADER. The arm is legible only from the directory name, so the model is
    # resolved by cross-referencing the sweep's own aggregation, which does record
    # arm -> model. That is recovery from an artifact, not inference from a label:
    # where the aggregation is absent the field stays `not recorded`.
    resolved = (arm_models or {}).get(re.sub(r"^sweep25m?_", "", arm))
    model_extra: dict[str, Any] = {
        "model_recorded_in_artifacts": False,
        "model_note": (
            "AgentClinic writes no agent-model field — `judge_model` names the "
            "grader, not the agent under test. "
            + (
                f"The model shown was resolved by cross-referencing the arm label "
                f"{arm!r} against results/modelsweep/collected.json, which records "
                "arm -> model."
                if resolved else
                "No sweep aggregation was available to resolve the arm label, so the "
                "agent's model is not recorded for this run."
            )
        ),
    }

    n_total = summary.get("n_cases_total")
    n_scored = summary.get("n_cases_scored")
    partial_note = None
    if isinstance(n_total, int) and isinstance(n_scored, int) and n_total:
        if n_scored == 0:
            partial_note = (
                f"**This run produced no scored cases** (0 of {n_total}). It is a "
                "failed run archived for the record, not a result. Its headline is "
                "absent because there is nothing to report."
            )
        elif n_scored < n_total / 2:
            partial_note = (
                f"**Heavily incomplete: only {n_scored} of {n_total} case(s) scored.** "
                "The headline describes the survivors of a run that mostly did not "
                "complete, and should not be compared against a full run."
            )

    return {
        "suite": "agentclinic",
        "arm": arm,
        "modality": modality,
        "run_dir": run_dir,
        "config": meta or {k: v for k, v in summary.items() if k != "selected_ids"},
        "summary": summary,
        "cases": cases,
        "ledger": ledger if ledger and len(ledger) else None,
        "date": date,
        "started_at": moment,
        "headline": _headline(
            "Diagnostic accuracy",
            (summary.get("accuracy") or 0) * 100
            if isinstance(summary.get("accuracy"), float) and summary["accuracy"] <= 1
            else summary.get("accuracy"),
            n=summary.get("n_cases_scored"),
        ),
        "requested": RequestedRecord(
            model=str(meta.get("model") or summary.get("model") or resolved or NOT_RECORDED),
            provider="whissle",
            judge_model=str(summary.get("judge_model") or NOT_RECORDED),
            judge_provider=str(summary.get("judge_provider") or NOT_RECORDED),
            judge_independent=summary.get("judge_independent"),
            extra=model_extra,
        ),
        "notes": [partial_note] if partial_note else [],
        "agent_id": summary.get("agent_id") or meta.get("agent_id"),
        "base_url": str(summary.get("base") or ""),
        "reproduce_command": (
            "python -m tau2.health.agentclinic.run "
            f"--dataset {summary.get('dataset', 'MedQA')} "
            f"--limit {summary.get('limit', '?')} "
            f"--sample {summary.get('sample', 'head')} "
            f"--seed {summary.get('seed', 42)} "
            f"--prompt-mode {summary.get('prompt_mode', 'override')} "
            f"--protocol {summary.get('protocol', 'markers')} "
            f"--history {summary.get('history', 'agentclinic')} "
            f"--total-inferences {summary.get('total_inferences', 20)} "
            f"--judge-provider {summary.get('judge_provider', 'whissle')}"
            + (f" --judge-model {summary['judge_model']}" if summary.get("judge_model") else "")
        ),
        "reproduce_notes": [
            f"Upstream: {summary.get('upstream', NOT_RECORDED)}",
            summary.get("judge_independence_note") or "",
        ],
    }


def read_patientagentbench(run_dir: Path) -> Optional[dict[str, Any]]:
    summary = _load(run_dir / "summary.json")
    if not isinstance(summary, dict) or "aggregate" not in summary:
        return None
    prov = summary.get("provenance") or {}
    date, moment = _date_from(
        summary, ("provenance.generated_at",), "summary.json", run_dir
    )
    mode = str(summary.get("mode") or "").lower()
    modality = MODALITY_VOICE if "voice" in mode else (
        MODALITY_TEXT if mode else MODALITY_NOT_RECORDED)
    cases = _cases(run_dir / "cases")
    sampling = summary.get("sampling") or {}
    judge = summary.get("judge") or {}
    return {
        "suite": "patientagentbench",
        "arm": run_dir.name,
        "modality": modality,
        "run_dir": run_dir,
        "config": {
            "mode": summary.get("mode"),
            "label": summary.get("label"),
            "sampling": sampling,
            "weights": summary.get("weights"),
            "pass_threshold": summary.get("pass_threshold"),
            "judge": judge,
            "provenance": prov,
        },
        "summary": summary,
        "cases": cases,
        "date": date,
        "started_at": moment,
        "headline": _headline(
            "Weighted rubric aggregate", summary.get("aggregate"),
            unit="score", n=summary.get("n_scored"),
        ),
        "requested": RequestedRecord(
            model=str(summary.get("label") or NOT_RECORDED),
            provider="whissle",
            judge_model=str(judge.get("model") or summary.get("judge_model") or NOT_RECORDED),
            judge_provider=str(
                judge.get("provider") or summary.get("judge_provider") or NOT_RECORDED),
            judge_independent=summary.get("judge_independent"),
        ),
        "agent_id": prov.get("whissle_agent_id"),
        "base_url": str(prov.get("whissle_base") or ""),
        "reproduce_command": (
            "python -m tau2.health.patientagent.cli run "
            f"--cases data/pab_cases_120.json "
            f"--limit {sampling.get('n_requested', '?')} "
            f"--seed {sampling.get('seed', 42)} "
            f"--mode {summary.get('mode', 'harness')} "
            f"--name {run_dir.name}"
        ),
        "reproduce_notes": [
            "PatientAgentBench needs its own virtualenv (langchain 1.x).",
            f"Excluded {summary.get('n_excluded', 0)} of {summary.get('n_total', 0)} "
            f"case(s): {summary.get('excluded_breakdown')}. The headline is a "
            "statement about the survivors.",
        ],
    }


def read_flow_sim(run_dir: Path) -> Optional[dict[str, Any]]:
    summary = _load(run_dir / "SUMMARY.json")
    session_files = sorted(run_dir.glob("*.session.json"))

    if not isinstance(summary, dict) or "sessions" not in summary:
        # A flow-sim agent-type directory with session files but no SUMMARY.json is
        # still a run — `flow_sim_baseline/customer_support` is exactly this, and the
        # first version of this reader dropped it silently. Reconstruct the summary
        # from the sessions rather than skipping: a run whose rollup was never written
        # is missing a rollup, not missing.
        if not session_files or not run_dir.parent.name.startswith("flow_sim"):
            return None
        sessions = [s for s in (_load(p) for p in session_files) if s]
        stamps = sorted(
            m.group(0) for m in (_TS_COMPACT.search(p.name) for p in session_files) if m
        )
        summary = {
            "agent_type": run_dir.name,
            "sessions": len(sessions),
            "sessions_ran": len(sessions),
            "task_success": sum(
                1 for s in sessions
                if isinstance(s, dict) and s.get("outcome", {}).get("task_success")
            ) or None,
            "ts": stamps[0] if stamps else None,
            "_reconstructed": (
                "This SUMMARY was reconstructed by the archive from the run's own "
                "session files: the harness never wrote a SUMMARY.json for this "
                "directory. Session-level facts are the harness's; the rollup is the "
                "archive's arithmetic over them."
            ),
        }

    date, moment = _date_from(summary, ("ts",), "SUMMARY.json", run_dir)
    sessions = summary.get("sessions_detail") or []
    cases = [c for c in (_load(p) for p in session_files) if c] or sessions

    # Modality from the sessions' own `mode` field, corroborated by whether the run
    # left audio on disk. A directory full of .wav files did not happen over text.
    modes = {
        str(s.get("mode") or (s.get("metadata") or {}).get("mode") or "").lower()
        for s in [*sessions, *cases] if isinstance(s, dict)
    }
    modes.discard("")
    has_audio = any(run_dir.glob("*.wav"))
    if modes == {"voice"} or (not modes and has_audio):
        modality = MODALITY_VOICE
    elif modes == {"text"}:
        modality = MODALITY_TEXT
    elif modes:
        modality = MODALITY_NOT_RECORDED  # a mixed run is not one modality
    else:
        modality = MODALITY_NOT_RECORDED

    ran = summary.get("sessions_ran") or summary.get("sessions") or 0
    success = summary.get("task_success")
    # The agent type alone is NOT a unique arm: flow_sim/ and flow_sim_baseline/ both
    # hold a directory per agent type, and archiving them under the same label would
    # present a baseline and a current run as two runs of the same thing.
    family = run_dir.parent.name
    agent_type = summary.get("agent_type") or run_dir.name
    return {
        "suite": "tau2_flow",
        "arm": f"{family}-{agent_type}" if family.startswith("flow_sim") else agent_type,
        "modality": modality,
        "run_dir": run_dir,
        "config": {
            "agent_type": summary.get("agent_type") or run_dir.name,
            "sessions": summary.get("sessions"),
            "modes_observed": sorted(modes) or NOT_RECORDED,
            "audio_on_disk": has_audio,
            "coverage": summary.get("coverage"),
        },
        "summary": summary,
        "cases": cases,
        "date": date,
        "started_at": moment,
        "headline": _headline(
            "Task success", (100.0 * success / ran) if ran and success is not None else None,
            n=ran or None,
        ),
        "requested": RequestedRecord(provider="whissle"),
        "reproduce_command": (
            "python -m tau2.flow.simulate run "
            f"--agent-type {summary.get('agent_type') or run_dir.name} "
            f"--sessions {summary.get('sessions', '?')} "
            f"--mode {sorted(modes)[0] if len(modes) == 1 else '<text|voice>'}"
        ),
        "reproduce_notes": [
            "Voice sessions leave `.bot.wav` / `.caller.wav` / `.mix.wav` beside each "
            "session record — the audio is the evidence the run was really voice.",
        ],
    }


def read_flow_mutation(run_dir: Path) -> Optional[dict[str, Any]]:
    report = _load(run_dir / "report.json")
    if not isinstance(report, dict) or "mutations" not in report:
        return None
    date, moment = _date_from(report, ("ts",), "report.json", run_dir)
    mode = str(report.get("mode") or "").lower()
    modality = (
        MODALITY_VOICE if mode == "voice"
        else MODALITY_TEXT if mode == "text"
        else MODALITY_NOT_RECORDED
    )
    results = report.get("results") or []
    total = report.get("mutations") or len(results)
    passed = report.get("passed")
    return {
        "suite": "flow_mutation",
        "arm": run_dir.name,
        "modality": modality,
        "run_dir": run_dir,
        "config": {
            "agent_type": report.get("agent_type"),
            "mode": report.get("mode"),
            "mutations": total,
            "skipped_kinds": report.get("skipped_kinds"),
        },
        "summary": report,
        "cases": results,
        "date": date,
        "started_at": moment,
        "headline": _headline(
            "Mutations detected",
            (100.0 * passed / total) if total and passed is not None else None,
            n=total,
        ),
        "requested": RequestedRecord(provider="whissle"),
        "reproduce_command": (
            "python -m tau2.flow.mutation_suite run "
            f"--agent-type {report.get('agent_type')} --mode {report.get('mode', 'text')}"
        ),
        "reproduce_notes": [
            "A mutation suite measures SENSITIVITY: each mutation perturbs the flow "
            "and the run passes when the perturbation is detected. A high score means "
            "the harness notices changes, not that the agent performs well.",
        ],
    }


def read_flow_defaults(run_dir: Path) -> Optional[dict[str, Any]]:
    summary = _load(run_dir / "SUMMARY.json")
    if not isinstance(summary, dict) or summary.get("suite") != "flow_defaults":
        return None
    date, moment = _date_from(summary, ("ts",), "SUMMARY.json", run_dir)
    totals = summary.get("totals") or {}
    cases = [
        _load(p) for p in sorted(run_dir.glob("*.json"))
        if p.name not in ("SUMMARY.json", "report.json")
    ]
    cases = [c for c in cases if c]
    types = totals.get("types") or 0
    return {
        "suite": "flow_defaults",
        "arm": "default-flow-conformance",
        # Drives agents over the text channel to assert each agent type's default
        # flow attaches and drives; no audio path is exercised.
        "modality": MODALITY_TEXT,
        "run_dir": run_dir,
        "config": {"suite": "flow_defaults", "totals": totals},
        "summary": summary,
        "cases": cases,
        "date": date,
        "started_at": moment,
        "headline": _headline(
            "Agent types passing",
            (100.0 * (totals.get("pass") or 0) / types) if types else None,
            n=types or None,
        ),
        "requested": RequestedRecord(provider="whissle"),
        "reproduce_command": "python -m tau2.flow.defaults run",
        "reproduce_notes": [
            "This is a conformance check over every agent type's DEFAULT flow — it "
            "asserts the flow attaches and drives, not that the agent is good.",
        ],
    }


def read_tau2_core(path: Path) -> Optional[dict[str, Any]]:
    """A tau2 core simulation file (``retail_run1.json`` and friends).

    These are single JSON files rather than directories, and they are the reason the
    date rule in this module's docstring is stated so firmly: their mtimes are all
    4 Aug, and their ``timestamp`` fields are 31 Jul.
    """
    payload = _load(path)
    if not isinstance(payload, dict) or "simulations" not in payload:
        return None
    moment = _parse_iso(payload.get("timestamp"))
    date = (
        DateProvenance(
            date=moment.strftime("%Y-%m-%d"),
            timestamp=moment.isoformat(timespec="seconds"),
            source=DATE_FROM_ARTIFACT,
            source_detail=f"{path.name}:timestamp = {payload.get('timestamp')!r}",
        )
        if moment else DateProvenance()
    )
    info = payload.get("info") or {}
    sims = payload.get("simulations") or []
    rewards = [
        s.get("reward_info", {}).get("reward")
        for s in sims
        if isinstance(s, dict) and isinstance(s.get("reward_info"), dict)
    ]
    rewards = [r for r in rewards if isinstance(r, (int, float))]
    agent_info = info.get("agent_info") or {}
    user_info = info.get("user_info") or {}

    # These four files are the only artifacts in the whole results tree that recorded
    # real spend at the time. It is dollars rather than tokens (`agent_cost` /
    # `user_cost` per simulation), so it goes in as dollars — pricing it from a token
    # table we do not have would be a fabrication dressed as arithmetic.
    ledger = None
    agent_usd = sum(
        s["agent_cost"] for s in sims
        if isinstance(s, dict) and isinstance(s.get("agent_cost"), (int, float))
    )
    user_usd = sum(
        s["user_cost"] for s in sims
        if isinstance(s, dict) and isinstance(s.get("user_cost"), (int, float))
    )
    if agent_usd or user_usd:
        from .serving import SOURCE_ARTIFACT, ServingLedger

        ledger = ServingLedger(source=SOURCE_ARTIFACT + " (per-simulation agent_cost/user_cost)")
        if agent_usd:
            ledger.record_usd("agent", agent_usd)
        if user_usd:
            ledger.record_usd("user_simulator", user_usd)

    return {
        "suite": "tau2_core",
        "arm": path.stem,
        # tau2 core is a text simulation framework: an LLM user simulator against an
        # LLM agent over structured messages. No audio exists in this harness.
        "modality": MODALITY_TEXT,
        "run_dir": path,
        "config": info,
        "summary": {
            "timestamp": payload.get("timestamp"),
            "n_simulations": len(sims),
            "n_tasks": len(payload.get("tasks") or []),
            "avg_reward": round(sum(rewards) / len(rewards), 4) if rewards else None,
            "agent_cost_usd": round(agent_usd, 6) if agent_usd else None,
            "user_simulator_cost_usd": round(user_usd, 6) if user_usd else None,
            "info": info,
        },
        "cases": sims,
        "ledger": ledger,
        "date": date,
        "started_at": moment,
        "headline": _headline(
            "Average reward",
            round(sum(rewards) / len(rewards), 4) if rewards else None,
            unit="score", n=len(rewards) or None,
        ),
        "requested": RequestedRecord(
            model=str(agent_info.get("llm") or NOT_RECORDED),
            provider=NOT_RECORDED,
            extra={
                "user_simulator_llm": str(user_info.get("llm") or NOT_RECORDED),
                "tau2_git_commit": str(info.get("git_commit") or NOT_RECORDED),
            },
        ),
        "reproduce_command": (
            f"# tau2 core run recorded at git commit {info.get('git_commit', NOT_RECORDED)}\n"
            f"python -m tau2.run --num-trials {info.get('num_trials', 1)} "
            f"--max-steps {info.get('max_steps', '?')} "
            f"--max-errors {info.get('max_errors', '?')}"
        ),
        "reproduce_notes": [
            f"tau2 harness commit recorded in the artifact: "
            f"`{info.get('git_commit', NOT_RECORDED)}`.",
            "The user simulator here is "
            f"`{user_info.get('llm', NOT_RECORDED)}` — an external model, so this run "
            "is not gated on Whissle-side simulator availability.",
        ],
    }


def read_modelsweep(path: Path, *, source_label: str = "") -> Optional[dict[str, Any]]:
    """``results/modelsweep/collected.json`` — the cross-arm aggregation.

    The sweep has no runner of its own: each arm is a MedAgentBench / AgentClinic /
    PatientAgentBench run archived in its own right. This record archives the
    aggregation, and points at the arms rather than duplicating them.
    """
    payload = _load(path)
    if not isinstance(payload, dict) or not payload:
        return None
    if not all(isinstance(v, dict) and "model" in v for v in payload.values()):
        return None
    arms = sorted(payload)
    cases = [{"arm": arm, **payload[arm]} for arm in arms]
    return {
        "suite": "modelsweep",
        "arm": f"{len(arms)}-arm-sweep",
        # The sweep aggregates three text suites; no arm of it was driven over audio.
        "modality": MODALITY_TEXT,
        "run_dir": path,
        "config": {
            "arms": arms,
            "models": {arm: payload[arm].get("model") for arm in arms},
            "benchmarks": sorted({
                k for v in payload.values() for k in v if k != "model"
            }),
            "source": source_label or str(path),
        },
        "summary": {
            "n_arms": len(arms),
            "arms": {
                arm: {
                    "model": payload[arm].get("model"),
                    "medagent_overall": (payload[arm].get("medagent") or {}).get("overall"),
                    "agentclinic_accuracy": (payload[arm].get("agentclinic") or {}).get("accuracy"),
                    "pab_aggregate": (payload[arm].get("pab") or {}).get("aggregate"),
                }
                for arm in arms
            },
        },
        "cases": cases,
        # The aggregation carries no timestamp of its own, and its arms' dates span
        # several runs — so it has no single recoverable date.
        "date": DateProvenance(),
        "requested": RequestedRecord(
            model=f"{len(arms)} arms: " + ", ".join(
                str(payload[a].get("model")) for a in arms),
            provider="mixed",
        ),
        "reproduce_command": (
            "# branch exp/model-sweep\n"
            "./scripts/modelsweep/run_arms.sh medagent\n"
            "./scripts/modelsweep/run_arms.sh agentclinic\n"
            "./scripts/modelsweep/run_arms.sh pab\n"
            "python3 scripts/modelsweep/collect.py"
        ),
        "reproduce_notes": [
            "The sweep is a shell driver over the three health CLIs; each arm's real "
            "artifacts are archived separately under their own suites.",
            "Gemini arms (`g35f`, `g35fl`, `g3fp`) cannot currently be re-run: the "
            "Gemini quota is exhausted and every call returns 429.",
        ],
    }


def read_ttft(path: Path, *, source_label: str = "") -> Optional[dict[str, Any]]:
    """``results/modelsweep/ttft_results.json`` — the direct-to-vendor latency probe.

    Archived separately from the sweep because it measures something else: it calls
    the vendors directly rather than through our backend, so its latencies are a
    floor for the model and say nothing about our stack's overhead. It is also the
    only historical artifact carrying per-call token counts and cost.
    """
    rows = _load(path)
    if not isinstance(rows, list) or not rows:
        return None
    if not all(isinstance(r, dict) for r in rows) or "ttft_ms" not in rows[0]:
        return None

    from .serving import SOURCE_ARTIFACT, ServingLedger

    ledger = ServingLedger(source=SOURCE_ARTIFACT + " (ttft_results.json per-probe rows)")
    for row in rows:
        ledger.record_response(
            {
                "model": row.get("model"),
                "usage": {
                    "input_tokens": row.get("in_tokens") or 0,
                    "output_tokens": row.get("out_tokens") or 0,
                },
                "stop_reason": row.get("stop_reason"),
            },
            case_id=str(row.get("case") or row.get("arm") or ""),
            requested_model=row.get("model"),
        )
        if isinstance(row.get("cost_usd"), (int, float)):
            ledger.record_usd("vendor_direct", float(row["cost_usd"]))

    arms = sorted({str(r.get("arm")) for r in rows if r.get("arm")})
    ttfts = sorted(r["ttft_ms"] for r in rows if isinstance(r.get("ttft_ms"), (int, float)))
    return {
        "suite": "modelsweep",
        "arm": "ttft-probe",
        # Direct HTTP streaming calls to each vendor. No audio, no ASR.
        "modality": MODALITY_TEXT,
        "run_dir": path,
        "config": {
            "arms": arms,
            "n_probes": len(rows),
            "source": source_label or str(path),
            "note": (
                "Probes call each vendor's API DIRECTLY, bypassing the Whissle "
                "backend. The latencies are therefore a floor for the model and "
                "exclude every layer of our own stack."
            ),
        },
        "summary": {
            "n_probes": len(rows),
            "arms": arms,
            "ttft_ms_p50": ttfts[len(ttfts) // 2] if ttfts else None,
            "ttft_ms_p95": ttfts[int(len(ttfts) * 0.95)] if ttfts else None,
        },
        "cases": rows,
        "ledger": ledger,
        # The file records no timestamp of any kind, inside or in its name.
        "date": DateProvenance(),
        "requested": RequestedRecord(model=f"{len(arms)} probe arms", provider="mixed"),
        "reproduce_command": (
            "# branch exp/model-sweep\npython3 scripts/modelsweep/ttft_probe.py --reps 5"
        ),
        "reproduce_notes": [
            "This file carries no timestamp of any kind. The nearest anchor is the "
            "commit that introduced it on `exp/model-sweep`; that anchor is NOT "
            "recorded as the run date, because a commit time is not a run time.",
        ],
        "notes": [
            "**No date is recoverable for this run.** The artifact contains no "
            "timestamp and its filename carries none. It is listed last in the index "
            "rather than being dated from a commit or an mtime.",
        ],
    }


def arm_model_map(collected: Optional[Path]) -> dict[str, str]:
    """``arm -> model`` from the sweep aggregation, used to resolve the agent model
    for AgentClinic runs, which never recorded one."""
    payload = _load(collected) if collected else None
    if not isinstance(payload, dict):
        return {}
    return {
        arm: str(v["model"])
        for arm, v in payload.items()
        if isinstance(v, dict) and v.get("model")
    }


#: Directory readers, tried in order against each candidate run directory.
DIR_READERS: tuple[Callable[[Path], Optional[dict[str, Any]]], ...] = (
    read_medagentbench,
    read_agentclinic,
    read_patientagentbench,
    read_flow_mutation,
    read_flow_defaults,
    read_flow_sim,
)

#: File readers for runs that are a single JSON file rather than a directory.
FILE_READERS: tuple[Callable[[Path], Optional[dict[str, Any]]], ...] = (
    read_tau2_core,
)


def discover(
    results_root: Path, arm_models: Optional[dict[str, str]] = None
) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Every run under ``results_root`` a reader recognises."""
    results_root = Path(results_root)
    if not results_root.is_dir():
        return

    seen: set[Path] = set()
    # Depth 1 and 2 both carry runs: flow_sim/<agent_type>/ and
    # medagentbench/<run>/ are two levels, flow_defaults/ is one.
    candidates: list[Path] = []
    for child in sorted(p for p in results_root.iterdir() if p.is_dir()):
        candidates.append(child)
        candidates.extend(sorted(p for p in child.iterdir() if p.is_dir()))

    for directory in candidates:
        if directory in seen:
            continue
        for reader in DIR_READERS:
            try:
                kwargs = (
                    reader(directory, arm_models)  # type: ignore[call-arg]
                    if reader is read_agentclinic else reader(directory)
                )
            except Exception:  # noqa: BLE001 — one bad directory never stops a backfill
                kwargs = None
            if kwargs:
                seen.add(directory)
                yield directory, kwargs
                break

    for path in sorted(results_root.glob("*.json")):
        for reader in FILE_READERS:
            try:
                kwargs = reader(path)
            except Exception:  # noqa: BLE001
                kwargs = None
            if kwargs:
                yield path, kwargs
                break


#: Where the sweep's aggregation lives. Tried in order: the local branch first, then
#: the remote-tracking ref — a local branch can be pruned or deleted (it was, mid-way
#: through building this) while the remote ref survives, and a backfill that gives up
#: on the first miss would silently drop the sweep from the archive.
MODELSWEEP_REFS = ("exp/model-sweep", "origin/exp/model-sweep",
                   "refs/remotes/origin/exp/model-sweep")
MODELSWEEP_PATH = "results/modelsweep/collected.json"
TTFT_PATH = "results/modelsweep/ttft_results.json"


def _resolve(
    results_root: Path, repo: Optional[Path], repo_relative: str
) -> Optional[tuple[Path, str]]:
    """Find an artifact on disk, else extract it from a git ref. Returns
    ``(path, human-readable source)`` so the manifest can say where it came from."""
    on_disk = Path(results_root).parent / Path(repo_relative).relative_to("results")
    if on_disk.exists():
        return on_disk, str(on_disk)
    if repo:
        found = materialise(Path(repo), repo_relative)
        if found:
            return found[0], f"git show {found[1]}:{repo_relative}"
    return None


def materialise(
    repo: Path, path: str, refs: tuple[str, ...] = MODELSWEEP_REFS
) -> Optional[tuple[Path, str]]:
    """Extract a file from a git ref into a temp file, so a run committed on a side
    branch can be archived from a checkout that does not have it.

    Returns ``(temp_path, ref)`` so the manifest can name the ref it came from.
    """
    if not (Path(repo) / ".git").exists():
        return None
    for ref in refs:
        try:
            blob = subprocess.run(
                ["git", "-C", str(repo), "show", f"{ref}:{path}"],
                capture_output=True, text=True, timeout=60, check=False,
            )
        except Exception:  # noqa: BLE001
            continue
        if blob.returncode != 0 or not blob.stdout.strip():
            continue
        import tempfile

        tmp = Path(tempfile.mkdtemp(prefix="tau2-archive-")) / Path(path).name
        tmp.write_text(blob.stdout, encoding="utf-8")
        return tmp, ref
    return None


def run(
    results_root: Path,
    *,
    root: Optional[Path] = None,
    repo: Optional[Path] = None,
    include_modelsweep: bool = True,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Backfill every recognised run. Returns one row per run, archived or not."""
    rows: list[dict[str, Any]] = []
    jobs: list[tuple[Path, dict[str, Any]]] = []

    # The sweep aggregation is resolved FIRST: it is the only artifact recording
    # arm -> model, which AgentClinic needs and never wrote down itself.
    sweep = _resolve(results_root, repo, MODELSWEEP_PATH) if include_modelsweep else None
    probe = _resolve(results_root, repo, TTFT_PATH) if include_modelsweep else None

    jobs.extend(discover(results_root, arm_model_map(sweep[0] if sweep else None)))

    if include_modelsweep:
        if sweep:
            kwargs = read_modelsweep(sweep[0], source_label=sweep[1])
            if kwargs:
                jobs.append((sweep[0], kwargs))
            if probe:
                probe_kwargs = read_ttft(probe[0], source_label=probe[1])
                if probe_kwargs:
                    jobs.append((probe[0], probe_kwargs))
        else:
            rows.append({
                "source": MODELSWEEP_PATH, "suite": "modelsweep", "arm": NOT_RECORDED,
                "modality": MODALITY_NOT_RECORDED, "date": NOT_RECORDED,
                "date_source": NOT_RECORDED, "n_cases": 0, "archived": False,
                "path": None, "unrecoverable": ["the whole run"],
                "error": (
                    f"{MODELSWEEP_PATH} is not on disk and none of {MODELSWEEP_REFS} "
                    "resolves in this repository — the model sweep could not be "
                    "backfilled. Fetch the branch and re-run rather than treating "
                    "this as 'no sweep exists'."
                ),
            })

    for source, kwargs in jobs:
        row = {
            "source": str(source),
            "suite": kwargs["suite"],
            "arm": kwargs.get("arm"),
            "modality": kwargs["modality"],
            "date": kwargs["date"].date if kwargs.get("date") else NOT_RECORDED,
            "date_source": kwargs["date"].source if kwargs.get("date") else NOT_RECORDED,
            "n_cases": len(kwargs.get("cases") or []),
            "archived": False,
            "path": None,
            "error": None,
            "unrecoverable": [],
        }
        # Name the gaps explicitly so the backfill's own report can count them, and
        # separately: "we know the model but not the bill" and "we know neither" are
        # different states, and a single combined label would hide the difference.
        ledger = kwargs.get("ledger")
        if row["date_source"] == NOT_RECORDED:
            row["unrecoverable"].append("date")
        if kwargs["modality"] == MODALITY_NOT_RECORDED:
            row["unrecoverable"].append("modality")
        if not (ledger and ledger.served().available):
            row["unrecoverable"].append("served model")
        if not (ledger and (ledger.usage_by_model() or ledger.extra_usd())):
            row["unrecoverable"].append("token usage / cost")

        if dry_run:
            rows.append(row)
            continue
        reader_notes = list(kwargs.pop("notes", None) or [])
        try:
            result: ArchiveResult = export_run(
                backfill=True, root=root, update_index=False,
                notes=[
                    "Backfilled from an existing results directory — this run was not "
                    "archived at the time it was produced, so anything the harness did "
                    "not persist is unrecoverable and marked accordingly.",
                    *reader_notes,
                ],
                **kwargs,
            )
            row["archived"] = True
            row["path"] = str(result.path)
            row["notes"] = result.notes
        except Exception as exc:  # noqa: BLE001
            row["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)

    if not dry_run:
        from .index import rebuild
        from .writer import archive_root

        rebuild(archive_root(root))
    return rows
