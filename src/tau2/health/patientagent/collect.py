"""Turn a PatientAgentBench run directory into classified outcomes + artifacts.

Kept separate from ``cli.py`` and free of any ``patient_agent_bench`` import so the
whole collection-and-scoring path is exercised offline against fixture directories —
the part of the pipeline most likely to silently mis-score a published number.

A run directory looks like::

    output/<cases>_<ts>/
      benchmark_cases.json
      0_0/                      # one experiment: assistant[0] x user[0]
        conversations.json      # [{case_id, conversation, error?, ...}]
        evaluations.json        # [{case_id, evaluation: {rubric_scores, ...}}]
        summary.json
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Optional

from tau2.health.patientagent import diagnostics as case_diagnostics
from tau2.health.patientagent.report import write_case_artifact
from tau2.health.patientagent.scoring import (
    INFRA_MARKER,
    SessionOutcome,
    classify_session,
)


def _read_json(path: str, default: Any) -> Any:
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except ValueError:
            return default


def find_experiment_dirs(run_dir: str) -> list[str]:
    """Experiment subdirectories, named ``{assistant_idx}_{user_idx}``."""
    if not os.path.isdir(run_dir):
        return []
    found = [
        os.path.join(run_dir, name)
        for name in sorted(os.listdir(run_dir))
        if os.path.isdir(os.path.join(run_dir, name))
        and len(name.split("_")) == 2
        and all(part.isdigit() for part in name.split("_"))
    ]
    return found


def extract_transcript(conversation: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a serialized conversation into (transcript, tool_calls).

    Their messages serialize with a ``type`` (``human``/``ai``/``tool``) and, on AI
    turns, a ``tool_calls`` list. Tool calls are pulled out separately because they
    are the evidence behind the workflow-accuracy score.
    """
    transcript: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    messages = conversation if isinstance(conversation, list) else (conversation or {}).get("messages", [])

    for index, message in enumerate(messages or []):
        if not isinstance(message, dict):
            continue
        role = message.get("type") or message.get("role") or "unknown"
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        entry = {"index": index, "role": role, "content": content}
        meta = message.get("response_metadata") or {}
        # Voice runs carry per-turn latency/channel AND the per-turn signal +
        # metadata frames on the message metadata — the only path by which a live
        # spoken turn's diagnostics reach a persisted artifact, since the benchmark
        # harness owns the loop and hands us nothing else.
        if isinstance(meta, dict) and meta.get("channel") == "voice":
            entry["voice"] = {
                k: meta.get(k)
                for k in ("turn", "latency_ms", "bot_audio_bytes", "boundary", "kind",
                          "room", "conversation_id", "signals", "user_metadata",
                          "hesitant_input", "current_state", "flow_steps")
                if k in meta
            }
        transcript.append(entry)

        for call in message.get("tool_calls") or []:
            if isinstance(call, dict):
                tool_calls.append(
                    {
                        "turn_index": index,
                        "name": call.get("name"),
                        "args": call.get("args") or call.get("arguments") or {},
                        "id": call.get("id"),
                    }
                )
    return transcript, tool_calls


@dataclass
class DiagnosticsContext:
    """Everything a per-case diagnostic envelope needs that only the RUN knows.

    Gathered once and copied onto every case so a case file is self-describing: the
    mode (and therefore which signals can exist at all), the judge and whether it
    was independent, the seed and stratification, the agent and base URL, and the
    run's judge spend to allocate. ``None`` here means the caller is re-reporting an
    old run that predates this record — the envelope then says so rather than
    inventing values."""

    mode: str = "harness"
    judge: Optional[dict[str, Any]] = None
    sampling: Optional[dict[str, Any]] = None
    provenance: Optional[dict[str, Any]] = None
    run_dir: Optional[str] = None
    n_cases: int = 1
    # Fetching the flow trace costs an HTTP call per case; only voice runs can have
    # one, so it is opt-in and off by default for a 100-case text run.
    fetch_flow_trace: bool = False

    def trace_client(self):
        if not self.fetch_flow_trace:
            return None
        from tau2.health.diagnostics import TraceClient

        return TraceClient()


def collect_outcomes(
    experiment_dir: str,
    *,
    artifact_dir: Optional[str] = None,
    case_metadata: Optional[dict[str, dict[str, Any]]] = None,
    diagnostics: Optional[DiagnosticsContext] = None,
) -> list[SessionOutcome]:
    """Classify every case in one experiment directory, writing per-case artifacts.

    Cases present in ``conversations.json`` but missing from ``evaluations.json``
    still produce an outcome, so a crashed grading pass shows up as an exclusion
    rather than shrinking N invisibly.

    ``diagnostics`` carries the run-level facts each case's diagnostic envelope
    needs (mode, judge, seed/strata, agent, spend). Omitted, the artifacts are
    written exactly as before — a re-report of an old run does not gain fabricated
    provenance.
    """
    conversations = _read_json(os.path.join(experiment_dir, "conversations.json"), [])
    evaluations = _read_json(os.path.join(experiment_dir, "evaluations.json"), [])

    evaluation_by_case: dict[str, dict[str, Any]] = {}
    for item in evaluations or []:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("case_id", ""))
        evaluation = item.get("evaluation")
        if case_id and isinstance(evaluation, dict):
            evaluation_by_case[case_id] = evaluation

    outcomes: list[SessionOutcome] = []
    for record in conversations or []:
        if not isinstance(record, dict):
            continue
        case_id = str(record.get("case_id", ""))
        error = record.get("error")
        evaluation = evaluation_by_case.get(case_id) or record.get("evaluation")

        outcome = classify_session(
            case_id,
            error=error,
            evaluation=evaluation,
            infra_marker=INFRA_MARKER,
            meta={
                "num_turns": record.get("num_turns"),
                "personality": record.get("personality"),
            },
        )
        outcomes.append(outcome)

        if artifact_dir:
            transcript, tool_calls = extract_transcript(record.get("conversation"))
            scenario = (case_metadata or {}).get(
                case_id, {"scenario": record.get("scenario")})
            envelope = None
            if diagnostics is not None:
                envelope = case_diagnostics.build(
                    case_id=case_id,
                    mode=diagnostics.mode,
                    transcript=transcript,
                    raw_tool_calls=tool_calls,
                    scenario=scenario,
                    judge=diagnostics.judge,
                    sampling=diagnostics.sampling,
                    provenance_extra=diagnostics.provenance,
                    run_dir=diagnostics.run_dir,
                    n_cases=diagnostics.n_cases,
                    trace_client=diagnostics.trace_client(),
                )
            write_case_artifact(
                artifact_dir,
                case_id,
                outcome=outcome,
                transcript=transcript,
                tool_calls=tool_calls,
                evaluation=evaluation,
                scenario=scenario,
                diagnostics=envelope,
            )
    return outcomes


def case_metadata_from_run(run_dir: str) -> dict[str, dict[str, Any]]:
    """Map case_id -> the seed attributes (task type, severity, ...) so artifacts
    and stratification checks can be joined back to the scenario mix."""
    cases = _read_json(os.path.join(run_dir, "benchmark_cases.json"), [])
    metadata: dict[str, dict[str, Any]] = {}
    for index, case in enumerate(cases or []):
        if not isinstance(case, dict):
            continue
        case_id = str(
            case.get("id") or case.get("scenario_id") or case.get("case_id") or f"case_{index:05d}"
        )
        metadata[case_id] = {
            key: case.get(key)
            for key in (
                "task_type",
                "severity_level",
                "scenario_complexity",
                "condition_name",
                "personality",
                "preferred_care_option",
            )
            if key in case
        }
    return metadata
