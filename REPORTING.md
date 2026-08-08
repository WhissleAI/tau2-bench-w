# Benchmark reporting

One research-grade report per run, an index that accumulates across runs, and one
JSON file the public `/benchmark` page consumes.

```bash
python -m tau2.reporting all         # every run + INDEX.md + web/benchmark_export.json
python -m tau2.reporting build results/whissle/medagentbench/brain-parity_mab_100
python -m tau2.reporting check       # audit everything, write nothing (the CI shape)
python -m tau2.reporting list        # which run dirs are recognised, by which adapter
```

Nothing here runs a benchmark. Every command is a pure function of the artifacts
already on disk, so it is safe to re-run at any time over any run directory —
including one a benchmark is still writing into.

---

## Why this exists

Each adapter already wrote a `summary.json` and a short `REPORT.md`. Those are fine
for the person who launched the run and useless to anyone else, because they answer
"what was the number" and not the six questions that decide whether the number means
anything:

* over how many units, and how many were thrown away before the count?
* who graded it, and were they independent of us?
* what does it compare to, and what about that comparison does not hold?
* where exactly did it fail, in the agent's own words?
* what would a reviewer refuse to conclude from it?
* how do I re-run it?

A report that omits any of those is not shorter — it is a different, weaker claim
wearing the same number. This layer makes the strong version the only one the
generator can emit.

---

## The report

`REPORT.md` and a machine-readable `report.json` land next to the run's artifacts.
Sections, in the order the questions occur to a reader:

| § | Section | What it settles |
|---|---|---|
| — | **Abstract** | the headline with N, exclusion rate, judge independence, and the bounding analysis in the first three sentences |
| — | **At a glance** | headline, CI, attempted/scored/excluded, judge, mode, date, commit, run id |
| 1 | **What was measured, and why** | what the benchmark actually tests, and why that is worth testing |
| 2 | **Methodology** | agent under test, mode, endpoint, prompt handling, turn limits, tools bound, judge, scoring rule |
| 3 | **Setup and provenance** | agent id, base URL, dataset, harness commit, repo commit, capture time; **3.1** judge identity and independence; **3.2** sampling, seed, strata |
| 4 | **Results** | headline + components, confidence intervals, per-dimension / per-category / integrity tables |
| 5 | **Comparison to published baselines** | the table where one exists, and an explicit statement of what is and is not comparable. Where none exists, why it is *deliberately* empty |
| 6 | **Failure analysis** | categorised counts and rates, each with representative cases pulled from the real artifacts and quoted |
| 7 | **Exclusions** | how many, why, verbatim reasons, and a floor/ceiling bound on what they could be worth |
| 8 | **Limitations and threats to validity** | what a reviewer would object to, written down before they have to |
| 9 | **Reproduction** | exact commands, environment, and what will and will not reproduce |
| A | **Raw artifacts** | every file, present or missing |
| B | **Honesty-rule compliance** | the rules, executed against this document |

### The exclusion bound

The section that changes how a number reads. A run with a 13% exclusion rate does
not have a 13%-wide error bar — it has a range, and the range is computable from the
scale's floor and ceiling:

> Bounding it: if every excluded unit had scored at the floor of the scale, the
> all-100 figure would be **3.83**; at the ceiling, **4.35**. That interval is wider
> than the sampling confidence interval, which means the exclusions — not the sample
> size — are the dominant uncertainty in this run.

These are bounds, not estimates, and the report says so. It is arithmetic on the
run's own numbers, generated for every run with a non-empty exclusion set.

---

## The honesty rules

In `honesty.py`, executed against both the `RunReport` and the rendered Markdown. A
violation blocks generation unless the caller passes `--allow-violations`. Each rule
has a test that tampers with a passing report and asserts the rule fires — a rule
that cannot fail is decoration.

| Rule | What it forbids | How it is enforced |
|---|---|---|
| **R1** `headline_requires_n` | a headline number without its denominator | every occurrence of the headline value in the rendered document must carry `N = <n>` on the same line; a report that never states its own number also fails |
| **R2** `judge_independence_disclosed` | quoting a self-graded number as if it were independently graded | when `judge.independent is False`, `judge not independent` must appear beside every statement of the number, and the judge must carry an explanatory note rather than a bare boolean |
| **R3** `exclusion_rate_adjacent` | burying the exclusion rate in a footnote | when anything was excluded, `<k>/<n> excluded` must appear beside the number; the arithmetic must close; "we dropped some" with no breakdown is rejected |
| **R4** `preliminary_labelled` | presenting a small or unfinished run as settled | `N < 30` **or** an incomplete run directory forces `PRELIMINARY` into the qualifier, the banner and the index |
| **R5** `no_provider_names` | naming the underlying LLM vendor in agent-facing text | a vendor-name regex over the whole document, with two sanctioned exemptions: the published-baseline table and the judge-independence note, both wrapped in explicit HTML-comment spans. Vendor names quoted verbatim from error payloads are **redacted with a visible marker**, not dropped |
| **R6** `comparability_stated` | a baseline table with no statement of what does not transfer | `baselines.comparability_note` must be non-empty whenever baselines are shown |

The mechanism for R1–R4 is one machine-checkable **qualifier**:

```
4.25 (N = 87 · 13/100 excluded (13.0%) · judge not independent)
```

The renderer can only emit the headline through the helper that builds it; the
linter then re-derives the requirement from the report rather than trusting the
renderer, so an edit that bypasses the helper fails a test instead of shipping.

The two exemption spans are marked in the file itself:

```html
<!-- honesty:allow-providers -->  … published baselines / judge note …   <!-- /honesty:allow-providers -->
<!-- honesty:allow-context -->    … component and per-category figures … <!-- /honesty:allow-context -->
```

`allow-context` marks places where a number equal to the headline is arithmetic
rather than a second claim — a confidence-interval endpoint, a per-category rate, a
dimension mean. The claim itself lives in the abstract, the glance table, §4 and §7,
and those stay under the strict rule.

The same rules run over the website export (`web_export.validate`), expressed on the
data instead of the prose: a row cannot exist without `sampleSize`, a row with
exclusions must say so in its note, a row graded non-independently must say so, a row
under the threshold must set `preliminary`.

---

## The index

`results/whissle/INDEX.md` + `index.json` — every run ever recorded.

**It accumulates.** `index.json` is merged, never overwritten. A run whose directory
has since been deleted keeps its entry, flagged `artifacts_present: false`. History a
regeneration can silently erase is not history.

**It only compares like with like.** The regression view is keyed by `series_key` —
the *subject* under test, not the benchmark. Ten agent types share the flow
benchmark; diffing an appointment flow against a car-rental flow would put a
confident arrow on a comparison of two unrelated things. A delta is printed only when
the series, the metric, the mode, the judge's independence and the order of magnitude
of N all agree. Otherwise the reason appears where the arrow would be:

> not comparable to the previous run: sample sizes are not of the same order
> (N = 2 → N = 100); the difference would be mostly sampling noise

---

## The website export

`web/benchmark_export.json`, schema `whissle.benchmark.web/v1`. Field names are the
field names in the website repo's `benchmarkdata.ts`, so consuming it is mechanical.

The honesty rules travel *in the data*: `sampleSize`, `attempted`, `excluded`,
`exclusionRatePct`, `judgeIndependent`, `preliminary`, `comparabilityNote`,
`scoreKind`, and a generated `note` that assembles the required caveats. A rubric
score on a 1–5 scale is rescaled to 0–100 for the page **and** flagged
`scoreKind: "normalised_rubric"` with the native value alongside, because a rescaled
rubric is not a pass rate.

`publishable()` picks one run per benchmark by *authority* — largest scored N, latest
date breaking ties — so a two-case smoke run written five minutes ago cannot displace
the hundred-case result it was smoke-testing.

---

## Adding a benchmark

Two steps.

1. Write `src/tau2/reporting/adapters/<name>.py` with a class exposing:

```python
class MyBenchAdapter:
    benchmark = "mybench"
    benchmark_title = "MyBench"

    @classmethod
    def detect(cls, run_dir: Path) -> bool: ...      # filesystem only, never raises

    @classmethod
    def build(cls, run_dir: Path, ctx: BuildContext) -> RunReport: ...
```

2. Append it to `ADAPTERS` in `adapters/__init__.py`.

Nothing else changes. The renderer, the honesty rules, the index and the export read
`RunReport` and know nothing about benchmarks.

`build` must **degrade rather than crash**: a partial or corrupt run directory sets
`status="partial"`, explains in `partial_reason`, and reports whatever is present.
Run directories are read while runs are still writing into them, and a generator that
throws on a half-written tree is a generator nobody runs. `adapters/base.py` provides
the tolerant readers (`read_json`, `read_json_dir`, `dig`) that make this the path of
least resistance.

---

## Relationship to the other artifacts

* `summary.json` / `SUMMARY.json` — the harness's own aggregation. Still the source of
  truth for numbers; this layer reads it and never recomputes what it already says,
  except where it is absent.
* per-case `diagnostics` (`tau2.health.diagnostics/v1`, see `HEALTH_DIAGNOSTICS.md`) —
  the shared spine the failure analysis and provenance sections are pulled from.
  Availability flags are respected: an unmeasured signal is reported as absent, not as
  zero.
* the adapters' own short `REPORT.md` — superseded by this one, at the same path.

---

## Tests

`tests/test_reporting.py`. Fixtures are minimal hand-built run directories, not copies
of real runs, so a change in a benchmark's artifact shape surfaces as a failing
adapter test rather than quietly reshaping every expectation. Covered:

* a run with exclusions (bounds arithmetic, verbatim reasons, adjacency)
* a run with published baselines at less than the published N
* a run with no baseline registered (asserts the section is deliberately empty)
* a preliminary-N run
* a partial run with no `SUMMARY.json`, a corrupt case file, and an empty directory
* every honesty rule, exercised by tampering with a report that passes
* index accumulation, regeneration without duplication, and every refusal-to-diff
* export validation, rubric rescaling, baseline label mapping, and the website
  publishing gate's requirements
