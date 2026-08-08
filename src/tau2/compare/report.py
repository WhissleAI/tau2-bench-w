# Copyright Sierra
"""Report writer: Markdown to read, JSON to consume.

WHAT THE MARKDOWN IS FOR
------------------------
Not the verdict — the verdict is one line. The report exists so a reader can
audit that line: the transcript both systems produced, and, for Whissle, the
flow trace narrating *why* the agent did what it did (state entered, transition
fired and the engine's own recorded reason, variable written and from what
source). A comparison document without that is a scoreboard, and a scoreboard
from the winning vendor is worth nothing.

Three rules the layout enforces:

* the honesty banner is the first thing on the page, before any number
  (:mod:`tau2.compare.honesty`);
* a run that is not a comparison says so in its title and its first section, not
  in a footnote;
* every "cannot tell" prints its reason inline, next to where a verdict would
  have gone — so an ambiguous result costs the reader the same attention as a
  decided one, rather than being quietly rounded away.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from tau2.compare import baselines as bl
from tau2.compare import compare as cmp
from tau2.compare import honesty
from tau2.compare.vendors import HOME_VENDOR

VERDICT_LABEL = {
    cmp.WIN: "Whissle wins",
    cmp.LOSS: "Whissle loses",
    cmp.TIE: "Tie",
    cmp.CANNOT_TELL: "CANNOT TELL",
    cmp.CANNOT_COMPARE: "CANNOT COMPARE",
}

OUTCOME_LABEL = {True: "pass", False: "fail", None: "**cannot tell**"}


def _fence(payload: Any, lang: str = "json") -> str:
    text = (
        json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        if not isinstance(payload, str)
        else payload
    )
    return f"```{lang}\n{text}\n```\n"


def _escape(text: Any, limit: Optional[int] = None) -> str:
    """Make a value safe inside a Markdown table cell.

    ``limit`` truncates for legibility only — every truncated reason is printed in
    full in that vendor's own section below, so nothing is lost, only deferred."""
    out = str(text if text is not None else "").replace("|", "\\|").replace("\n", " ")
    if limit and len(out) > limit:
        out = out[: limit - 1] + "…"
    return out


# ── sections ────────────────────────────────────────────────────────────────────


def _header(data: cmp.ComparisonReportData) -> list[str]:
    title = (
        "Whissle vs. vendor — scenario comparison"
        if data.is_comparison
        else "Whissle scenario measurement — **NOT A COMPARISON**"
    )
    lines = [f"# {title}", ""]
    banner = honesty.banner_markdown()
    if banner:
        lines += [banner]
    lines += [
        f"- **Run id:** `{data.run_id}`",
        f"- **Vendors attempted:** {', '.join(data.vendors)}",
        f"- **Scenarios:** {len(data.scenarios)}",
        f"- **Differentiator status:** `{honesty.differentiator_status()}`",
        "",
    ]
    if not data.is_comparison:
        lines += [
            "## This document is not a head-to-head",
            "",
            f"> {data.not_a_comparison_reason}",
            "",
            "Everything below is a measurement of Whissle alone. No competitor "
            "number appears in it, because none was observed — and this harness "
            "has no path that would invent one. Do not present any figure here as "
            "a comparison.",
            "",
        ]
    return lines


def _preflight_section(data: cmp.ComparisonReportData) -> list[str]:
    lines = ["## Vendor availability", "",
             "| Vendor | Runnable | Missing env | Reason |",
             "| --- | --- | --- | --- |"]
    for vendor, pre in data.preflights.items():
        lines.append(
            f"| {vendor} | {'yes' if pre.get('runnable') else '**no**'} | "
            f"{_escape(', '.join(pre.get('missing_env') or []) or '—')} | "
            f"{_escape(pre.get('reason') or '—')} |"
        )
    lines.append("")
    return lines


def _rollup_section(data: cmp.ComparisonReportData) -> list[str]:
    roll = data.rollup()
    lines = ["## Rollup", "", "### Per-vendor scenario outcomes", "",
             "| Vendor | pass | fail | cannot tell | not runnable |",
             "| --- | --- | --- | --- | --- |"]
    for vendor, tally in roll["per_vendor"].items():
        lines.append(
            f"| {vendor} | {tally['pass']} | {tally['fail']} | "
            f"{tally['cannot_tell']} | {tally['not_runnable']} |"
        )
    lines += ["", "### Head-to-head verdicts", ""]
    if not data.is_comparison:
        lines += ["_No head-to-head verdicts: this run had no matched pair._", ""]
    else:
        lines += ["| Verdict | Scenarios |", "| --- | --- |"]
        for verdict, count in roll["verdicts"].items():
            lines.append(f"| {VERDICT_LABEL.get(verdict, verdict)} | {count} |")
        lines.append("")
    mech = roll["whissle_mechanism_evidence"]
    lines += [
        "### Did Whissle's claimed mechanism fire?",
        "",
        "Read from the flow trace, independently of pass/fail. A scenario that "
        "passed without its mechanism firing passed for some other reason.",
        "",
        "| Mechanism evidence | Scenarios |",
        "| --- | --- |",
        f"| found in trace | {mech.get('found', 0)} |",
        f"| trace read, mechanism absent | {mech.get('absent', 0)} |",
        f"| cannot tell (no trace, or head disabled) | {mech.get('cannot_tell', 0)} |",
        "",
    ]
    return lines


def _baselines_section(data: cmp.ComparisonReportData) -> list[str]:
    if not data.baselines:
        return []
    return ["## Baselines", "", bl.render_table(data.baselines)]


def _transcript(outcome: cmp.VendorOutcome) -> list[str]:
    if not outcome.run.turns:
        return ["_No transcript — the run produced no turns._", ""]
    lines = ["<details><summary>Transcript</summary>", ""]
    for turn in outcome.run.turns:
        lines.append(f"**{turn.index}. user:** {turn.user}")
        lines.append("")
        lines.append(f"**agent:** {turn.reply or '_(no reply)_'}")
        if turn.tools:
            names = ", ".join(
                f"`{c.get('name')}({json.dumps(c.get('arguments'), default=str)})`"
                for c in turn.tools
            )
            lines.append("")
            lines.append(f"_tools:_ {names}")
        lines.append("")
    lines += ["</details>", ""]
    return lines


def _scenario_section(comparison: cmp.ScenarioComparison) -> list[str]:
    s = comparison.scenario
    h = s.hypothesis
    lines = [
        f"## `{s.id}` — {s.title}",
        "",
        f"**Verdict: {VERDICT_LABEL.get(comparison.verdict, comparison.verdict)}** — "
        f"{comparison.verdict_reason}",
        "",
        f"- **Hypothesis ({h.expectation}):** {h.claim}",
        f"- **Mechanism:** {h.mechanism}",
        f"- **Would falsify it:** {h.falsifier}",
        f"- **Agent type:** `{s.agent_type}` · **transports:** "
        f"{', '.join(s.transports)}",
    ]
    if s.proxy_note:
        lines += [
            "",
            f"> **Proxy warning.** {s.proxy_note}",
        ]
    lines += ["", "### Criteria", "",
              "| Criterion | " + " | ".join(comparison.outcomes) + " |",
              "| --- |" + " --- |" * len(comparison.outcomes)]
    by_vendor = {
        v: {c.criterion_id: c for c in o.checks}
        for v, o in comparison.outcomes.items()
    }
    for criterion in s.pass_criteria:
        cells = []
        for vendor in comparison.outcomes:
            res = by_vendor[vendor].get(criterion.id)
            if res is None:
                cells.append("—")
                continue
            label = OUTCOME_LABEL[res.passed]
            cells.append(f"{label}<br><sub>{_escape(res.reason, 180)}</sub>")
        flag = " **(critical)**" if criterion.critical else ""
        lines.append(
            f"| `{criterion.id}`{flag}<br><sub>{_escape(criterion.description)}</sub>"
            f" | " + " | ".join(cells) + " |"
        )
    lines.append("")

    # ── the part this package exists for ────────────────────────────────────
    home = comparison.home
    lines += ["### Why Whissle did what it did (flow trace)", ""]
    if home is None:
        lines += ["_Whissle was not run for this scenario._", ""]
    elif home.evidence is None:
        lines += ["_No trace evaluation was performed._", ""]
    else:
        e = home.evidence
        status = {
            "found": "mechanism FIRED — the trace shows it",
            "absent": "mechanism ABSENT — the trace was read and does not show it",
            "cannot_tell": "**CANNOT TELL** — there is no trace to read",
        }.get(e.status, e.status)
        lines += [
            f"**{status}.** {e.reason}",
            "",
            f"_What would prove it:_ {s.trace_evidence.description}",
            "",
        ]
        if e.narrative:
            lines += ["The engine's own account of the call:", ""]
            lines += [f"- {line}" for line in e.narrative]
            lines += [""]
        if e.excerpt:
            lines += ["<details><summary>Raw trace excerpt</summary>", "",
                      _fence(e.excerpt), "</details>", ""]
        if not e.narrative and not e.excerpt:
            flow = (home.run.flow_section or {})
            lines += [
                "> No trace steps were captured for this run"
                + (f" — {flow.get('reason')}" if flow.get("reason") else "")
                + ".",
                "",
                "> For a product whose differentiator is inspectability, an "
                "unreadable run is itself the finding: on this transport we cannot "
                "say why the agent behaved as it did, and neither could a customer.",
                "",
            ]

    for vendor, outcome in comparison.outcomes.items():
        lines += [f"### {vendor}", ""]
        if not outcome.run.runnable:
            lines += [f"> Not runnable — {outcome.run.not_runnable_reason}", ""]
            continue
        if outcome.run.error:
            lines += [f"> Errored — `{outcome.run.error}`", ""]
        lines += [f"Outcome: **{OUTCOME_LABEL[outcome.passed]}** — {outcome.reason}",
                  ""]
        if outcome.run.setup_caveats:
            lines += ["What we could not match / what this run is not:", ""]
            lines += [f"- {c}" for c in outcome.run.setup_caveats]
            lines += [""]
        lines += _transcript(outcome)
    return lines


def _footer() -> list[str]:
    return [
        "---",
        "",
        "## How to read this document",
        "",
        "- **Setup-matched** numbers were produced by driving both systems "
        "ourselves on identical scenarios. Only these support a head-to-head "
        "verdict.",
        f"- **{bl.PUBLISHED_MARKER}** numbers were quoted from a vendor's own "
        "published material. We did not run them, we usually cannot match their "
        "task set or scoring rule, and they are context — never a result.",
        "- **cannot tell** means the evidence needed does not exist. It is never "
        "resolved in Whissle's favour, and it is never rounded to a pass or a fail.",
        "- A vendor that could not be reached did **not** score zero. It has no "
        "score. This harness has no code path that produces a competitor number it "
        "did not observe.",
        "",
    ]


# ── entry points ────────────────────────────────────────────────────────────────


def render_markdown(data: cmp.ComparisonReportData) -> str:
    lines: list[str] = []
    lines += _header(data)
    lines += _preflight_section(data)
    lines += _rollup_section(data)
    lines += _baselines_section(data)
    for comparison in data.scenarios:
        lines += _scenario_section(comparison)
    lines += _footer()
    return "\n".join(lines) + "\n"


def render_json(data: cmp.ComparisonReportData) -> dict[str, Any]:
    return data.to_dict()


def write(
    data: cmp.ComparisonReportData, out_dir: str, *, stem: str = "report",
) -> dict[str, str]:
    """Write ``<stem>.md`` and ``<stem>.json``. Returns the paths."""
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, f"{stem}.md")
    json_path = os.path.join(out_dir, f"{stem}.json")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(data))
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(render_json(data), handle, indent=2, ensure_ascii=False,
                  default=str)
    return {"markdown": md_path, "json": json_path}


def write_runs(
    data: cmp.ComparisonReportData, out_dir: str,
) -> list[str]:
    """Persist every per-vendor run as its own diagnostics case file, so a single
    scenario's evidence can travel into a bug report on its own — the same reason
    ``tau2.health.diagnostics.write_case`` exists."""
    from tau2.health.diagnostics import write_case

    paths: list[str] = []
    runs_dir = os.path.join(out_dir, "runs")
    for comparison in data.scenarios:
        for vendor, outcome in comparison.outcomes.items():
            paths.append(
                write_case(
                    runs_dir,
                    f"{comparison.scenario.id}__{vendor}",
                    {
                        "scenario_id": comparison.scenario.id,
                        "vendor": vendor,
                        "verdict": comparison.verdict,
                        "outcome": outcome.to_dict(),
                        "differentiator_status": honesty.differentiator_status(),
                    },
                )
            )
    return paths


def summary_line(data: cmp.ComparisonReportData) -> str:
    """One line for a terminal, honest enough to quote."""
    roll = data.rollup()
    if not data.is_comparison:
        return (
            f"{data.run_id}: NOT A COMPARISON — "
            f"{roll['per_vendor'].get(HOME_VENDOR, {}).get('pass', 0)}/"
            f"{roll['n_scenarios']} Whissle-only scenarios passed; "
            f"{data.not_a_comparison_reason}"
        )
    parts = [f"{VERDICT_LABEL.get(k, k)}={v}" for k, v in roll["verdicts"].items()]
    return f"{data.run_id}: " + ", ".join(parts)


def load_json(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def rerender(path: str, out_dir: Optional[str] = None) -> str:
    """Re-render a previously written ``report.json`` to Markdown.

    Deliberately re-derives the banner from :mod:`tau2.compare.honesty` at render
    time and prints BOTH: the status the run recorded and the status now. A report
    regenerated after the metadata head is restored must not silently claim the
    old run had it."""
    payload = load_json(path)
    recorded = payload.get("differentiator_status")
    current = honesty.differentiator_status()
    lines = [
        "# Re-rendered comparison report",
        "",
        honesty.banner_markdown(),
        f"- **Differentiator status recorded at run time:** `{recorded}`",
        f"- **Differentiator status now:** `{current}`",
        "",
    ]
    if recorded != current:
        lines += [
            "> The differentiator status CHANGED between the run and this "
            "re-render. The results below were produced under the recorded status "
            f"(`{recorded}`) and must be interpreted under it, not the current one.",
            "",
        ]
    lines += ["```json", json.dumps(payload.get("rollup"), indent=2), "```", ""]
    text = "\n".join(lines)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        target = os.path.join(out_dir, "rerendered.md")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(text)
        return target
    return text
