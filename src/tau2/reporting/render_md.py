"""Render a :class:`~tau2.reporting.model.RunReport` as a reviewer-grade Markdown report.

The section order is the one a reader of a methods paper expects, because that is
the order in which the questions occur: what is the number, what was measured, how,
on what, what does it compare to, where did it fail, what was thrown away, what
would I not conclude from this, and how do I re-run it.

The renderer is the only place allowed to write the headline value, and it can only
write it through :func:`~tau2.reporting.honesty.headline_claim`, which carries the
N / exclusion / judge / preliminary annotations. The linter then re-checks the
output independently, so a future edit that bypasses the helper fails a test rather
than shipping a bare number.
"""

from __future__ import annotations

from typing import Optional

from .honesty import (
    ALLOW_CONTEXT_CLOSE,
    ALLOW_CONTEXT_OPEN,
    ALLOW_PROVIDERS_CLOSE,
    ALLOW_PROVIDERS_OPEN,
    compliance_table,
    headline_claim,
    qualifier,
    redact_providers,
)
from .model import PRELIMINARY_N_THRESHOLD, Metric, RunReport, Table

GENERATOR = "tau2.reporting"


def _table(t: Table) -> list[str]:
    if not t.rows:
        return []
    out: list[str] = []
    if t.allow_context:
        out.append(ALLOW_CONTEXT_OPEN)
    if t.allow_providers:
        out.append(ALLOW_PROVIDERS_OPEN)
    out.append(f"**{t.title}**")
    out.append("")
    out.append("| " + " | ".join(t.columns) + " |")
    out.append("|" + "|".join(["---"] * len(t.columns)) + "|")
    for row in t.rows:
        cells = [str(c).replace("|", "\\|").replace("\n", " ") for c in row]
        out.append("| " + " | ".join(cells) + " |")
    if t.note:
        out.append("")
        out.append(t.note)
    if t.allow_providers:
        out.append(ALLOW_PROVIDERS_CLOSE)
    if t.allow_context:
        out.append(ALLOW_CONTEXT_CLOSE)
    out.append("")
    return out


def _kv(rows: list[tuple[str, Optional[str]]]) -> list[str]:
    rows = [(k, v) for k, v in rows if v not in (None, "", "None")]
    if not rows:
        return []
    out = ["| Field | Value |", "|---|---|"]
    for k, v in rows:
        out.append(f"| {k} | {str(v).replace('|', '\\|')} |")
    out.append("")
    return out


def _bounds(report: RunReport) -> Optional[tuple[float, float]]:
    """What the headline would be if every excluded unit had scored at the floor,
    and at the ceiling. Not an estimate — a bound. It is the only defensible way to
    say how much a 13% exclusion rate could be worth."""
    m = report.headline
    ex = report.exclusions
    if not ex.any or m.value is None or m.floor is None or m.ceiling is None:
        return None
    if not ex.n_total:
        return None
    lo = (m.value * ex.n_scored + m.floor * ex.n_excluded) / ex.n_total
    hi = (m.value * ex.n_scored + m.ceiling * ex.n_excluded) / ex.n_total
    return (round(lo, 2), round(hi, 2))


def _metric_line(m: Metric) -> str:
    ci = f" {m.ci_formatted()}" if m.ci else ""
    n = f", N = {m.n}" if m.n else ""
    note = f" — {m.note}" if m.note else ""
    return f"- **{m.label}:** {m.formatted()}{ci}{n}{note}"


def render(report: RunReport) -> str:  # noqa: C901 - a document, not a branch tree
    L: list[str] = []
    W = L.append
    q = qualifier(report)

    # ---------------------------------------------------------------- title
    W(f"# {report.title}")
    W("")
    if report.preliminary:
        W(
            f"> **PRELIMINARY** — {report.preliminary_reason}. Treat every figure below "
            "as directional."
        )
        W("")
    if report.status == "partial":
        W(f"> **Partial run.** {report.partial_reason}")
        W("")

    # ------------------------------------------------------------- abstract
    W("## Abstract")
    W("")
    W(
        f"{report.label} was evaluated on **{report.benchmark_title}** in "
        f"`{report.mode}` mode. The headline result is {headline_claim(report)} "
        f"for {report.headline.label.lower()}"
        + (f", 95% CI {report.headline.ci_formatted()}" if report.headline.ci else "")
        + "."
    )
    W("")
    W(report.what_measured)
    W("")
    if report.exclusions.any:
        b = _bounds(report)
        bound_txt = ""
        if b:
            bound_txt = (
                f" Had every excluded unit been scored at the floor of the scale the "
                f"figure would be {b[0]}; at the ceiling, {b[1]}. The true "
                f"all-{report.exclusions.n_total} value lies in that interval, and the "
                "headline is not it."
            )
        W(
            f"**{report.exclusions.n_excluded} of {report.exclusions.n_total} units "
            f"({report.exclusions.rate_pct:.1f}%) were excluded** before scoring — see "
            f"§7.{bound_txt}"
        )
        W("")
    if report.judge.needs_disclosure:
        W(
            "**The judge is not independent of the agent's vendor.** This number is a "
            "sound internal regression instrument and is not a leaderboard result; §3 "
            "says exactly why."
        )
        W("")

    # ----------------------------------------------------------- at a glance
    W("## At a glance")
    W("")
    # The headline row states the claim and carries its qualifiers. Everything below
    # it is supporting detail — including the confidence interval, whose endpoints can
    # coincide with the headline value on a saturated run without restating it.
    W("| Field | Value |")
    W("|---|---|")
    W(f"| **{report.headline.label}** | **{report.headline.formatted()}** ({q}) |")
    W(ALLOW_CONTEXT_OPEN)
    glance: list[tuple[str, Optional[str]]] = [
        ("95% CI", report.headline.ci_formatted() if report.headline.ci else None),
        ("Attempted / scored / excluded",
         f"{report.exclusions.n_total} / {report.exclusions.n_scored} / "
         f"{report.exclusions.n_excluded} ({report.exclusions.rate_pct:.1f}%)"),
        ("Judge", report.judge.short),
        ("Mode", f"`{report.mode}`"),
        ("Date", report.date),
        ("Harness commit", f"`{report.provenance.harness_commit}`" if report.provenance.harness_commit else None),
        ("Run id", f"`{report.run_id}`"),
        ("Status", "**PRELIMINARY**" if report.preliminary else "complete"),
    ]
    for k, v in glance:
        if v not in (None, "", "None"):
            W(f"| {k} | {str(v).replace('|', '\\|')} |")
    W(ALLOW_CONTEXT_CLOSE)
    W("")
    if report.secondary_metrics:
        # Components of the headline, not restatements of it: a secondary metric
        # that happens to equal the headline value is arithmetic, not a second claim.
        W(ALLOW_CONTEXT_OPEN)
        for m in report.secondary_metrics[:4]:
            W(_metric_line(m))
        W(ALLOW_CONTEXT_CLOSE)
        W("")

    # -------------------------------------------------- 1. what was measured
    W("## 1. What was measured, and why")
    W("")
    W(report.what_measured)
    W("")
    if report.why_measured:
        W(f"**Why this benchmark.** {report.why_measured}")
        W("")

    # ------------------------------------------------------- 2. methodology
    W("## 2. Methodology")
    W("")
    L += _kv([(k, v) for k, v in report.methodology])
    if report.scoring_rule:
        W(f"**Scoring rule.** {report.scoring_rule}")
        W("")

    # ------------------------------------------- 3. setup, provenance, judge
    W("## 3. Setup and provenance")
    W("")
    p = report.provenance
    L += _kv(
        [
            ("Agent id", f"`{p.agent_id}`" if p.agent_id else None),
            ("Base URL", f"`{p.base_url}`" if p.base_url else None),
            ("Transport endpoint", f"`{p.endpoint}`" if p.endpoint else None),
            ("Mode", f"`{p.mode}`" if p.mode else None),
            ("Dataset", p.dataset),
            ("Dataset size", str(p.dataset_size) if p.dataset_size else None),
            ("Upstream", p.upstream),
            ("Harness commit", f"`{p.harness_commit}`" if p.harness_commit else None),
            ("Repo commit at report time", f"`{p.repo_commit}`" if p.repo_commit else None),
            ("Captured at", p.captured_at),
            # Always repo-relative. An absolute path from whichever machine happened
            # to generate the report is not provenance — it is that machine's
            # filesystem layout, and publishing it leaks the operator's environment.
            ("Run directory", f"`results/whissle/{report.run_id}`"),
            (
                "Harness output directory",
                f"`{p.run_dir}`"
                if p.run_dir and not str(p.run_dir).startswith("/")
                else None,
            ),
        ]
        + [(k.replace("_", " ").capitalize(), str(v)) for k, v in (p.extra or {}).items() if v is not None]
    )

    W("### 3.1 Judge and its independence")
    W("")
    j = report.judge
    L += _kv(
        [
            ("Grading kind", j.kind.replace("_", " ")),
            ("Provider", f"`{j.provider}`" if j.provider else None),
            ("Model", f"`{j.model}`" if j.model else None),
            ("Endpoint", f"`{j.endpoint}`" if j.endpoint else None),
            (
                "Independent of the agent's vendor",
                {True: "**yes**", False: "**NO**", None: "n/a — no judge model is called"}[
                    j.independent
                ],
            ),
            ("K (grading passes)", str(j.k) if j.k else None),
            ("Judge calls", str(j.calls) if j.calls else None),
            ("Judge spend", f"${j.cost_usd:.4f}" if j.cost_usd else None),
        ]
    )
    if j.note:
        # Naming an *alternative independent grader* is the disclosure, not branding —
        # the one place a provider name earns its keep outside the baseline table.
        W(ALLOW_PROVIDERS_OPEN)
        W(f"> {j.note}")
        W(ALLOW_PROVIDERS_CLOSE)
        W("")

    W("### 3.2 Sampling and population")
    W("")
    s = report.sampling
    L += _kv(
        [
            ("Method", s.method),
            ("Population", str(s.n_population) if s.n_population else None),
            ("Requested", str(s.n_requested) if s.n_requested else None),
            ("Selected", str(s.n_selected) if s.n_selected else None),
            ("Scored", str(report.n_scored)),
            ("Seed", str(s.seed) if s.seed is not None else None),
            ("Strata", ", ".join(f"`{k}`" for k in s.strata_keys) if s.strata_keys else None),
        ]
    )
    if s.note:
        W(s.note)
        W("")
    for t in s.strata_tables:
        L += _table(t)

    # ----------------------------------------------------------- 4. results
    W("## 4. Results")
    W("")
    W(f"**{report.headline.label}: {report.headline.formatted()}** ({q})"
      + (f", 95% CI {report.headline.ci_formatted()}" if report.headline.ci else "")
      + ".")
    W("")
    if report.secondary_metrics:
        W("| Metric | Value | 95% CI | N | Qualifiers |")
        W("|---|---:|---|---:|---|")
        W(
            f"| **{report.headline.label}** | **{report.headline.formatted()}** | "
            f"{report.headline.ci_formatted()} | {report.headline.n or '—'} | {q} |"
        )
        W(ALLOW_CONTEXT_OPEN)
        for m in report.secondary_metrics:
            W(
                f"| {m.label} | {m.formatted()} | {m.ci_formatted()} | {m.n or '—'} | "
                f"{m.note or '—'} |"
            )
        W(ALLOW_CONTEXT_CLOSE)
        W("")
        W(
            "_The headline row is the claim and carries its qualifiers; the rest are "
            "components of it and inherit them._"
        )
        W("")
    for t in report.tables:
        L += _table(t)

    # -------------------------------------------------------- 5. comparison
    W("## 5. Comparison to published baselines")
    W("")
    if report.baselines.any:
        W(report.baselines.comparability_note)
        W("")
        keys: list[str] = []
        for b in report.baselines.baselines:
            for k in b.values:
                if k not in keys:
                    keys.append(k)
        cols = ["System", "N"] + [k.title() for k in keys]
        rows = [
            [
                f"**{report.label} (this run)**",
                str(report.n_scored),
            ]
            + [_our_value(report, k) for k in keys]
        ]
        for b in sorted(
            report.baselines.baselines,
            key=lambda b: -(b.values.get(keys[0], 0) if keys else 0),
        ):
            rows.append(
                [b.name, str(b.n or "—")]
                + [f"{b.values[k]:.1f}" if k in b.values else "—" for k in keys]
            )
        L += _table(
            Table(
                key="baselines",
                title=f"Published baselines — {report.baselines.source}",
                columns=cols,
                rows=rows,
                note=(
                    f"Published protocol: {report.baselines.published_protocol}. "
                    "External model names appear here and only here; they are published "
                    "comparators, not components of the system under test."
                ),
                allow_providers=True,
                allow_context=True,
            )
        )
        W(
            "**What is comparable:** the protocol — same prompts, same action grammar, "
            "same grader. **What is not:** the sample. "
            + ("This run matches the published N." if report.baselines.comparable
               else f"This run scored {report.n_scored}; the published figures are over "
                    f"{report.baselines.baselines[0].n or 'a different N'}.")
        )
        W("")
    else:
        W(report.baselines.comparability_note or
          "No published baseline is registered for this benchmark in this harness.")
        W("")
        W(
            "_An empty comparison section is a result. Printing a number next to a "
            "differently-measured one would not be._"
        )
        W("")

    # -------------------------------------------------- 6. failure analysis
    W("## 6. Failure analysis")
    W("")
    if not report.failures:
        W("_No categorised failures were recorded for this run._")
        W("")
    else:
        W("| Category | Count | Rate | Severity |")
        W("|---|---:|---:|---|")
        W(ALLOW_CONTEXT_OPEN)
        for f in report.failures:
            rate = (
                f"{100.0 * f.count / f.denominator:.1f}%" if f.denominator else "—"
            )
            W(f"| {f.label} | {f.count} | {rate} | {f.severity} |")
        W(ALLOW_CONTEXT_CLOSE)
        W("")
        for f in report.failures:
            denom = f" of {f.denominator}" if f.denominator else ""
            W(f"### 6.{report.failures.index(f) + 1} {f.label} — {f.count}{denom}")
            W("")
            if f.description:
                # A failure description is, by definition, about a subset: the rates
                # in it are per-category and cannot be a restatement of the headline
                # claim. The claim itself lives in the abstract, the glance table,
                # §4 and §7, all of which stay under the strict annotation rule.
                W(ALLOW_CONTEXT_OPEN)
                W(f.description)
                W(ALLOW_CONTEXT_CLOSE)
                W("")
            for ex in f.examples:
                W(f"- **`{ex.case_id}`**" + (f" — {ex.summary}" if ex.summary else ""))
                if ex.evidence:
                    # Quoted verbatim from the artifact — except for vendor names,
                    # which are replaced with a visible marker rather than dropped.
                    ev = redact_providers(ex.evidence.replace("\n", " ").strip())
                    W(f"  > {ev}")
                if ex.artifact:
                    W(f"  _artifact:_ `{ex.artifact}`")
            if f.examples:
                W("")

    # ------------------------------------------------------- 7. exclusions
    W("## 7. Exclusions and what they do to the number")
    W("")
    ex = report.exclusions
    if not ex.any:
        W(
            f"Nothing was excluded: all {ex.n_total} attempted units produced a "
            "gradable result. The headline denominator is the full attempted set."
        )
        W("")
    else:
        W(ALLOW_CONTEXT_OPEN)
        W(
            f"| Attempted | Scored | Excluded | Exclusion rate |\n|---:|---:|---:|---:|\n"
            f"| {ex.n_total} | {ex.n_scored} | {ex.n_excluded} | "
            f"**{ex.rate_pct:.1f}%** |"
        )
        W(ALLOW_CONTEXT_CLOSE)
        W("")
        W("**Why each unit was excluded**")
        W("")
        W("| Reason | Count | Share of attempted |")
        W("|---|---:|---:|")
        W(ALLOW_CONTEXT_OPEN)
        for k, v in sorted(ex.breakdown.items(), key=lambda kv: -kv[1]):
            W(f"| `{k}` | {v} | {100.0 * v / ex.n_total:.1f}% |")
        W(ALLOW_CONTEXT_CLOSE)
        W("")
        if ex.reason_examples:
            W("Verbatim, from the artifacts:")
            W("")
            for r in ex.reason_examples[:3]:
                W(f"> `{redact_providers(r[:300])}`")
                W("")
        b = _bounds(report)
        W("**Effect on interpretation.**")
        W("")
        W(
            f"An exclusion rate of {ex.rate_pct:.1f}% is not a rounding detail. The "
            f"headline describes {ex.n_scored} units; it is silent about "
            f"{ex.n_excluded}."
        )
        if b:
            W("")
            W(
                f"Bounding it: if every excluded unit had scored at the floor of the "
                f"scale, the all-{ex.n_total} figure would be **{b[0]}**; at the "
                f"ceiling, **{b[1]}**. That interval is wider than the sampling "
                "confidence interval, which means the exclusions — not the sample "
                "size — are the dominant uncertainty in this run. These are bounds, "
                "not estimates: nobody knows how the excluded units would have scored."
            )
        W("")
        W(
            "The excluded set is also unlikely to be random with respect to "
            "difficulty. Transport failures accumulate over turns, so longer and "
            "harder units are more exposed to them, and the scored set is plausibly "
            "the easier half of what was drawn."
        )
        W("")
        if ex.excluded_ids:
            W(
                "<details><summary>Excluded unit ids ("
                + str(len(ex.excluded_ids))
                + ")</summary>\n\n"
                + ", ".join(f"`{i}`" for i in ex.excluded_ids)
                + "\n\n</details>"
            )
            W("")

    # ------------------------------------------------------ 8. limitations
    W("## 8. Limitations and threats to validity")
    W("")
    if report.limitations:
        for lim in report.limitations:
            W(f"- **{lim.key.replace('_', ' ')}** ({lim.severity}) — {lim.text}")
        W("")
    if report.n_scored < PRELIMINARY_N_THRESHOLD:
        W(
            f"- **sample size** (high) — N = {report.n_scored} is below the "
            f"{PRELIMINARY_N_THRESHOLD}-unit threshold this reporting layer uses to "
            "call a figure settled. The report is labelled PRELIMINARY throughout."
        )
        W("")

    # ----------------------------------------------------- 9. reproduction
    W("## 9. Reproduction")
    W("")
    if report.reproduction.commands:
        W("```bash")
        for c in report.reproduction.commands:
            W(c)
        W("```")
        W("")
    if report.reproduction.environment:
        L += _kv([(k, v) for k, v in report.reproduction.environment.items()])
    for n in report.reproduction.notes:
        W(f"- {n}")
    if report.reproduction.notes:
        W("")

    # ------------------------------------------------------- appendix A/B
    W("## Appendix A — raw artifacts")
    W("")
    W("| Path | Present | What it is |")
    W("|---|:---:|---|")
    for a in report.artifacts:
        W(f"| `{a.path}` | {'yes' if a.present else '**missing**'} | {a.description} |")
    W("")
    W(
        "Every per-case record carries a `diagnostics` block "
        "(`tau2.health.diagnostics/v1`) with flow trace, signals, metadata sidecar, "
        "tool forensics, provenance and cost — and explicit availability flags, so an "
        "absent measurement reads as absent rather than as zero. See "
        "`HEALTH_DIAGNOSTICS.md`."
    )
    W("")

    W("## Appendix B — honesty-rule compliance")
    W("")
    W(
        "These rules are executed against this document, not asserted about it. A "
        "failing rule blocks generation."
    )
    W("")
    W("| Rule | Verdict | Checked |")
    W("|---|:---:|---|")
    for rule, verdict, what in compliance_table(report, None):
        W(f"| `{rule}` | {verdict} | {what} |")
    W("")

    if report.warnings:
        W("**Generator warnings**")
        W("")
        for w in report.warnings:
            W(f"- {w}")
        W("")

    if report.licence_note:
        W("---")
        W("")
        W(f"_{report.licence_note}_")
        W("")
    W(
        f"<!-- generated by {GENERATOR} from {report.run_id}; "
        f"schema {report.schema} -->"
    )
    return "\n".join(L) + "\n"


def _our_value(report: RunReport, key: str) -> str:
    """Our own figure for a baseline column, from the headline or a secondary metric."""
    aliases = {
        "overall": ("success_rate", "accuracy", "aggregate", "task_success"),
        "query": ("query_success_rate",),
        "action": ("action_success_rate",),
    }
    wanted = aliases.get(key, (key,))
    if report.headline.key in wanted and report.headline.value is not None:
        return f"**{report.headline.value:.1f}**"
    for m in report.secondary_metrics:
        if m.key in wanted and m.value is not None:
            return f"**{m.value:.1f}**"
    return "—"
