"""The publishable export: one JSON file the public ``/benchmark`` page consumes.

Schema ``whissle.benchmark.web/v1``.

**Designed to feed an existing frontend rather than to replace it.** The website
repo's ``components/marketing/benchmark/benchmarkdata.ts`` already defines
``BenchmarkRow`` / ``FlowRow`` / ``BenchmarkBaseline`` and a publishing gate that
rejects a data file missing an N or naming a model vendor. Every field name here is
the field name over there, so the frontend change is: read this file, spread it into
those constants, set ``PLACEHOLDER = false``. The extra keys this file carries
beyond the current TypeScript interfaces (``history``, ``attempted``, ``excluded``,
``judgeIndependent``, ``scoreNative``, …) are additive — a consumer that ignores
them still type-checks, and a consumer that wants to show exclusions or a trend
line already has the data.

The honesty rules travel *in the data*, not in a rendering convention: a row cannot
exist without its N, and if a run was judged by a non-independent grader or lost
units to exclusion, the row says so in a field the page can render and a test can
assert on.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .honesty import Violation
from .model import PRELIMINARY_N_THRESHOLD, RunReport

WEB_SCHEMA = "whissle.benchmark.web/v1"
BENCHMARK_REPO_URL = "https://github.com/WhissleAI/tau2-bench-w"

#: The website's publishing gate allows exactly these comparator labels. Published
#: names are mapped onto them so the generated file passes that gate unchanged;
#: ``baselinesAll`` below keeps the full published table for when it widens.
BASELINE_LABEL_MAP = {
    "gpt-4o": "GPT-4o",
    "claude 3.5 sonnet v2": "Claude-3.5-Sonnet",
    "claude-3.5-sonnet": "Claude-3.5-Sonnet",
    "claude 3.5 sonnet": "Claude-3.5-Sonnet",
}

from .honesty import _PROVIDER_RE  # noqa: E402  — one regex, one definition

#: Rows whose benchmark is in here render in the flow-suite block, not the
#: leaderboard table — they have no external comparator by construction.
FLOW_BENCHMARKS = {"flow_sim"}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _pct_score(report: RunReport) -> tuple[Optional[float], str]:
    """A 0–100 number for the page, plus what kind of number it is.

    A rubric mean on a 1–5 scale is rescaled — and flagged, because a rescaled
    rubric is not a pass rate and must not be read as one.
    """
    m = report.headline
    if m.value is None:
        return None, "none"
    if m.unit == "pct":
        return round(float(m.value), 1), "pass_rate"
    if m.floor is not None and m.ceiling is not None and m.ceiling > m.floor:
        rescaled = 100.0 * (m.value - m.floor) / (m.ceiling - m.floor)
        return round(rescaled, 1), "normalised_rubric"
    return round(float(m.value), 1), "raw"


def _passed(report: RunReport) -> Optional[int]:
    """Only where the headline really is a count over the scored denominator — the
    website's gate cross-checks ``passed / sampleSize`` against ``score``."""
    m = report.headline
    if m.unit != "pct" or m.value is None or not report.n_scored:
        return None
    return int(round(m.value / 100.0 * report.n_scored))


def _note(report: RunReport) -> str:
    """One line that carries the caveats a reader needs beside the number.

    Every clause here is required by an honesty rule, which is why it is assembled
    rather than written: the exclusion clause appears because units were excluded,
    not because someone remembered.
    """
    bits: list[str] = []
    score, kind = _pct_score(report)
    if kind == "normalised_rubric":
        bits.append(
            f"weighted rubric aggregate {report.headline.formatted()} on a "
            f"{report.headline.floor:g}–{report.headline.ceiling:g} scale, rescaled to "
            "0–100 here; it is a quality score, not a pass rate"
        )
    if report.exclusions.any:
        bits.append(
            f"{report.exclusions.n_excluded} of {report.exclusions.n_total} runs "
            f"({report.exclusions.rate_pct:.0f}%) were excluded for transport failure "
            "and are not in this figure"
        )
    if report.judge.independent is False:
        bits.append("graded by our own grader, not an independent one")
    elif report.judge.kind == "deterministic":
        bits.append("graded deterministically against database state, not by a grader model")
    if report.preliminary:
        bits.append("preliminary — small sample")
    return "; ".join(bits).capitalize() + "." if bits else ""


def _baselines(report: RunReport) -> tuple[list[dict], list[dict]]:
    allowed: list[dict] = []
    everything: list[dict] = []
    for b in report.baselines.baselines:
        key = next(iter(b.values)) if b.values else None
        if key is None:
            continue
        val = b.values.get("overall", b.values.get(key))
        # Named and sourced, always. "Frontier text agent — 0.60" is a shape that
        # looks like a comparison; a reader cannot go and check it, and R7 rejects it.
        display = f"{b.name}, {b.source}" if b.source else b.name
        if b.n:
            display += f", N = {b.n}"
        everything.append(
            {
                "label": b.name,
                "score": round(float(val), 1),
                "n": b.n,
                "source": b.source,
                "sourceUrl": b.source_url,
                "protocol": b.protocol,
                "display": f"{display} — {float(val):.1f}",
            }
        )
        mapped = BASELINE_LABEL_MAP.get(b.name.strip().lower())
        if mapped:
            allowed.append(
                {
                    "label": mapped,
                    "score": round(float(val), 1),
                    "source": b.source,
                    "sourceUrl": b.source_url,
                    "n": b.n,
                }
            )
    return allowed, everything


def row_for(report: RunReport) -> dict[str, Any]:
    """A ``BenchmarkRow`` — plus the fields the current interface does not yet have."""
    score, kind = _pct_score(report)
    allowed, everything = _baselines(report)
    row: dict[str, Any] = {
        # --- fields the existing BenchmarkRow interface already declares -----
        "id": _slug(f"{report.benchmark}-{report.mode}"),
        "suite": report.benchmark_title,
        "task": _task_label(report),
        "modality": "voice" if report.mode == "voice" else "text",
        "score": score,
        "sampleSize": report.n_scored,
        "language": "en",
        "preliminary": report.preliminary,
        "note": _note(report),
        "artifact": f"results/whissle/{report.run_id}/REPORT.md",
        # --- additive: the honesty rules, carried as data --------------------
        "scoreKind": kind,
        "scoreNative": report.headline.value,
        "scoreNativeFormatted": report.headline.formatted(),
        "scoreNativeScale": (
            f"{report.headline.floor:g}–{report.headline.ceiling:g}"
            if report.headline.floor is not None
            else None
        ),
        "ci": list(report.headline.ci) if report.headline.ci else None,
        "attempted": report.exclusions.n_total,
        "excluded": report.exclusions.n_excluded,
        "exclusionRatePct": round(report.exclusions.rate_pct, 1),
        "judgeIndependent": report.judge.independent,
        "judgeKind": report.judge.kind,
        "comparable": report.baselines.comparable,
        "comparabilityNote": report.baselines.comparability_note,
        "date": report.date,
        "runId": report.run_id,
        "harnessCommit": report.provenance.harness_commit,
        "status": report.status,
    }
    passed = _passed(report)
    if passed is not None:
        row["passed"] = passed
    if allowed:
        row["baselines"] = allowed
    if everything:
        row["baselinesAll"] = everything
    return row


def _task_label(report: RunReport) -> str:
    return {
        "patientagentbench": "Patient-facing health assistant",
        "medagentbench": "Electronic health record — read and write",
        "agentclinic": "Diagnostic consultation",
        "flow_sim": "Conversation flows — real audio",
    }.get(report.benchmark, report.benchmark_title)


def flow_row_for(report: RunReport) -> list[dict[str, Any]]:
    """``FlowRow`` entries — one per headline/secondary rate on a flow run."""
    out = []
    metrics = [report.headline] + [
        m for m in report.secondary_metrics if m.unit == "pct"
    ]
    for m in metrics:
        if m.value is None or not m.n:
            continue
        passed = int(round(m.value / 100.0 * m.n))
        out.append(
            {
                "id": _slug(f"{report.series}-{m.key}"),
                "metric": m.label,
                "value": f"{passed} / {m.n}",
                "passed": passed,
                "sampleSize": m.n,
                "note": m.note,
                "preliminary": report.preliminary,
                "runId": report.run_id,
                "date": report.date,
                "artifact": f"results/whissle/{report.run_id}/REPORT.md",
            }
        )
    return out


def history_from_index(index: dict) -> dict[str, list[dict]]:
    """Per-benchmark trend, taken from the accumulated cross-run index.

    Only comparable points are emitted as a series — the index has already decided
    which runs measured the same thing the same way, and a chart that silently
    joins two different instruments is worse than no chart.
    """
    out: dict[str, list[dict]] = {}
    for bench, rows in (index.get("regression") or {}).items():
        series = []
        for r in rows:
            series.append(
                {
                    "runId": r.get("run_id"),
                    "date": r.get("date"),
                    "score": r.get("value"),
                    "scoreFormatted": r.get("value_formatted"),
                    "sampleSize": r.get("n_scored"),
                    "excluded": r.get("n_excluded"),
                    "judgeIndependent": r.get("judge_independent"),
                    "preliminary": r.get("preliminary"),
                    "deltaVsPrevious": r.get("delta_vs_previous"),
                    "comparabilityNote": r.get("comparability_note"),
                }
            )
        out[bench] = series
    return out


def build(reports: list[RunReport], index: Optional[dict] = None) -> dict[str, Any]:
    rows = [row_for(r) for r in reports if r.benchmark not in FLOW_BENCHMARKS]
    flow_rows: list[dict] = []
    for r in reports:
        if r.benchmark in FLOW_BENCHMARKS:
            flow_rows += flow_row_for(r)

    latest_date = max((r.date or "" for r in reports), default="")
    return {
        "schema": WEB_SCHEMA,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "placeholder": False,
        "lastUpdated": _human_date(latest_date),
        "benchmarkRepoUrl": BENCHMARK_REPO_URL,
        "rows": sorted(rows, key=lambda r: (-(r.get("score") or 0), r["id"])),
        "flowRows": flow_rows,
        "history": history_from_index(index or {}),
        "methodology": _methodology(reports),
        "honestNegatives": _honest_negatives(reports),
        "runCount": len(reports),
    }


def _human_date(iso: str) -> str:
    try:
        d = datetime.strptime(iso[:10], "%Y-%m-%d")
        return f"{d.day} {d.strftime('%B %Y')}"
    except Exception:
        return iso or ""


def _methodology(reports: list[RunReport]) -> list[dict[str, str]]:
    """Rendered as a definition list on the page. Kept short and specific."""
    modes = sorted({r.mode for r in reports})
    graders = sorted({r.judge.kind.replace("_", " ") for r in reports})
    return [
        {
            "term": "What is under test",
            "detail": (
                "The Whissle agent stack as deployed — the same brain, prompts and "
                "tools that serve production traffic, driven through each benchmark's "
                "own harness so only the agent differs from a published run."
            ),
        },
        {
            "term": "Scoring basis",
            "detail": (
                "Where a benchmark grades deterministically we use its grader unchanged "
                "— database state, not an opinion. Where it grades with a model we say "
                "which grader ran and whether it was independent of us; the row carries "
                "that flag, not the footnotes. Graders in play: "
                + ", ".join(graders)
                + "."
            ),
        },
        {
            "term": "Sample size",
            "detail": (
                "Every row states the N it was computed over. A run below "
                f"{PRELIMINARY_N_THRESHOLD} scored units is marked preliminary and "
                "should be read as directional."
            ),
        },
        {
            "term": "Exclusions",
            "detail": (
                "A run that never reached the agent — a transport error, a timeout — is "
                "excluded from the denominator and counted separately. The excluded "
                "count and its share sit on the row, because a score over 87 of 100 "
                "attempts is a different claim from a score over 100."
            ),
        },
        {
            "term": "User simulator",
            "detail": (
                "Each benchmark supplies its own user simulator and we do not swap it: "
                "a simulated patient, a simulated caller, or the benchmark's scripted "
                "task text. Modes measured: " + ", ".join(f"{m}" for m in modes) + "."
            ),
        },
        {
            "term": "Concurrency and pass^1",
            "detail": (
                "Tasks run with bounded concurrency against the live stack, and every "
                "figure is a single pass over each task — pass^1, no best-of-n, "
                "no retries on a task that failed on its merits. Only transport-level "
                "failures are retried, and a task that exhausts those retries is "
                "excluded rather than scored."
            ),
        },
        {
            "term": "Reproduction",
            "detail": (
                "Each row links a full report carrying the exact commands, the harness "
                "commit, the agent id and the raw trajectories the number came from."
            ),
        },
    ]


def _honest_negatives(reports: list[RunReport]) -> list[dict[str, str]]:
    """The things we would rather not put on a marketing page, generated from the
    runs so they cannot quietly stop being generated."""
    out: list[dict[str, str]] = []
    for r in reports:
        if r.exclusions.rate_pct >= 5.0:
            out.append(
                {
                    "title": f"{r.benchmark_title}: {r.exclusions.rate_pct:.0f}% of runs never completed",
                    "body": (
                        f"{r.exclusions.n_excluded} of {r.exclusions.n_total} sessions "
                        "failed at the transport layer before the agent could answer, and "
                        "are excluded from the score. That is an availability defect on "
                        "our side, it is the largest single finding in that run, and the "
                        f"headline describes the {r.n_scored} that survived."
                    ),
                }
            )
        if r.judge.independent is False:
            out.append(
                {
                    "title": f"{r.benchmark_title}: we graded our own homework",
                    "body": (
                        "The graders and simulators for this run were our own models. "
                        "Held constant it is a fair way to measure change over time; it "
                        "is not the same as an independent evaluation, and we do not "
                        "present it as one."
                    ),
                }
            )
        for lim in r.limitations:
            if lim.severity == "high" and lim.key in {
                "sandbox_not_a_hospital",
                "simulated_patient",
                "simulated_caller",
                "tiny_n",
            }:
                out.append({"title": f"{r.benchmark_title}: {lim.key.replace('_', ' ')}", "body": lim.text})
    seen = set()
    unique = []
    for item in out:
        if item["title"] in seen:
            continue
        seen.add(item["title"])
        unique.append(item)
    return unique[:8]


# --------------------------------------------------------------------------
# Validation — the same honesty rules, expressed over the export
# --------------------------------------------------------------------------

_USER_FACING_KEYS = ("suite", "task", "note", "metric", "value", "title", "body", "detail")


def validate(export: dict) -> list[Violation]:
    """Every rule the website's publishing gate enforces, plus ours, checked here so
    a bad file is caught by our tests rather than by their CI."""
    out: list[Violation] = []
    for row in export.get("rows", []) + export.get("flowRows", []):
        rid = row.get("id", "?")
        n = row.get("sampleSize")
        if not isinstance(n, int) or n <= 0:
            out.append(Violation("R1_headline_requires_n", "row has no positive sampleSize", rid))
        if not export.get("placeholder") and row.get("score") is None and "value" not in row:
            out.append(
                Violation("R1_headline_requires_n", "published row has a null score", rid)
            )
        if isinstance(n, int) and n < PRELIMINARY_N_THRESHOLD and not row.get("preliminary"):
            out.append(
                Violation(
                    "R4_preliminary_labelled",
                    f"sampleSize {n} < {PRELIMINARY_N_THRESHOLD} but preliminary is not set",
                    rid,
                )
            )
        if row.get("excluded") and "excluded" not in str(row.get("note", "")).lower():
            out.append(
                Violation(
                    "R3_exclusion_rate_adjacent",
                    "row excludes units but its note does not say so",
                    rid,
                )
            )
        if row.get("judgeIndependent") is False and "independent" not in str(
            row.get("note", "")
        ).lower():
            out.append(
                Violation(
                    "R2_judge_independence_disclosed",
                    "row was graded non-independently and its note does not say so",
                    rid,
                )
            )
        if row.get("score") is not None and "results/whissle/" not in str(row.get("artifact", "")):
            out.append(
                Violation(
                    "R7_artifact_linked",
                    "a published score must link the artifact that produced it",
                    rid,
                )
            )
        passed = row.get("passed")
        score = row.get("score")
        if isinstance(passed, int) and isinstance(score, (int, float)) and n:
            if passed > n:
                out.append(Violation("R8_arithmetic", "passed exceeds sampleSize", rid))
            if abs(passed / n * 100 - score) >= 0.5:
                out.append(
                    Violation("R8_arithmetic", "passed/sampleSize does not match score", rid)
                )
        for key in _USER_FACING_KEYS:
            m = _PROVIDER_RE.search(str(row.get(key) or ""))
            if m:
                out.append(
                    Violation(
                        "R5_no_provider_names",
                        f"{m.group(0)!r} appears in user-facing field {key!r}",
                        rid,
                    )
                )
    for block in ("honestNegatives",):
        for item in export.get(block, []):
            for key in ("title", "body"):
                m = _PROVIDER_RE.search(str(item.get(key) or ""))
                if m:
                    out.append(
                        Violation(
                            "R5_no_provider_names",
                            f"{m.group(0)!r} appears in {block}.{key}",
                            str(item.get("title"))[:40],
                        )
                    )
    from .honesty import VAGUE_BASELINE_TOKENS

    for row in export.get("rows", []):
        for b in (row.get("baselines") or []) + (row.get("baselinesAll") or []):
            label = str(b.get("label") or "")
            if any(t in label.lower() for t in VAGUE_BASELINE_TOKENS):
                out.append(
                    Violation(
                        "R7_baseline_named",
                        f"{label!r} describes a comparator instead of naming one",
                        str(row.get("id")),
                    )
                )
            if not (b.get("source") or "").strip():
                out.append(
                    Violation(
                        "R7_baseline_named",
                        f"comparator {label!r} reaches the page with no published source",
                        str(row.get("id")),
                    )
                )

    ids = [r.get("id") for r in export.get("rows", [])]
    if len(ids) != len(set(ids)):
        out.append(Violation("R9_unique_ids", "duplicate row ids", ""))
    if not any(
        m.get("term") == "What is under test" and "Whissle agent stack" in m.get("detail", "")
        for m in export.get("methodology", [])
    ):
        out.append(
            Violation(
                "R10_methodology_present",
                "the methodology block must state what is under test",
                "",
            )
        )
    return out


def write(path: Path, export: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(export, indent=2) + "\n", encoding="utf-8")
