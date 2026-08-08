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
from typing import Any, Optional

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
        # Voice runs carry per-turn latency/channel on the message metadata.
        if isinstance(meta, dict) and meta.get("channel") == "voice":
            entry["voice"] = {
                k: meta.get(k) for k in ("latency_ms", "bot_audio_bytes", "boundary", "kind")
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


def collect_outcomes(
    experiment_dir: str,
    *,
    artifact_dir: Optional[str] = None,
    case_metadata: Optional[dict[str, dict[str, Any]]] = None,
) -> list[SessionOutcome]:
    """Classify every case in one experiment directory, writing per-case artifacts.

    Cases present in ``conversations.json`` but missing from ``evaluations.json``
    still produce an outcome, so a crashed grading pass shows up as an exclusion
    rather than shrinking N invisibly.
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
            write_case_artifact(
                artifact_dir,
                case_id,
                outcome=outcome,
                transcript=transcript,
                tool_calls=tool_calls,
                evaluation=evaluation,
                scenario=(case_metadata or {}).get(case_id, {"scenario": record.get("scenario")}),
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
