# Copyright Sierra
"""MANIFEST.md — the page that makes a folder interpretable months later.

The test this file is written against: **hand the folder to someone who was not in
the room, months from now, with no access to the conversation that produced it.** Can
they tell what ran, when, against what, how honest the number is, and how to run it
again? Every section below exists because the answer was once "no".

Which is why the manifest states caveats in prose rather than leaving them implied by
a JSON field. ``"modality": "text"`` is a fact a reader has to know to interpret; "this
run was driven entirely over text and says nothing about voice performance" is a fact
they cannot misread. ``manifest.json`` carries the same content for machines; this is
the one a human opens.
"""
from __future__ import annotations

from typing import Any, Optional

from .schema import (
    MODALITY_MEANING,
    MODALITY_NOT_RECORDED,
    NOT_RECORDED,
    is_voice,
)


def modality_meaning(modality: str) -> str:
    return MODALITY_MEANING.get(
        modality, f"unrecognised modality {modality!r} — treat as {NOT_RECORDED}"
    )


def modality_caveat(modality: str) -> str:
    """The one sentence that stops this run being quoted as something it is not."""
    if modality == MODALITY_NOT_RECORDED:
        return (
            "**Modality is not recorded.** This run may have been driven over text or "
            "over voice; the artifacts do not say. Do not present it as either."
        )
    if is_voice(modality):
        return (
            "This run was driven over a real audio transport, so voice-pipeline "
            "claims about it are supported by its own artifacts."
        )
    return (
        "**This run was driven over TEXT, not voice.** No audio was synthesised, "
        "transmitted or recognised at any point, so it measures the language model "
        "and the tool loop — not speech recognition, not turn-taking, not latency "
        "under audio. Presenting it as a voice result would be false; two runs were "
        "published that way before, which is why this line is here."
    )


def _get(record: dict[str, Any], *path: str, default: Any = NOT_RECORDED) -> Any:
    cur: Any = record
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return default if cur is None else cur


def _row(label: str, value: Any) -> str:
    return f"| {label} | {value if value not in (None, '') else NOT_RECORDED} |"


def _served_line(record: dict[str, Any]) -> str:
    served = record.get("served") or {}
    if not served.get("available"):
        return (
            f"**{NOT_RECORDED}** — {served.get('reason') or 'no per-turn record exists'}. "
            "The requested model below is a *request*, not evidence of what answered."
        )
    models = served.get("models") or {}
    parts = ", ".join(f"`{m}` × {n}" for m, n in sorted(models.items(), key=lambda kv: -kv[1]))
    if served.get("failover_observed"):
        return (
            f"{parts} — **more than one model served this run.** Provider failover "
            "occurred, so the arm label does not describe every turn. Any per-arm "
            "comparison must account for this."
        )
    return f"{parts} — a single model served every recorded turn."


def render(record: dict[str, Any]) -> str:
    modality = str(record.get("modality") or MODALITY_NOT_RECORDED)
    meta = record.get("environment", {}).get("metadata_head", {}) or {}
    cost = record.get("cost") or {}
    date = record.get("date") or {}
    counts = record.get("counts") or {}
    repro = record.get("reproduce") or {}
    git = record.get("environment", {}).get("harness_git", {}) or {}
    backend = record.get("environment", {}).get("backend", {}) or {}

    L: list[str] = []
    W = L.append

    W(f"# {record.get('suite', '?')} — {record.get('arm', '?')}")
    W("")
    W(
        f"`{record.get('archive_id', '?')}` · schema `{record.get('schema', '?')}` · "
        f"archived {record.get('archived_at', NOT_RECORDED)}"
        + ("  · **backfilled from an existing results directory**"
           if record.get("backfilled") else "")
    )
    W("")

    # ---- the three things a reader must not get wrong ---------------------
    W("## Read this before quoting any number from this run")
    W("")
    W(f"1. **Modality — `{modality}`.** {modality_caveat(modality)}")
    W("")
    head_state = "IN the path" if meta.get("in_path") else "NOT in the path"
    W(f"2. **Whissle metadata head — {head_state}.** {meta.get('reason', NOT_RECORDED)}")
    W("")
    W(f"3. **Model that actually served the run.** {_served_line(record)}")
    W("")
    W(
        "Each of these was captured from the environment or the wire at archive time, "
        "not asserted by hand. `manifest.json` carries the same facts for machines."
    )
    W("")

    # ---- what ran ---------------------------------------------------------
    W("## What ran")
    W("")
    W("| | |")
    W("|---|---|")
    W(_row("Suite", f"`{record.get('suite')}`"))
    W(_row("Arm / configuration", f"`{record.get('arm_raw') or record.get('arm')}`"))
    W(_row("Modality", f"`{modality}` — {modality_meaning(modality)}"))
    W(_row("Cases archived", counts.get("cases")))
    W(_row("Date", f"{date.get('date', NOT_RECORDED)}"))
    W(_row("Date source", f"`{date.get('source', NOT_RECORDED)}` — {date.get('source_detail', NOT_RECORDED)}"))
    W(_row("Started", _get(record, "run", "started_at")))
    W(_row("Finished", _get(record, "run", "finished_at")))
    W(_row("Duration", (
        f"{_get(record, 'run', 'duration_seconds')} s"
        if _get(record, "run", "duration_seconds", default=None) is not None
        else NOT_RECORDED)))
    W("")
    if date.get("source") == NOT_RECORDED:
        W(
            "> **The run date could not be recovered.** No timestamp exists inside "
            "the artifacts or in the source directory name. "
            f"{date.get('mtime_note', '')}"
        )
        W("")

    # ---- requested vs served ---------------------------------------------
    W("## Requested vs served")
    W("")
    W(
        "These are separate rows because they can disagree. A sweep arm names the "
        "model it *asked* for; provider failover decides what *answered*."
    )
    W("")
    requested = record.get("requested") or {}
    served = record.get("served") or {}
    W("| | Requested | Served |")
    W("|---|---|---|")
    W(
        f"| Model | `{requested.get('model', NOT_RECORDED)}` | "
        f"`{served.get('dominant', NOT_RECORDED)}`"
        + (f" (+{len(served.get('distinct') or []) - 1} more)"
           if served.get("distinct") and len(served["distinct"]) > 1 else "")
        + " |"
    )
    W(f"| Provider | `{requested.get('provider', NOT_RECORDED)}` | — |")
    W(f"| Effort | `{requested.get('effort', NOT_RECORDED)}` | — |")
    W(f"| Thinking | `{requested.get('thinking', NOT_RECORDED)}` | — |")
    W(f"| Turns recorded | — | {served.get('turns', NOT_RECORDED)} |")
    W("")
    if served.get("stop_reasons"):
        W("Stop reasons across served turns: "
          + ", ".join(f"`{k}` × {v}" for k, v in sorted(served["stop_reasons"].items())))
        W("")
    if served.get("stop_details"):
        W(
            f"**{len(served['stop_details'])} turn(s) carried `stop_details`** — the "
            "field is populated only on refusals, so these turns were declined rather "
            "than answered. See `serving.json`."
        )
        W("")

    # ---- judge ------------------------------------------------------------
    if requested.get("judge_provider", NOT_RECORDED) != NOT_RECORDED:
        indep = requested.get("judge_independent")
        provider = str(requested.get("judge_provider"))
        W("## Grading")
        W("")
        W(f"- **Grader**: `{provider}`")
        W(f"- **Judge model**: `{requested.get('judge_model', NOT_RECORDED)}`")
        if indep is False:
            W(
                "- **Independence**: **NOT independent** — the same vendor supplied "
                "both the agent under test and the grader. This number is a "
                "regression instrument, not a leaderboard result. Re-run the judge on "
                "an external provider before publishing it against a paper's table."
            )
        elif indep is True:
            W(
                "- **Independence**: independent of the agent's vendor — the stronger "
                "footing for a published comparison."
            )
        elif provider in ("builtin", "refsol", "deterministic"):
            W(
                "- **Independence**: not applicable. This suite grades "
                "deterministically against a reference solution, so no judge model is "
                "involved and the independence question does not arise."
            )
        else:
            W(f"- **Independence**: {NOT_RECORDED}.")
        W("")

    # ---- cost -------------------------------------------------------------
    W("## Cost")
    W("")
    if not cost.get("available"):
        W(f"{NOT_RECORDED} — {cost.get('reason', 'no usage was captured')}")
    else:
        W("| | |")
        W("|---|---|")
        W(_row("Agent spend", f"${cost.get('agent_usd')}" if cost.get("agent_usd") is not None else NOT_RECORDED))
        W(_row("Judge / simulator spend", f"${cost.get('judge_and_simulator_usd')}" if cost.get("judge_and_simulator_usd") is not None else NOT_RECORDED))
        W(_row("Total", f"**${cost.get('total_usd')}**" if cost.get("total_usd") is not None else NOT_RECORDED))
        W(_row("Per case", f"${cost.get('usd_per_case')}" if cost.get("usd_per_case") is not None else NOT_RECORDED))
        usage = cost.get("usage_total") or {}
        W(_row("Input tokens", f"{usage.get('total_input_tokens', NOT_RECORDED):,}" if isinstance(usage.get("total_input_tokens"), int) else NOT_RECORDED))
        W(_row("Output tokens", f"{usage.get('output_tokens', NOT_RECORDED):,}" if isinstance(usage.get("output_tokens"), int) else NOT_RECORDED))
        W("")
        if cost.get("input_dominates_output"):
            W(f"> {cost['input_dominates_output']}")
            W("")
        if cost.get("unpriced_models"):
            W(
                "> **Partial.** No published price is recorded for "
                + ", ".join(f"`{m}`" for m in cost["unpriced_models"])
                + ", so those turns are **excluded** from the total rather than "
                "counted as zero. The figure above is a floor, not the bill."
            )
            W("")
        W(f"_Prices as of {cost.get('prices_as_of', NOT_RECORDED)}. {cost.get('prices_source', '')}_")
    W("")

    # ---- reproduce --------------------------------------------------------
    W("## Reproduce")
    W("")
    command = repro.get("command", NOT_RECORDED)
    if command and command != NOT_RECORDED:
        W("```bash")
        W(command)
        W("```")
    else:
        W(f"Exact command: **{NOT_RECORDED}**.")
    W("")
    W("| | |")
    W("|---|---|")
    W(_row("Harness commit", f"`{git.get('short_sha', NOT_RECORDED)}` on `{git.get('branch', NOT_RECORDED)}`"))
    W(_row("Harness working tree", (
        "**dirty — uncommitted changes were present, so this SHA alone does not "
        "reproduce the run**" if git.get("dirty") else
        "clean" if git.get("dirty") is False else NOT_RECORDED)))
    W(_row("Backend commit", f"`{backend.get('sha', NOT_RECORDED)}`"))
    W(_row("Backend URL", f"`{backend.get('base_url', NOT_RECORDED)}`"))
    W(_row("Backend reachable at archive time", backend.get("reachable")))
    W("")
    if backend.get("sha", NOT_RECORDED) == NOT_RECORDED:
        W(f"> {backend.get('sha_source', '')}")
        W("")
    for note in repro.get("notes") or []:
        W(f"- {note}")
    if repro.get("notes"):
        W("")

    # ---- environment ------------------------------------------------------
    W("## Environment")
    W("")
    envv = record.get("environment", {}).get("env_vars", {}) or {}
    W("| Variable | Value |")
    W("|---|---|")
    for name, value in sorted(envv.items()):
        W(f"| `{name}` | `{value}` |")
    W("")
    W(
        "_Secret-shaped variables are recorded as a `sha256:` fingerprint — enough to "
        "answer \"was it the same key?\", useless to anyone who copies it out._"
    )
    W("")
    W("| | |")
    W("|---|---|")
    W(_row("Python", _get(record, "environment", "python")))
    W(_row("Platform", _get(record, "environment", "platform")))
    W(_row("Host", _get(record, "environment", "hostname")))
    W("")

    # ---- what is in this folder ------------------------------------------
    W("## What is in this folder")
    W("")
    W("| Path | What it is |")
    W("|---|---|")
    W("| `MANIFEST.md` | this page |")
    W("| `manifest.json` | the same facts, machine-readable, schema-stamped |")
    W("| `config.json` | the run's configuration — model, provider, N, seeds, judge, task set |")
    W("| `summary.json` | headline metrics, N, exclusions |")
    W("| `REPORT.md` | the research-paper writeup of this run |")
    W("| `cases/` | one file per case |")
    W("| `logs/` | runner output, infra failures, and `archive.log` — what this archive skipped |")
    W("| `raw/` | **the harness's own output, unmodified.** Never edited. Every derived number here is recomputable from it. |")
    if record.get("served", {}).get("available"):
        W("| `serving.json` | every model call: served model, tokens, stop reason, per-case cost |")
    W("")
    W(
        f"`raw/` holds {counts.get('raw_files', 0)} file(s) "
        f"({(counts.get('raw_bytes') or 0) / 1e6:.2f} MB) copied from "
        f"`{counts.get('raw_source', NOT_RECORDED)}`."
    )
    if counts.get("raw_failed"):
        W("")
        W(
            f"> **`raw/` is incomplete** — {counts['raw_failed']} file(s) could not be "
            "copied. See `logs/archive.log`."
        )
    if counts.get("raw_oversized_skipped"):
        skipped = counts["raw_oversized_skipped"]
        W("")
        W(
            f"> **`raw/` is incomplete by configuration** — {len(skipped)} file(s) "
            f"totalling {(counts.get('raw_oversized_skipped_bytes') or 0) / 1e6:.1f} MB "
            "each exceeded the configured per-file size ceiling and were not copied. "
            f"They remain at `{counts.get('raw_source', NOT_RECORDED)}`. Every skipped "
            "file is listed by path and size in `logs/archive.log`."
        )
    W("")

    # ---- notes ------------------------------------------------------------
    notes = record.get("notes") or []
    W("## Notes and caveats raised at archive time")
    W("")
    if notes:
        for note in notes:
            W(f"- {note}")
    else:
        W(
            "None. Nothing was dropped, degraded or worked around while writing this "
            "archive. (This is a statement about the archiving, not about the run.)"
        )
    W("")
    return "\n".join(L) + "\n"
