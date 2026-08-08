# Copyright Sierra
"""Baselines, and the provenance taxonomy that keeps two incompatible kinds apart.

THE PROBLEM THIS SOLVES
-----------------------
There are exactly two ways a competitor number gets into a comparison, and they
are not interchangeable:

``setup_matched``
    We ran both systems ourselves, on the same scenario file, in the same week,
    scored by the same criteria. Requires credentials and a live agent on both
    sides. This is the only kind that supports a head-to-head verdict.

``published_external``
    A number quoted from the vendor's own published material — a docs page, a
    benchmark post, a press release. We did not run it. We usually cannot even
    tell what it measured: a different task set, a different scoring rule, a
    different definition of "success". It is *context*, never a comparator.

Quoting a published number against a number we measured is the single most
common way a vendor-comparison document becomes dishonest, and it is almost
always accidental — the two numbers look alike once they are in the same table.
So the taxonomy is enforced structurally rather than by convention:

* a ``published_external`` baseline **cannot be constructed** without a citation
  URL, a publication date, the vendor's exact metric definition, and an explicit
  list of what we could not match (:class:`Baseline` raises otherwise);
* :func:`mixing_warning` fires the moment both kinds appear in one comparison,
  and the renderers print that warning where the number is, not in a footnote;
* :meth:`Baseline.label` prefixes every published number with a visible marker so
  it cannot be skimmed as one of ours.

The precedent is ``tau2.health.medagent.data.PUBLISHED_BASELINES`` — the
MedAgentBench paper's leaderboard, carried inline so a reader need not go find
the paper. That dict is exactly a set of ``published_external`` baselines that
predates this taxonomy; :func:`medagent_published_baselines` re-expresses it in
this shape so the two paths cannot drift.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

SETUP_MATCHED = "setup_matched"
PUBLISHED_EXTERNAL = "published_external"
KINDS = (SETUP_MATCHED, PUBLISHED_EXTERNAL)

#: Printed wherever a published number sits next to one we measured.
MIXED_KINDS_WARNING = (
    "MIXED PROVENANCE — this view places numbers WE MEASURED beside numbers "
    "QUOTED from a vendor's published material. They are not comparable: the "
    "published numbers come from a task set, scoring rule and setup we did not "
    "control and in most cases cannot inspect. Read the published rows as "
    "context for what the vendor claims, never as a head-to-head result."
)

#: The marker that precedes every published number in rendered output.
PUBLISHED_MARKER = "[VENDOR-PUBLISHED — NOT MEASURED BY US]"

#: Why a comparison cannot produce a verdict, by cause. These strings are the
#: report's ``cannot_compare_reason`` values.
NO_SETUP_MATCHED = (
    "no setup_matched run exists — we did not run both systems ourselves on these "
    "scenarios, so there is no head-to-head result to report. Published vendor "
    "numbers cannot stand in for one."
)
ONE_SIDED = (
    "only one vendor produced a runnable result, so this is a single-vendor "
    "measurement and not a comparison"
)


class BaselineError(ValueError):
    """A baseline that cannot be constructed honestly."""


@dataclass(frozen=True)
class Baseline:
    """One vendor's number for one metric, stamped with how we came to have it.

    ``kind`` decides which fields are mandatory. The constructor refuses to build
    a ``published_external`` baseline that is missing its citation, its date, the
    vendor's own metric definition, or the ``unmatched`` list — those four are the
    difference between a citation and a rumour, and a rumour in a comparison table
    is indistinguishable from a measurement."""

    vendor: str
    metric: str
    value: Optional[float]
    kind: str

    unit: Optional[str] = None
    # ── published_external: all four REQUIRED ────────────────────────────────
    citation_url: Optional[str] = None
    publication_date: Optional[str] = None
    metric_definition: Optional[str] = None
    #: What about the vendor's setup we could NOT match — task set, scoring rule,
    #: audio conditions, model version. An empty list is a claim that we matched
    #: everything, which for a published number is essentially never true, so it
    #: is rejected.
    unmatched: tuple[str, ...] = ()
    # ── setup_matched provenance ─────────────────────────────────────────────
    run_id: Optional[str] = None
    scenario_ids: tuple[str, ...] = ()
    harness_commit: Optional[str] = None
    captured_at: Optional[str] = None
    notes: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise BaselineError(
                f"unknown baseline kind {self.kind!r}; expected one of {KINDS}"
            )
        if self.kind == PUBLISHED_EXTERNAL:
            missing = [
                name
                for name in ("citation_url", "publication_date", "metric_definition")
                if not getattr(self, name)
            ]
            if missing:
                raise BaselineError(
                    f"published_external baseline for {self.vendor}/{self.metric} is "
                    f"missing required provenance: {', '.join(missing)}. A quoted "
                    "number without a citation, a date and the vendor's own metric "
                    "definition cannot be shown next to a number we measured."
                )
            if not self.unmatched:
                raise BaselineError(
                    f"published_external baseline for {self.vendor}/{self.metric} "
                    "declares nothing in `unmatched`. State what about the vendor's "
                    "setup we could not reproduce — task set, scoring rule, audio "
                    "conditions, model version. If we genuinely matched everything, "
                    "it is a setup_matched run, not a quote."
                )
        elif self.kind == SETUP_MATCHED and not self.scenario_ids:
            raise BaselineError(
                f"setup_matched baseline for {self.vendor}/{self.metric} names no "
                "scenario_ids. A matched run has to say what it ran."
            )

    # ── rendering ────────────────────────────────────────────────────────────

    @property
    def is_measured(self) -> bool:
        """True only for numbers this harness produced itself."""
        return self.kind == SETUP_MATCHED

    def label(self) -> str:
        """The vendor label as it must appear in output — published numbers carry
        :data:`PUBLISHED_MARKER` so they cannot be skimmed as ours."""
        return (
            self.vendor
            if self.is_measured
            else f"{self.vendor} {PUBLISHED_MARKER}"
        )

    def rendered_value(self) -> str:
        if self.value is None:
            return "n/a"
        unit = self.unit or ""
        return f"{self.value:g}{unit}"

    def provenance_line(self) -> str:
        """One line stating where this number came from, for the row's footnote."""
        if self.is_measured:
            bits = [f"measured by this harness over {len(self.scenario_ids)} scenario(s)"]
            if self.run_id:
                bits.append(f"run `{self.run_id}`")
            if self.harness_commit:
                bits.append(f"commit `{self.harness_commit}`")
            if self.captured_at:
                bits.append(self.captured_at)
            return "; ".join(bits)
        bits = [
            f"quoted from {self.citation_url}",
            f"published {self.publication_date}",
            f'vendor\'s metric: "{self.metric_definition}"',
            "could NOT match: " + "; ".join(self.unmatched),
        ]
        return " — ".join(bits)

    def to_dict(self) -> dict[str, Any]:
        return {
            "vendor": self.vendor,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "kind": self.kind,
            "is_measured": self.is_measured,
            "label": self.label(),
            "provenance": self.provenance_line(),
            "citation_url": self.citation_url,
            "publication_date": self.publication_date,
            "metric_definition": self.metric_definition,
            "unmatched": list(self.unmatched),
            "run_id": self.run_id,
            "scenario_ids": list(self.scenario_ids),
            "harness_commit": self.harness_commit,
            "captured_at": self.captured_at,
            "notes": self.notes,
            **self.extra,
        }


def setup_matched(
    vendor: str,
    metric: str,
    value: Optional[float],
    *,
    scenario_ids: list[str] | tuple[str, ...],
    unit: Optional[str] = None,
    run_id: Optional[str] = None,
    harness_commit: Optional[str] = None,
    notes: Optional[str] = None,
    **extra: Any,
) -> Baseline:
    """A number this harness produced by running the system itself."""
    return Baseline(
        vendor=vendor,
        metric=metric,
        value=value,
        kind=SETUP_MATCHED,
        unit=unit,
        scenario_ids=tuple(scenario_ids),
        run_id=run_id,
        harness_commit=harness_commit,
        captured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        notes=notes,
        extra=dict(extra),
    )


def published_external(
    vendor: str,
    metric: str,
    value: Optional[float],
    *,
    citation_url: str,
    publication_date: str,
    metric_definition: str,
    unmatched: list[str] | tuple[str, ...],
    unit: Optional[str] = None,
    notes: Optional[str] = None,
    **extra: Any,
) -> Baseline:
    """A number quoted from a vendor's published material. All four provenance
    arguments are required — see :class:`Baseline`."""
    return Baseline(
        vendor=vendor,
        metric=metric,
        value=value,
        kind=PUBLISHED_EXTERNAL,
        unit=unit,
        citation_url=citation_url,
        publication_date=publication_date,
        metric_definition=metric_definition,
        unmatched=tuple(unmatched),
        notes=notes,
        extra=dict(extra),
    )


# ── collections ─────────────────────────────────────────────────────────────────


def kinds_present(baselines: list[Baseline]) -> set[str]:
    return {b.kind for b in baselines}


def has_setup_matched(baselines: list[Baseline]) -> bool:
    return any(b.is_measured for b in baselines)


def mixing_warning(baselines: list[Baseline]) -> Optional[str]:
    """:data:`MIXED_KINDS_WARNING` when both kinds are present, else ``None``."""
    return MIXED_KINDS_WARNING if len(kinds_present(baselines)) > 1 else None


def render_table(baselines: list[Baseline]) -> str:
    """A Markdown table in which a published number cannot be mistaken for ours.

    The mixing warning, when it applies, is printed ABOVE the table rather than
    under it: a reader who reads one line reads the caveat, not the numbers."""
    if not baselines:
        return "_No baselines._\n"
    lines: list[str] = []
    warning = mixing_warning(baselines)
    if warning:
        lines += [f"> **{warning}**", ""]
    lines += [
        "| System | Metric | Value | Provenance |",
        "| --- | --- | --- | --- |",
    ]
    for b in baselines:
        lines.append(
            f"| {b.label()} | {b.metric} | {b.rendered_value()} | "
            f"{b.provenance_line()} |"
        )
    lines.append("")
    return "\n".join(lines)


def medagent_published_baselines(
    metric: str = "overall",
) -> list[Baseline]:
    """``tau2.health.medagent.data.PUBLISHED_BASELINES`` re-expressed in this
    taxonomy.

    That dict is the MedAgentBench (NEJM AI 2025) leaderboard, already carried in
    the repo for the MedAgentBench report. It is the canonical example of a
    ``published_external`` set: a different task surface, a different grader, and
    models we never ran. Re-expressing it here rather than copying the numbers
    keeps one source of truth, and forces the same visible marker onto them."""
    from tau2.health.medagent.data import PUBLISHED_BASELINES

    out: list[Baseline] = []
    for model, scores in PUBLISHED_BASELINES.items():
        if metric not in scores:
            continue
        out.append(
            published_external(
                vendor=model,
                metric=f"medagentbench_{metric}",
                value=scores[metric],
                unit="%",
                citation_url=(
                    "https://github.com/stanfordmlgroup/MedAgentBench"
                ),
                publication_date="2025",
                metric_definition=(
                    "task success rate over the 300-task MedAgentBench set, graded "
                    "by the paper's own per-category reference solutions"
                ),
                unmatched=(
                    "different task set — MedAgentBench FHIR tasks, not these "
                    "conversational scenarios",
                    "different grader — the paper's reference solutions, not this "
                    "package's per-scenario criteria",
                    "model versions and decoding parameters were not disclosed in a "
                    "form we could reproduce",
                ),
                notes="carried from tau2.health.medagent.data.PUBLISHED_BASELINES",
            )
        )
    return out
