"""Publishing a run to the benchmark results store.

Benchmark results live in the database, written here, so the public page can show
history, per-run detail and sample cases without a frontend deploy for every run.
This module is the wire format and the client.

**The envelope is the contract.** ``run_envelope()`` is the single definition of
what a published run looks like; the file the frontend used to read, the body this
client POSTs, and the object the store persists are all the same shape. There is no
second format to drift from.

**The honesty rules are the schema, not a convention.** ``sampleSize``,
``attempted``, ``excluded``, ``exclusionRatePct`` and ``judge.independent`` are
required top-level fields, always present and never implied. The store rejects a
run missing any of them; :func:`validate_envelope` runs the same check here so the
rejection happens in our tests rather than over the wire.

    export WHISSLE_BASE=https://aws-gateway-backend.whissle.ai/bot
    export WHISSLE_API_KEY=...
    python -m tau2.reporting publish results/whissle/medagentbench/brain-parity_mab_100
    python -m tau2.reporting all --publish

Idempotent on ``runId``: re-publishing a run updates it in place. That matters more
than it sounds — a run gets re-reported every time the generator improves, and a
store that appended would grow a fake history of a single measurement.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from .honesty import Violation, audit, qualifier
from .model import RunReport

#: Bumped only for a breaking change to the envelope. The store persists it beside
#: the run so a reader can tell which contract produced a row.
RUN_SCHEMA = "whissle.benchmark.run/v1"
INDEX_SCHEMA_WIRE = "whissle.benchmark.history/v1"

#: Where the store listens. Paths are relative to ``WHISSLE_BASE``.
RUNS_PATH = "/api/bench/results"
INDEX_PATH = "/api/bench/results/index"

DEFAULT_TIMEOUT = 60


# ---------------------------------------------------------------------------
# The envelope
# ---------------------------------------------------------------------------


def _metric(m) -> dict[str, Any]:
    d = {
        "key": m.key,
        "label": m.label,
        "unit": m.unit,
        "value": m.value,
        "valueFormatted": m.formatted(),
        "ci": list(m.ci) if m.ci else None,
        "n": m.n,
        "floor": m.floor,
        "ceiling": m.ceiling,
        "higherIsBetter": m.higher_is_better,
        "note": m.note,
    }
    return d


def _score_0_100(report: RunReport) -> tuple[Optional[float], str]:
    """A comparable 0–100 number for the page, and what kind of number it is.

    Shared with the file export so a rubric mean is rescaled identically in both,
    and flagged identically: a rescaled rubric is not a pass rate and the page must
    not be able to render it as one by accident.
    """
    from .web_export import _pct_score

    return _pct_score(report)


def exclusion_bounds(report: RunReport) -> Optional[dict[str, Any]]:
    from .render_md import _bounds

    b = _bounds(report)
    if not b:
        return None
    return {
        "floor": b[0],
        "ceiling": b[1],
        "note": (
            f"If every one of the {report.exclusions.n_excluded} excluded units had "
            f"scored at the floor of the scale the all-{report.exclusions.n_total} "
            f"figure would be {b[0]}; at the ceiling, {b[1]}. These are bounds, not "
            "estimates."
        ),
    }


def _baselines(report: RunReport) -> list[dict[str, Any]]:
    """Named comparators with their sources. A comparator without a source is not a
    comparator — see :func:`~tau2.reporting.honesty.check_baseline_labels`."""
    out = []
    for b in report.baselines.baselines:
        primary = b.values.get("overall") or next(iter(b.values.values()), None)
        out.append(
            {
                "label": b.name,
                "score": primary,
                "unit": report.headline.unit if report.headline.unit == "pct" else "score",
                "values": dict(b.values),
                "source": b.source,
                "sourceUrl": b.source_url,
                "protocol": b.protocol or report.baselines.published_protocol,
                "n": b.n,
                # everything a page needs to render "GPT-4o — MedAgentBench,
                # NEJM AI 2025, 300 tasks — 64.0" without inventing a label
                "display": _baseline_display(b, primary, report),
            }
        )
    return out


def _baseline_display(b, primary, report: RunReport) -> str:
    bits = [b.name]
    if b.source:
        bits.append(b.source)
    if b.n:
        bits.append(f"N = {b.n}")
    head = ", ".join(bits)
    if primary is None:
        return head
    val = f"{primary:.1f}%" if report.headline.unit == "pct" else f"{primary:.2f}"
    return f"{head} — {val}"


def run_envelope(report: RunReport, markdown: Optional[str] = None) -> dict[str, Any]:
    """The published shape of one run. The single definition of the wire format."""
    score, score_kind = _score_0_100(report)
    ex = report.exclusions
    j = report.judge

    return {
        "schema": RUN_SCHEMA,
        "generator": f"tau2.reporting ({report.schema})",
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # ---- identity. runId is the idempotency key. ----------------------
        "runId": report.run_id,
        "benchmark": report.benchmark,
        "benchmarkTitle": report.benchmark_title,
        "seriesKey": report.series,
        "title": report.title,
        "label": report.label,
        "mode": report.mode,
        "modality": "voice" if report.mode == "voice" else "text",
        "date": report.date,
        "status": report.status,
        "partialReason": report.partial_reason,
        # ---- mandatory honesty fields. The store rejects a run without these.
        "sampleSize": report.n_scored,
        "attempted": ex.n_total,
        "excluded": ex.n_excluded,
        "exclusionRatePct": round(ex.rate_pct, 1),
        "exclusionBreakdown": dict(ex.breakdown),
        "exclusionReasons": list(ex.reason_examples[:5]),
        "excludedIds": list(ex.excluded_ids),
        "exclusionBounds": exclusion_bounds(report),
        "preliminary": report.preliminary,
        "preliminaryReason": report.preliminary_reason,
        "judge": {
            "kind": j.kind,
            # `null` means no judge model is involved (deterministic grading) and is
            # a different fact from `false`. Both are acceptable; absence is not.
            "independent": j.independent,
            "provider": j.provider,
            "model": j.model,
            "endpoint": j.endpoint,
            "k": j.k,
            "note": j.note,
            "calls": j.calls,
            "costUsd": j.cost_usd,
            "summary": j.short,
        },
        "qualifier": qualifier(report),
        # ---- the numbers --------------------------------------------------
        "headline": {
            **_metric(report.headline),
            "score0to100": score,
            "scoreKind": score_kind,
        },
        "secondaryMetrics": [_metric(m) for m in report.secondary_metrics],
        "tables": [t.to_dict() for t in report.tables],
        # ---- comparison ---------------------------------------------------
        "baselines": _baselines(report),
        "baselineComparability": {
            "comparable": report.baselines.comparable,
            "note": report.baselines.comparability_note,
            "publishedProtocol": report.baselines.published_protocol,
            "source": report.baselines.source,
        },
        # ---- the parts that make a page worth visiting ---------------------
        "failures": [f.to_dict() for f in report.failures],
        "sampleCases": [c.to_dict() for c in report.sample_cases],
        "limitations": [x.to_dict() for x in report.limitations],
        "whatMeasured": report.what_measured,
        "whyMeasured": report.why_measured,
        "methodology": [{"term": k, "detail": v} for k, v in report.methodology],
        "scoringRule": report.scoring_rule,
        "provenance": report.provenance.to_dict(),
        "sampling": report.sampling.to_dict(),
        "reproduction": report.reproduction.to_dict(),
        "artifacts": [a.to_dict() for a in report.artifacts],
        "licenceNote": report.licence_note,
        "warnings": list(report.warnings),
        # The rendered report, so per-run detail renders server-side with no
        # frontend deploy and no second rendering implementation.
        "reportMarkdown": markdown,
        "reportPath": f"results/whissle/{report.run_id}/REPORT.md",
    }


def index_envelope(index: dict) -> dict[str, Any]:
    """The history document: one series per subject, with the comparability verdicts.

    Published as a whole rather than derived server-side on purpose. Deciding
    whether two runs may be diffed is a judgement about instruments — metric, mode,
    judge independence, order of magnitude of N — and it should have exactly one
    implementation. That one lives in ``index.regression_view``.
    """
    return {
        "schema": INDEX_SCHEMA_WIRE,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "nRuns": index.get("n_runs", 0),
        "runs": index.get("runs", []),
        "series": index.get("regression", {}),
    }


# ---------------------------------------------------------------------------
# Validation — the store's rejection rules, run locally first
# ---------------------------------------------------------------------------

#: Labels that describe a comparator without identifying one. "Frontier text agent"
#: is unfalsifiable: a reader cannot go and check it, and it quietly implies we beat
#: whatever they were imagining.
_VAGUE_BASELINE_TOKENS = (
    "frontier",
    "leading",
    "state of the art",
    "state-of-the-art",
    "sota",
    "competitor",
    "industry standard",
    "typical",
    "a baseline",
    "generic",
    "other agents",
    "commercial",
)


def validate_envelope(env: dict[str, Any]) -> list[Violation]:
    """Everything the store will reject a run for, checked before it is sent."""
    out: list[Violation] = []
    rid = str(env.get("runId") or "?")

    if not env.get("runId"):
        out.append(Violation("W1_run_id_required", "a run must have a runId to be idempotent on", rid))

    n = env.get("sampleSize")
    if not isinstance(n, int) or n <= 0:
        out.append(
            Violation("R1_headline_requires_n", "sampleSize must be a positive integer", rid)
        )

    for key in ("attempted", "excluded", "exclusionRatePct"):
        if env.get(key) is None:
            out.append(
                Violation(
                    "R3_exclusion_rate_adjacent",
                    f"{key} is mandatory — omitting it turns 'nothing was excluded' and "
                    "'we did not record exclusions' into the same value",
                    rid,
                )
            )
    att, exc = env.get("attempted"), env.get("excluded")
    if isinstance(att, int) and isinstance(exc, int) and isinstance(n, int):
        if n + exc != att:
            out.append(
                Violation(
                    "R3_exclusion_rate_adjacent",
                    f"exclusion arithmetic does not close: {n} + {exc} != {att}",
                    rid,
                )
            )
    if exc and not env.get("exclusionBreakdown"):
        out.append(
            Violation("R3_exclusion_rate_adjacent", "excluded units with no reason breakdown", rid)
        )

    judge = env.get("judge")
    if not isinstance(judge, dict) or "independent" not in judge:
        out.append(
            Violation(
                "R2_judge_independence_disclosed",
                "judge.independent is mandatory: true, false, or null for deterministic "
                "grading — but the key must be present",
                rid,
            )
        )
    elif judge.get("independent") is False and not (judge.get("note") or "").strip():
        out.append(
            Violation(
                "R2_judge_independence_disclosed",
                "a non-independent judge must carry an explanatory note",
                rid,
            )
        )

    if isinstance(n, int) and n < 30 and not env.get("preliminary"):
        out.append(
            Violation("R4_preliminary_labelled", f"sampleSize {n} < 30 but preliminary is not set", rid)
        )

    for b in env.get("baselines") or []:
        label = str(b.get("label") or "")
        if not label:
            out.append(Violation("R7_baseline_named", "a baseline with no label", rid))
            continue
        low = label.lower()
        if any(tok in low for tok in _VAGUE_BASELINE_TOKENS):
            out.append(
                Violation(
                    "R7_baseline_named",
                    f"{label!r} describes a comparator without naming one; a reader "
                    "cannot go and check it",
                    rid,
                )
            )
        if not (b.get("source") or "").strip():
            out.append(
                Violation(
                    "R7_baseline_named",
                    f"baseline {label!r} has no published source",
                    rid,
                )
            )
        if b.get("score") is None:
            out.append(Violation("R7_baseline_named", f"baseline {label!r} has no score", rid))
    if env.get("baselines") and not (env.get("baselineComparability") or {}).get("note"):
        out.append(
            Violation("R6_comparability_stated", "baselines published with no comparability note", rid)
        )
    return out


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


@dataclass
class PublishResult:
    run_id: str
    ok: bool
    status: int = 0
    detail: str = ""
    body: Optional[dict] = None

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{'✓' if self.ok else '✗'} {self.run_id}: {self.status} {self.detail}"


class BenchmarkStore:
    """Thin client for the results store. stdlib only, so the reporting layer stays
    importable in a bare interpreter."""

    def __init__(
        self,
        base: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.base = (base or os.getenv("WHISSLE_BASE") or "").rstrip("/")
        self.api_key = api_key or os.getenv("WHISSLE_API_KEY") or ""
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.base and self.api_key)

    def missing(self) -> str:
        gaps = []
        if not self.base:
            gaps.append("WHISSLE_BASE")
        if not self.api_key:
            gaps.append("WHISSLE_API_KEY")
        return ", ".join(gaps)

    def _send(self, path: str, payload: dict, method: str = "POST") -> tuple[int, str, Optional[dict]]:
        url = f"{self.base}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        # The store upserts on this; sending it as a header too lets a proxy or a
        # log tell one run's write from another without parsing the body.
        req.add_header("X-Bench-Run-Id", str(payload.get("runId") or payload.get("schema") or ""))
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
                try:
                    return resp.status, raw, json.loads(raw)
                except Exception:
                    return resp.status, raw, None
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace") if e.fp else ""
            return e.code, raw, None
        except Exception as e:
            return 0, f"{type(e).__name__}: {e}", None

    def publish_run(self, envelope: dict) -> PublishResult:
        rid = str(envelope.get("runId") or "?")
        status, raw, body = self._send(RUNS_PATH, envelope)
        ok = 200 <= status < 300
        detail = (body or {}).get("action") if isinstance(body, dict) else None
        return PublishResult(rid, ok, status, detail or raw[:200], body)

    def publish_index(self, envelope: dict) -> PublishResult:
        status, raw, body = self._send(INDEX_PATH, envelope, method="PUT")
        ok = 200 <= status < 300
        return PublishResult("index", ok, status, raw[:200], body)


def publish_reports(
    reports: list[tuple[RunReport, str]],
    index: Optional[dict] = None,
    *,
    store: Optional[BenchmarkStore] = None,
    dry_run: bool = False,
    allow_violations: bool = False,
) -> tuple[list[PublishResult], list[Violation]]:
    """Build, validate and send. Nothing is sent if anything fails validation.

    A partial publish is worse than none: it leaves the store holding some runs from
    this generation and some from the last, and the history view cannot tell.
    """
    envelopes: list[dict] = []
    violations: list[Violation] = []
    for report, md in reports:
        env = run_envelope(report, md)
        violations += audit(report, md)
        violations += validate_envelope(env)
        envelopes.append(env)

    if violations and not allow_violations:
        return [], violations
    if dry_run:
        return [PublishResult(e["runId"], True, 0, "dry-run") for e in envelopes], violations

    store = store or BenchmarkStore()
    if not store.configured:
        return [], violations + [
            Violation("W2_store_not_configured", f"missing {store.missing()}", "")
        ]

    results = [store.publish_run(e) for e in envelopes]
    if index is not None and all(r.ok for r in results):
        results.append(store.publish_index(index_envelope(index)))
    return results, violations
