"""The benchmark-agnostic intermediate representation a report is rendered from.

Every adapter's only job is to turn a run directory into a :class:`RunReport`.
Renderers, the cross-run index, the website export and the honesty rules all read
*this* — never a benchmark's private artifact shape. Adding a benchmark is therefore
one adapter file, not an edit to a monolith.

Pure stdlib on purpose: this module must import in a bare interpreter so the
generator can run over an archived results tree with no benchmark deps installed.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional

SCHEMA = "tau2.reporting.run_report/v1"

#: Below this many scored units a run is labelled PRELIMINARY, everywhere.
PRELIMINARY_N_THRESHOLD = 30

MetricUnit = Literal["score", "pct", "count", "ratio"]
RunStatus = Literal["complete", "partial"]
JudgeKind = Literal["llm_jury", "deterministic", "rule_analyzer", "unknown"]


def _round(x: Optional[float], nd: int = 2) -> Optional[float]:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return round(float(x), nd)


@dataclass
class Metric:
    """One measured quantity.

    ``floor``/``ceiling`` are the scale bounds — they are what makes the exclusion
    bounding analysis computable rather than hand-waved.
    """

    key: str
    label: str
    value: Optional[float]
    unit: MetricUnit = "score"
    ci: Optional[tuple[float, float]] = None
    n: Optional[int] = None
    floor: Optional[float] = None
    ceiling: Optional[float] = None
    higher_is_better: bool = True
    note: str = ""

    def formatted(self) -> str:
        if self.value is None:
            return "n/a"
        if self.unit == "pct":
            return f"{self.value:.1f}%"
        if self.unit == "count":
            return f"{int(self.value)}"
        return f"{self.value:.2f}"

    def ci_formatted(self) -> str:
        if not self.ci:
            return "—"
        lo, hi = self.ci
        if self.unit == "pct":
            return f"[{lo:.1f}%, {hi:.1f}%]"
        return f"[{lo:.2f}, {hi:.2f}]"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["value"] = _round(self.value)
        d["ci"] = list(self.ci) if self.ci else None
        d["formatted"] = self.formatted()
        return d


@dataclass
class Table:
    """A rendered results table. ``allow_providers`` marks the span as a place where
    external vendor names are legitimate (a published-baseline table)."""

    key: str
    title: str
    columns: list[str]
    rows: list[list[str]]
    note: str = ""
    allow_providers: bool = False
    #: Suppresses the headline-annotation requirement inside this table. Used for
    #: component/detail tables where a number equal to the headline is a coincidence
    #: of arithmetic, not a restatement of the claim.
    allow_context: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Exclusions:
    n_total: int = 0
    n_scored: int = 0
    n_excluded: int = 0
    breakdown: dict[str, int] = field(default_factory=dict)
    #: verbatim reason strings pulled off the artifacts, deduplicated
    reason_examples: list[str] = field(default_factory=list)
    #: case ids of excluded units, for the appendix
    excluded_ids: list[str] = field(default_factory=list)

    @property
    def rate_pct(self) -> float:
        return (100.0 * self.n_excluded / self.n_total) if self.n_total else 0.0

    @property
    def any(self) -> bool:
        return self.n_excluded > 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rate_pct"] = _round(self.rate_pct, 1)
        return d


@dataclass
class Judge:
    kind: JudgeKind = "unknown"
    provider: Optional[str] = None
    model: Optional[str] = None
    endpoint: Optional[str] = None
    #: ``None`` means "no judge is involved" (deterministic grading), which is a
    #: different fact from ``False`` ("a judge graded this and it was not independent").
    independent: Optional[bool] = None
    k: Optional[int] = None
    note: str = ""
    calls: Optional[int] = None
    cost_usd: Optional[float] = None

    @property
    def needs_disclosure(self) -> bool:
        return self.independent is False

    @property
    def short(self) -> str:
        if self.kind == "deterministic":
            return "deterministic grader (no judge model)"
        if self.kind == "rule_analyzer":
            return "rule analyzer + LLM grader"
        if self.independent is False:
            return f"{self.provider or 'unknown'} (NOT independent)"
        if self.independent is True:
            return f"{self.provider or 'unknown'} (independent)"
        return self.provider or "unknown"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["needs_disclosure"] = self.needs_disclosure
        d["short"] = self.short
        d["cost_usd"] = _round(self.cost_usd, 4)
        return d


@dataclass
class Provenance:
    agent_id: Optional[str] = None
    base_url: Optional[str] = None
    endpoint: Optional[str] = None
    mode: Optional[str] = None
    harness_commit: Optional[str] = None
    repo_commit: Optional[str] = None
    run_dir: Optional[str] = None
    captured_at: Optional[str] = None
    dataset: Optional[str] = None
    dataset_size: Optional[int] = None
    upstream: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Sampling:
    method: str = "unspecified"
    n_population: Optional[int] = None
    n_requested: Optional[int] = None
    n_selected: Optional[int] = None
    seed: Optional[int] = None
    strata_keys: list[str] = field(default_factory=list)
    #: rendered strata comparison tables (population % vs sample %)
    strata_tables: list[Table] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["strata_tables"] = [t.to_dict() for t in self.strata_tables]
        return d


@dataclass
class Baseline:
    """A published external comparator. Vendor model names are expected here and
    only here."""

    name: str
    values: dict[str, float]
    source: str = ""
    n: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BaselineSet:
    baselines: list[Baseline] = field(default_factory=list)
    #: True only when this run's protocol and N match the published protocol.
    comparable: bool = False
    comparability_note: str = ""
    #: what the paper's own setup was, when it differs from ours
    published_protocol: str = ""
    source: str = ""

    @property
    def any(self) -> bool:
        return bool(self.baselines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baselines": [b.to_dict() for b in self.baselines],
            "comparable": self.comparable,
            "comparability_note": self.comparability_note,
            "published_protocol": self.published_protocol,
            "source": self.source,
            "any": self.any,
        }


@dataclass
class FailureExample:
    case_id: str
    summary: str = ""
    evidence: str = ""
    artifact: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FailureCategory:
    key: str
    label: str
    count: int
    severity: str = "medium"
    description: str = ""
    denominator: Optional[int] = None
    examples: list[FailureExample] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rate_pct"] = (
            _round(100.0 * self.count / self.denominator, 1) if self.denominator else None
        )
        return d


@dataclass
class Limitation:
    key: str
    text: str
    severity: str = "medium"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Reproduction:
    commands: list[str] = field(default_factory=list)
    environment: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Artifact:
    path: str
    description: str = ""
    present: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunReport:
    """Everything one benchmark run's report needs, benchmark-agnostic."""

    run_id: str
    benchmark: str
    benchmark_title: str
    title: str
    headline: Metric
    schema: str = SCHEMA
    mode: str = ""
    label: str = "Whissle"
    #: What this run is a data point *of*, for the regression view. Two runs with the
    #: same series key measured the same subject the same way and may be diffed; two
    #: with different keys may not, however close their benchmark names look. The
    #: flow suite is the reason this is not just ``benchmark``: ten agent types share
    #: one benchmark and diffing across them produces nonsense with a confident arrow
    #: on it.
    series_key: str = ""
    date: Optional[str] = None
    status: RunStatus = "complete"
    partial_reason: str = ""

    what_measured: str = ""
    why_measured: str = ""
    #: ordered (label, value) rows: agent under test, endpoint, prompt handling,
    #: turn limits, tools bound, scoring rule, …
    methodology: list[tuple[str, str]] = field(default_factory=list)
    scoring_rule: str = ""

    secondary_metrics: list[Metric] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    exclusions: Exclusions = field(default_factory=Exclusions)
    judge: Judge = field(default_factory=Judge)
    provenance: Provenance = field(default_factory=Provenance)
    sampling: Sampling = field(default_factory=Sampling)
    baselines: BaselineSet = field(default_factory=BaselineSet)
    failures: list[FailureCategory] = field(default_factory=list)
    limitations: list[Limitation] = field(default_factory=list)
    reproduction: Reproduction = field(default_factory=Reproduction)
    artifacts: list[Artifact] = field(default_factory=list)
    licence_note: str = ""
    warnings: list[str] = field(default_factory=list)

    # ---- derived, and deliberately not settable by an adapter -------------

    @property
    def n_scored(self) -> int:
        return self.exclusions.n_scored or (self.headline.n or 0)

    @property
    def preliminary(self) -> bool:
        """Small-N or an incomplete run. Either way the number is not final."""
        return self.n_scored < PRELIMINARY_N_THRESHOLD or self.status == "partial"

    @property
    def preliminary_reason(self) -> str:
        bits = []
        if self.n_scored < PRELIMINARY_N_THRESHOLD:
            bits.append(
                f"N = {self.n_scored} is below the {PRELIMINARY_N_THRESHOLD}-unit "
                "threshold for a settled number"
            )
        if self.status == "partial":
            bits.append(self.partial_reason or "the run directory is incomplete")
        return "; ".join(bits)

    @property
    def series(self) -> str:
        return self.series_key or f"{self.benchmark}:{self.mode}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "run_id": self.run_id,
            "benchmark": self.benchmark,
            "series_key": self.series,
            "benchmark_title": self.benchmark_title,
            "title": self.title,
            "label": self.label,
            "mode": self.mode,
            "date": self.date,
            "status": self.status,
            "partial_reason": self.partial_reason,
            "preliminary": self.preliminary,
            "preliminary_reason": self.preliminary_reason,
            "n_scored": self.n_scored,
            "headline": self.headline.to_dict(),
            "secondary_metrics": [m.to_dict() for m in self.secondary_metrics],
            "what_measured": self.what_measured,
            "why_measured": self.why_measured,
            "methodology": [list(r) for r in self.methodology],
            "scoring_rule": self.scoring_rule,
            "tables": [t.to_dict() for t in self.tables],
            "exclusions": self.exclusions.to_dict(),
            "judge": self.judge.to_dict(),
            "provenance": self.provenance.to_dict(),
            "sampling": self.sampling.to_dict(),
            "baselines": self.baselines.to_dict(),
            "failures": [f.to_dict() for f in self.failures],
            "limitations": [x.to_dict() for x in self.limitations],
            "reproduction": self.reproduction.to_dict(),
            "artifacts": [a.to_dict() for a in self.artifacts],
            "licence_note": self.licence_note,
            "warnings": list(self.warnings),
        }
