"""Reporting — paper-shaped tables plus per-scenario artifacts.

Two outputs, deliberately separate:

* ``summary.json`` / ``REPORT.md`` — the run's numbers in the same shape as the
  paper's Table 4 (``mean [pass%]`` per dimension + weighted aggregate), so a reader
  can drop our row into their leaderboard without transformation. Every row carries
  its N and its mode.
* ``cases/<case_id>.json`` — the evidence behind each number: transcript, tool calls,
  per-rubric scores, and the session's classification.

The mode label is printed on every table. A harness-tools row and an agent-tools row
are different measurements, and the report is the last place where they could get
quietly conflated.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from tau2.health.diagnostics import attach as diag_attach
from tau2.health.patientagent.scoring import (
    INFRA_FAIL,
    RUBRIC_ORDER,
    SessionOutcome,
)

MODE_NOTES = {
    "harness_tools": (
        "PatientAgentBench's own ReAct harness, system prompt and 15 sandbox tools, "
        "with only the model swapped for the Whissle agent brain. Directly "
        "comparable to the paper's published baselines."
    ),
    "agent_tools": (
        "The deployed Whissle agent answering with its OWN prompt, tools and "
        "guardrails; the benchmark's sandbox tools are not bound. Measures the "
        "product, NOT the same task surface as the baselines — do not quote these "
        "numbers against the paper's leaderboard."
    ),
}


def _pretty(name: str) -> str:
    return name.replace("_", " ").title()


def write_case_artifact(
    directory: str,
    case_id: str,
    *,
    outcome: SessionOutcome,
    transcript: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
    evaluation: Optional[dict[str, Any]] = None,
    scenario: Optional[dict[str, Any]] = None,
    diagnostics: Optional[dict[str, Any]] = None,
) -> str:
    """Write one scenario's full evidence. Returns the path.

    ``diagnostics`` is the shared ``tau2.health.diagnostics`` envelope — flow trace,
    per-turn voice signals, metadata sidecar, tool forensics, per-case provenance
    and cost, each stamped available or explicitly unavailable-with-a-reason. It is
    added as one extra key so every existing reader of this file is unaffected."""
    os.makedirs(directory, exist_ok=True)
    # Case ids can carry path separators; keep the filename flat.
    safe = str(case_id).replace(os.sep, "_").replace("/", "_")
    path = os.path.join(directory, f"{safe}.json")
    payload = {
        "case_id": case_id,
        "status": outcome.status,
        "detail": outcome.detail,
        "rubric_scores": outcome.rubric_scores,
        "aggregate_score": outcome.aggregate,
        "meta": outcome.meta,
        "scenario": scenario or {},
        "transcript": transcript,
        "tool_calls": tool_calls,
        "evaluation": evaluation or {},
    }
    if diagnostics is not None:
        diag_attach(payload, diagnostics)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)
    return path


def leaderboard_row(summary: dict[str, Any], agent_label: str = "Whissle") -> str:
    """One markdown row in the paper's Table 4 shape: ``mean [pass%]`` per column."""
    cells = [agent_label, f"{summary.get('aggregate') if summary.get('aggregate') is not None else 'n/a'}"]
    for name in RUBRIC_ORDER:
        dim = summary["dimensions"].get(name) or {}
        if dim.get("mean") is None:
            cells.append("n/a")
        else:
            cells.append(f"{dim['mean']:.2f} [{dim['pass_rate']:.0f}%]")
    return "| " + " | ".join(cells) + " |"


def render_judge(judge: Optional[dict[str, Any]]) -> list[str]:
    """The judge block. Every number on this page was produced by SOME grader; saying
    which one, and whether it was independent of the agent's vendor, is the difference
    between a diagnostic and a claim."""
    lines = ["## Judge", ""]
    if not judge:
        lines += [
            "> **Judge provider: unrecorded.** This run predates judge-provider "
            "recording, so which model graded it cannot be established from the "
            "artifacts. Do not publish these numbers — re-run.",
            "",
        ]
        return lines
    independent = bool(judge.get("judge_independent"))
    lines += [
        f"- **provider**: `{judge.get('judge_provider', '?')}` "
        f"(`{judge.get('judge_endpoint', '?')}`)",
        f"- **evaluator model(s)**: `{judge.get('judge_model', '?')}`, "
        f"K = {judge.get('jury_k', '?')} "
        + ("(the paper uses K=2)" if judge.get("jury_k") != 2 else ""),
        f"- **patient simulator**: `{judge.get('patient_model', '?')}`  •  "
        f"**sandbox**: `{judge.get('sandbox_model', '?')}`",
        f"- **independent of the agent's vendor**: **{'yes' if independent else 'NO'}**",
    ]
    if judge.get("judge_calls") is not None:
        lines.append(
            f"- **judge spend**: {judge.get('judge_calls')} calls, "
            f"${float(judge.get('judge_cost_usd') or 0.0):.4f} "
            f"({judge.get('judge_calls_per_case', '?')}/case, "
            f"${float(judge.get('judge_cost_usd_per_case') or 0.0):.4f}/case)"
        )
    lines += [
        "",
        f"> {judge.get('judge_independence_note', '')}",
        "",
    ]
    return lines


def render_markdown(
    summary: dict[str, Any],
    *,
    agent_label: str = "Whissle",
    sample_report: Optional[dict[str, Any]] = None,
    comparison: Optional[dict[str, Any]] = None,
    provenance: Optional[dict[str, Any]] = None,
    judge: Optional[dict[str, Any]] = None,
) -> str:
    """Render the full run report."""
    mode = summary.get("mode", "unknown")
    lines: list[str] = []
    lines.append(f"# PatientAgentBench — {agent_label}")
    lines.append("")
    lines.append(f"**Mode:** `{mode}` — {MODE_NOTES.get(mode, 'unknown mode')}")
    lines.append("")
    lines.append(
        f"**N = {summary['n_scored']} scored** "
        f"(of {summary['n_total']} attempted; {summary['n_excluded']} excluded)"
    )
    lines.append("")
    lines.append(
        f"**Judge:** `{(judge or {}).get('judge_provider', 'unrecorded')}` — "
        + ("independent of the agent's vendor."
           if (judge or {}).get("judge_independent")
           else "NOT independent of the agent's vendor; see the Judge section below.")
    )
    lines.append("")

    # -- headline table, paper shape --
    lines.append("## Results (paper Table 4 shape)")
    lines.append("")
    lines.append(
        "| Agent | Aggregate | " + " | ".join(_pretty(n) for n in RUBRIC_ORDER) + " |"
    )
    lines.append("|---|:---:|" + "|".join([":---:"] * len(RUBRIC_ORDER)) + "|")
    lines.append(leaderboard_row(summary, agent_label))
    lines.append("")
    lines.append("Each cell is `mean [pass%]`, pass = score >= 3, scale 1-5.")
    lines.append("")

    # -- per-dimension detail with CIs and N --
    lines.append("## Per-dimension detail")
    lines.append("")
    lines.append("| Dimension | Weight | N | Mean | 95% CI | Pass rate | 95% CI (Wilson) |")
    lines.append("|---|:---:|:---:|:---:|:---:|:---:|:---:|")
    for name in RUBRIC_ORDER:
        dim = summary["dimensions"].get(name) or {}
        if dim.get("mean") is None:
            lines.append(f"| {_pretty(name)} | {summary['weights'].get(name)} | 0 | n/a | n/a | n/a | n/a |")
            continue
        mean_ci = dim.get("mean_ci") or (None, None)
        pass_ci = dim.get("pass_rate_ci") or (None, None)
        lines.append(
            f"| {_pretty(name)} | {dim.get('weight')} | {dim['n']} | {dim['mean']:.2f} | "
            f"[{mean_ci[0]}, {mean_ci[1]}] | {dim['pass_rate']:.1f}% | "
            f"[{pass_ci[0]}%, {pass_ci[1]}%] |"
        )
    agg_ci = summary.get("aggregate_ci") or (None, None)
    lines.append("")
    lines.append(
        f"**Weighted aggregate: {summary.get('aggregate')}** "
        f"(95% CI [{agg_ci[0]}, {agg_ci[1]}], N = {summary['n_scored']}); "
        f"weights sum to {round(sum(summary['weights'].values()), 1)}."
    )
    lines.append("")

    # -- exclusions, stated plainly --
    excluded = summary.get("excluded_breakdown", {})
    lines.append("## Excluded sessions")
    lines.append("")
    lines.append(
        f"- `{INFRA_FAIL}`: **{excluded.get(INFRA_FAIL, 0)}** — transport/pipeline "
        "faults (5xx, timeout, dead voice channel). These measured our uptime, not "
        "clinical quality, and are excluded from every mean above."
    )
    lines.append(
        f"- `agent_error`: **{excluded.get('agent_error', 0)}** — the conversation ran "
        "but produced no usable evaluation."
    )
    lines.append("")

    if sample_report:
        lines.append("## Sampling")
        lines.append("")
        lines.append(
            f"Seeded stratified sample: **{sample_report['n_selected']} of "
            f"{sample_report['n_population']}** cases, seed `{sample_report['seed']}`, "
            f"strata `{' x '.join(sample_report['strata_keys'])}`."
        )
        lines.append("")
        for key, values in (sample_report.get("distribution") or {}).items():
            lines.append(f"**{_pretty(key)}** (population % vs sample %)")
            lines.append("")
            lines.append("| Value | Population | Sample | Sample N |")
            lines.append("|---|:---:|:---:|:---:|")
            for value, stats in values.items():
                lines.append(
                    f"| {value} | {stats['population_pct']}% | "
                    f"{stats['sample_pct']}% | {stats['sample_n']} |"
                )
            lines.append("")

    if comparison:
        lines.append("## Text vs voice")
        lines.append("")
        if comparison.get("warning"):
            lines.append(f"> **WARNING:** {comparison['warning']}")
            lines.append("")
        lines.append(
            f"N: text = {comparison.get('n_text')}, voice = {comparison.get('n_voice')}."
        )
        lines.append("")
        lines.append("| Dimension | Text | Voice | Delta | Text pass% | Voice pass% |")
        lines.append("|---|:---:|:---:|:---:|:---:|:---:|")
        for name in RUBRIC_ORDER:
            d = (comparison.get("dimensions") or {}).get(name)
            if not d:
                lines.append(f"| {_pretty(name)} | n/a | n/a | n/a | n/a | n/a |")
                continue
            lines.append(
                f"| {_pretty(name)} | {d['text']:.2f} | {d['voice']:.2f} | "
                f"{d['delta']:+.2f} | {d['text_pass_rate']}% | {d['voice_pass_rate']}% |"
            )
        agg = comparison.get("aggregate") or {}
        if agg.get("delta") is not None:
            lines.append("")
            lines.append(
                f"**Aggregate: text {agg['text']} -> voice {agg['voice']} "
                f"({agg['delta']:+.2f})**"
            )
        lines.append("")

    lines.extend(render_judge(judge))

    if provenance:
        lines.append("## Provenance")
        lines.append("")
        for key, value in provenance.items():
            lines.append(f"- **{key}**: `{value}`")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(
        "PatientAgentBench is CC-BY-NC-4.0 and its authors state it is \"not a "
        "clinical certification or a deployment-readiness assessment\". These "
        "numbers are a research measurement, not a safety claim."
    )
    lines.append("")
    return "\n".join(lines)


def write_report(
    directory: str,
    summary: dict[str, Any],
    *,
    agent_label: str = "Whissle",
    sample_report: Optional[dict[str, Any]] = None,
    comparison: Optional[dict[str, Any]] = None,
    provenance: Optional[dict[str, Any]] = None,
    judge: Optional[dict[str, Any]] = None,
) -> dict[str, str]:
    """Write ``summary.json`` and ``REPORT.md``. Returns the written paths."""
    os.makedirs(directory, exist_ok=True)
    summary_path = os.path.join(directory, "summary.json")
    payload = dict(summary)
    if sample_report:
        payload["sampling"] = sample_report
    if comparison:
        payload["comparison"] = comparison
    if provenance:
        payload["provenance"] = provenance
    # Flattened as well as nested: a consumer reading only the top level of
    # summary.json must still see which judge produced the numbers next to it.
    payload["judge"] = judge or {"judge_provider": "unrecorded"}
    payload["judge_provider"] = (judge or {}).get("judge_provider", "unrecorded")
    payload["judge_independent"] = (judge or {}).get("judge_independent")
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, default=str)

    report_path = os.path.join(directory, "REPORT.md")
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(
            render_markdown(
                summary,
                agent_label=agent_label,
                sample_report=sample_report,
                comparison=comparison,
                provenance=provenance,
                judge=judge,
            )
        )
    return {"summary": summary_path, "report": report_path}
