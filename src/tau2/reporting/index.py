"""The cross-run index: every run ever recorded, and the same benchmark over time.

Two properties matter more than the formatting.

**It accumulates.** ``index.json`` is merged, never overwritten. A run recorded in
March survives a regeneration in August even if its directory has since been
deleted from the working tree — the entry stays, flagged ``artifacts_present:
false``. History that a regeneration can silently erase is not history.

**It only compares like with like.** The regression view is keyed by *series* — the
subject under test — not by benchmark, and a delta is printed only when the metric,
the mode, the judge's independence and the order of magnitude of N all agree. A
success rate graded by a non-independent judge and one graded independently are two
different measurements; an appointment flow and a car-rental flow are two different
subjects that happen to share a harness; a 2-case smoke and a 100-case run differ
mostly by noise. In each case the reason is printed where the arrow would be.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from .model import RunReport

INDEX_SCHEMA = "whissle.benchmark.index/v1"


def entry_for(report: RunReport, report_path: Optional[str] = None) -> dict[str, Any]:
    """The one-line-per-run record. Deliberately flat: this is what gets diffed."""
    return {
        "run_id": report.run_id,
        "benchmark": report.benchmark,
        "series_key": report.series,
        "benchmark_title": report.benchmark_title,
        "title": report.title,
        "mode": report.mode,
        "date": report.date,
        "metric_key": report.headline.key,
        "metric_label": report.headline.label,
        "metric_unit": report.headline.unit,
        "value": report.headline.value,
        "value_formatted": report.headline.formatted(),
        "ci": list(report.headline.ci) if report.headline.ci else None,
        "n_attempted": report.exclusions.n_total,
        "n_scored": report.n_scored,
        "n_excluded": report.exclusions.n_excluded,
        "exclusion_rate_pct": round(report.exclusions.rate_pct, 1),
        "judge_kind": report.judge.kind,
        "judge_provider": report.judge.provider,
        "judge_independent": report.judge.independent,
        "preliminary": report.preliminary,
        "status": report.status,
        "harness_commit": report.provenance.harness_commit,
        "repo_commit": report.provenance.repo_commit,
        "agent_id": report.provenance.agent_id,
        "report_md": report_path or f"{report.run_id}/REPORT.md",
        "report_json": f"{report.run_id}/report.json",
        "artifacts_present": True,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def merge(existing: Optional[dict], new_entries: Iterable[dict], results_root: Path) -> dict:
    """Union of what was already recorded and what was just generated.

    A run present in both is replaced by the freshly generated entry. A run only in
    the old index is kept, with ``artifacts_present`` re-derived from disk so a
    reader can tell the difference between "we still have the trajectories" and "we
    only have the number".
    """
    by_id: dict[str, dict] = {}
    for e in (existing or {}).get("runs", []) or []:
        if isinstance(e, dict) and e.get("run_id"):
            e = dict(e)
            e["artifacts_present"] = (results_root / str(e["run_id"])).exists()
            by_id[str(e["run_id"])] = e
    for e in new_entries:
        by_id[str(e["run_id"])] = e

    runs = sorted(
        by_id.values(),
        key=lambda e: (str(e.get("benchmark") or ""), str(e.get("date") or ""), str(e.get("run_id"))),
    )
    return {
        "schema": INDEX_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_runs": len(runs),
        "runs": runs,
        "regression": regression_view(runs),
    }


#: Two runs whose scored denominators differ by more than this factor are not
#: diffed. A 2-case smoke and a 100-case run can produce a 40-point "delta" that is
#: entirely sampling noise, and an arrow next to it is worse than no arrow.
MAX_N_RATIO = 3.0


def _comparable(a: dict, b: dict) -> tuple[bool, str]:
    if a.get("series_key") and b.get("series_key") and a["series_key"] != b["series_key"]:
        return False, (
            f"different subject under test (`{b['series_key']}` → `{a['series_key']}`)"
        )
    if a.get("metric_key") != b.get("metric_key"):
        return False, (
            f"different headline metric (`{b.get('metric_key')}` → `{a.get('metric_key')}`)"
        )
    if a.get("mode") != b.get("mode"):
        return False, f"different mode (`{b.get('mode')}` → `{a.get('mode')}`)"
    if a.get("judge_independent") != b.get("judge_independent"):
        return False, "judge independence changed between the runs"
    na, nb = a.get("n_scored") or 0, b.get("n_scored") or 0
    if na and nb and max(na, nb) / min(na, nb) > MAX_N_RATIO:
        return False, (
            f"sample sizes are not of the same order (N = {nb} → N = {na}); the "
            "difference would be mostly sampling noise"
        )
    return True, ""


def regression_view(runs: list[dict]) -> dict[str, list[dict]]:
    """One series per *subject*, over time, with a delta only where it means something.

    Keyed by ``series_key``, not by benchmark: ten agent types share the flow
    benchmark, and diffing an appointment flow against a car-rental flow would put a
    confident arrow on a comparison of two unrelated things.
    """
    out: dict[str, list[dict]] = {}
    for bench in sorted({str(r.get("series_key") or r.get("benchmark")) for r in runs}):
        series = [r for r in runs if str(r.get("series_key") or r.get("benchmark")) == bench]
        series.sort(key=lambda r: (str(r.get("date") or ""), str(r.get("run_id"))))
        rows = []
        prev: Optional[dict] = None
        for r in series:
            delta: Optional[float] = None
            note = ""
            if prev is not None:
                ok, why = _comparable(r, prev)
                if not ok:
                    note = f"not comparable to the previous run: {why}"
                elif r.get("value") is not None and prev.get("value") is not None:
                    delta = round(float(r["value"]) - float(prev["value"]), 2)
                    note = f"vs `{prev.get('run_id')}`"
            rows.append(
                {
                    "run_id": r.get("run_id"),
                    "series_key": r.get("series_key"),
                    "benchmark": r.get("benchmark"),
                    "date": r.get("date"),
                    "mode": r.get("mode"),
                    "metric_key": r.get("metric_key"),
                    "value": r.get("value"),
                    "value_formatted": r.get("value_formatted"),
                    "n_scored": r.get("n_scored"),
                    "n_excluded": r.get("n_excluded"),
                    "exclusion_rate_pct": r.get("exclusion_rate_pct"),
                    "judge_independent": r.get("judge_independent"),
                    "preliminary": r.get("preliminary"),
                    "delta_vs_previous": delta,
                    "comparability_note": note,
                    "harness_commit": r.get("harness_commit"),
                }
            )
            prev = r
        out[bench] = rows
    return out


def _arrow(d: Optional[float], unit: str) -> str:
    if d is None:
        return "—"
    sign = "+" if d > 0 else ""
    suffix = "pp" if unit == "pct" else ""
    return f"{sign}{d}{suffix}"


def render_markdown(index: dict) -> str:
    L: list[str] = []
    W = L.append
    W("# Whissle benchmark index")
    W("")
    W(
        f"Every benchmark run this repository has recorded — **{index.get('n_runs', 0)} "
        "runs**. Entries accumulate: a regeneration adds and updates, it never drops a "
        "run, so a figure quoted six months ago can still be traced to the artifacts "
        "that produced it."
    )
    W("")
    W(f"_Generated {index.get('generated_at', '')}._")
    W("")
    W("## How to read a row")
    W("")
    W(
        "- **N** is the scored denominator, never the attempted count. **Excl.** is what "
        "was thrown away and why it matters — a headline over a heavily excluded run is "
        "a statement about the survivors.\n"
        "- **Judge** answers one question: was the grader independent of the agent's "
        "vendor? `no` means the number is a regression instrument, not a leaderboard "
        "result. `n/a` means grading was deterministic and the question does not arise.\n"
        "- **Prelim.** marks a run below the sample-size threshold, or one whose "
        "directory was incomplete when the report was generated."
    )
    W("")

    W("## All runs")
    W("")
    W("| Benchmark | Run | Date | Mode | Headline | N | Excl. | Judge indep. | Prelim. | Commit | Report |")
    W("|---|---|---|---|---:|---:|---:|:---:|:---:|---|---|")
    for r in index.get("runs", []):
        ji = {True: "yes", False: "**no**", None: "n/a"}.get(r.get("judge_independent"), "?")
        excl = (
            f"{r.get('n_excluded')} ({r.get('exclusion_rate_pct')}%)"
            if r.get("n_excluded")
            else "0"
        )
        present = "" if r.get("artifacts_present", True) else " _(artifacts removed)_"
        W(
            f"| {r.get('benchmark_title') or r.get('benchmark')} "
            f"| `{r.get('run_id')}`{present} "
            f"| {r.get('date') or '—'} "
            f"| `{r.get('mode') or '—'}` "
            f"| {r.get('value_formatted') or '—'} "
            f"| {r.get('n_scored')} "
            f"| {excl} "
            f"| {ji} "
            f"| {'**yes**' if r.get('preliminary') else 'no'} "
            f"| `{r.get('harness_commit') or '—'}` "
            f"| [REPORT]({r.get('report_md')}) |"
        )
    W("")

    W("## Regression view — the same benchmark over time")
    W("")
    W(
        "A delta is printed only when the two runs measured the same thing the same "
        "way. Where the metric, the mode or the judge's independence changed, the "
        "reason is printed instead of an arrow, because a difference across a changed "
        "instrument is not a regression or an improvement — it is a different "
        "experiment."
    )
    W("")
    for bench, rows in (index.get("regression") or {}).items():
        if not rows:
            continue
        matching = [
            r
            for r in index.get("runs", [])
            if str(r.get("series_key") or r.get("benchmark")) == bench
        ]
        title = matching[0].get("benchmark_title") if matching else bench
        W(f"### {title} — `{bench}`")
        W("")
        unit = matching[0].get("metric_unit", "pct") if matching else "pct"
        W("| Date | Run | Metric | Value | N | Excl. | Δ vs previous | Note |")
        W("|---|---|---|---:|---:|---:|---:|---|")
        for row in rows:
            note = row.get("comparability_note") or ""
            if row.get("preliminary"):
                note = ("PRELIMINARY. " + note).strip()
            excl = (
                f"{row.get('n_excluded')} ({row.get('exclusion_rate_pct')}%)"
                if row.get("n_excluded")
                else "0"
            )
            W(
                f"| {row.get('date') or '—'} | `{row.get('run_id')}` "
                f"| `{row.get('metric_key')}` | {row.get('value_formatted') or '—'} "
                f"| {row.get('n_scored')} | {excl} "
                f"| {_arrow(row.get('delta_vs_previous'), unit)} | {note} |"
            )
        W("")
    return "\n".join(L) + "\n"


def write(results_root: Path, entries: list[dict]) -> dict:
    """Merge ``entries`` into ``results_root/index.json`` and rewrite ``INDEX.md``."""
    idx_path = results_root / "index.json"
    existing = None
    if idx_path.is_file():
        try:
            existing = json.loads(idx_path.read_text(encoding="utf-8"))
        except Exception:
            existing = None
    index = merge(existing, entries, results_root)
    idx_path.write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    (results_root / "INDEX.md").write_text(render_markdown(index), encoding="utf-8")
    return index
