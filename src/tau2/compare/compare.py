# Copyright Sierra
"""Turning runs into a comparison — and refusing to, when that is the honest answer.

THE REFUSAL IS THE FEATURE
--------------------------
Most of this module is about *not* emitting a verdict. A head-to-head requires a
``setup_matched`` pair (:mod:`tau2.compare.baselines`): both systems driven by us,
on the same scenario, hearing the same utterances. Absent that, the only truthful
output is ``cannot_compare`` with a reason, and :func:`compare_scenario` returns
exactly that rather than degrading into "well, Whissle scored 5/6".

Three specific traps are closed here:

*No competitor run → no comparison.* A vendor that could not be reached did not
lose. :func:`compare_scenario` yields ``cannot_compare`` / :data:`ONE_SIDED`, and
the run is reported as a Whissle-only measurement.

*Divergent utterances → no comparison.* If the vendor's simulated user drifted
off script (``utterances_matched: False``), the two systems did not hear the same
call. Same refusal.

*Unknown → never resolved toward Whissle.* Wherever either side's criterion
verdict is ``None``, the head-to-head is ``cannot_tell``. There is deliberately
no rule anywhere in this file of the form "if we cannot tell, credit the home
vendor" — including the tempting one where Whissle has a trace and the vendor
does not. Having more evidence is not the same as having won.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from tau2.compare import baselines as bl
from tau2.compare import criteria as crit
from tau2.compare import evidence as ev
from tau2.compare import honesty
from tau2.compare.vendors import HOME_VENDOR
from tau2.compare.vendors.base import ScenarioRun

WIN = "whissle_wins"
LOSS = "whissle_loses"
TIE = "tie"
CANNOT_TELL = "cannot_tell"
CANNOT_COMPARE = "cannot_compare"

#: Why a scenario produced no head-to-head. Distinct from CANNOT_TELL: one says
#: "we had no comparable pair", the other says "we had a pair and the result is
#: not legible".
REASON_UNMATCHED_UTTERANCES = (
    "the two systems did not hear the same utterances, so this is not a matched "
    "pair and no head-to-head can be drawn from it"
)


@dataclass
class VendorOutcome:
    """One vendor's result on one scenario."""

    vendor: str
    run: ScenarioRun
    checks: list[crit.CheckResult] = field(default_factory=list)
    passed: Optional[bool] = None
    reason: str = ""
    evidence: Optional[ev.EvidenceResult] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "vendor": self.vendor,
            "measured": self.run.measured,
            "runnable": self.run.runnable,
            "not_runnable_reason": self.run.not_runnable_reason,
            "error": self.run.error,
            "outcome": (
                "cannot_tell" if self.passed is None
                else ("pass" if self.passed else "fail")
            ),
            "passed": self.passed,
            "reason": self.reason,
            "setup_caveats": list(self.run.setup_caveats),
            "checks": [c.to_dict() for c in self.checks],
            "trace_evidence": self.evidence.to_dict() if self.evidence else None,
            "transcript": [t.to_dict() for t in self.run.turns],
        }


@dataclass
class ScenarioComparison:
    """One scenario across every vendor that was attempted."""

    scenario: Any
    outcomes: dict[str, VendorOutcome]
    verdict: str
    verdict_reason: str
    comparable: bool
    baselines: list[bl.Baseline] = field(default_factory=list)

    @property
    def home(self) -> Optional[VendorOutcome]:
        return self.outcomes.get(HOME_VENDOR)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario.to_dict(),
            "verdict": self.verdict,
            "verdict_reason": self.verdict_reason,
            "comparable": self.comparable,
            "baselines": [b.to_dict() for b in self.baselines],
            "outcomes": {k: v.to_dict() for k, v in self.outcomes.items()},
        }


@dataclass
class ComparisonReportData:
    """The whole run: every scenario, the rollup, and the disclosures."""

    run_id: str
    vendors: list[str]
    scenarios: list[ScenarioComparison]
    preflights: dict[str, dict[str, Any]]
    is_comparison: bool
    not_a_comparison_reason: Optional[str]
    baselines: list[bl.Baseline] = field(default_factory=list)

    def rollup(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for s in self.scenarios:
            counts[s.verdict] = counts.get(s.verdict, 0) + 1
        per_vendor: dict[str, dict[str, int]] = {}
        for vendor in self.vendors:
            tally = {"pass": 0, "fail": 0, "cannot_tell": 0, "not_runnable": 0}
            for s in self.scenarios:
                outcome = s.outcomes.get(vendor)
                if outcome is None or not outcome.run.runnable:
                    tally["not_runnable"] += 1
                elif outcome.passed is None:
                    tally["cannot_tell"] += 1
                elif outcome.passed:
                    tally["pass"] += 1
                else:
                    tally["fail"] += 1
            per_vendor[vendor] = tally
        mech = {"found": 0, "absent": 0, "cannot_tell": 0}
        for s in self.scenarios:
            home = s.home
            if home and home.evidence:
                mech[home.evidence.status] = mech.get(home.evidence.status, 0) + 1
            else:
                mech["cannot_tell"] += 1
        return {
            "verdicts": counts,
            "per_vendor": per_vendor,
            "whissle_mechanism_evidence": mech,
            "n_scenarios": len(self.scenarios),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "tau2.compare.report/v1",
            "run_id": self.run_id,
            "vendors": self.vendors,
            # Machine-readable twin of the banner — required by every consumer of
            # this file, so an automated reader cannot miss the disclosure a human
            # would read at the top of the Markdown.
            **honesty.banner_block(),
            "is_comparison": self.is_comparison,
            "not_a_comparison_reason": self.not_a_comparison_reason,
            "preflights": self.preflights,
            "rollup": self.rollup(),
            "baselines": [b.to_dict() for b in self.baselines],
            "baseline_mixing_warning": bl.mixing_warning(self.baselines),
            "scenarios": [s.to_dict() for s in self.scenarios],
        }


# ── per-scenario ────────────────────────────────────────────────────────────────


def _utterances_matched(run: ScenarioRun) -> bool:
    """True unless the adapter explicitly recorded a parity failure.

    Whissle's text channel sends the script literally, so it has no parity block
    and defaults to True; an adapter that drives a simulated user records the
    check and can veto the pair."""
    prov = ((run.diagnostics or {}).get("provenance") or {})
    value = prov.get("utterances_matched")
    return True if value is None else bool(value)


def compare_scenario(
    scenario: Any, runs: dict[str, ScenarioRun],
) -> ScenarioComparison:
    """Evaluate every vendor's run on one scenario and draw (or refuse) a verdict."""
    outcomes: dict[str, VendorOutcome] = {}
    for vendor, run in runs.items():
        checks = crit.evaluate(scenario, run)
        passed, reason = crit.verdict(checks)
        outcome = VendorOutcome(vendor=vendor, run=run, checks=checks,
                                passed=passed, reason=reason)
        if vendor == HOME_VENDOR:
            outcome.evidence = ev.evaluate(scenario, run)
        outcomes[vendor] = outcome

    measured = {v: o for v, o in outcomes.items() if o.run.measured}
    home = outcomes.get(HOME_VENDOR)
    others = {v: o for v, o in measured.items() if v != HOME_VENDOR}

    if home is None or not home.run.measured or not others:
        verdict, reason, comparable = CANNOT_COMPARE, bl.ONE_SIDED, False
        if home is not None and home.run.measured and not others:
            absent = [
                f"{v}: {o.run.not_runnable_reason or o.run.error or 'no turns'}"
                for v, o in outcomes.items()
                if v != HOME_VENDOR
            ]
            reason = bl.ONE_SIDED + (
                (" — " + "; ".join(absent)) if absent else ""
            )
        return ScenarioComparison(scenario, outcomes, verdict, reason, comparable)

    drifted = [v for v, o in measured.items() if not _utterances_matched(o.run)]
    if drifted:
        return ScenarioComparison(
            scenario, outcomes, CANNOT_COMPARE,
            REASON_UNMATCHED_UTTERANCES + f" (drifted: {', '.join(drifted)})",
            False,
        )

    # A matched pair exists. Now decide, with unknown never resolving homeward.
    opponent = next(iter(others.values()))
    if home.passed is None or opponent.passed is None:
        return ScenarioComparison(
            scenario, outcomes, CANNOT_TELL,
            (
                "cannot tell who won: "
                + "; ".join(
                    f"{o.vendor} — {o.reason}"
                    for o in (home, opponent)
                    if o.passed is None
                )
                + ". This is an unmeasured criterion, not a draw, and it is not "
                  "resolved in either vendor's favour."
            ),
            True,
        )
    if home.passed == opponent.passed:
        return ScenarioComparison(
            scenario, outcomes, TIE,
            (
                f"both {'passed' if home.passed else 'failed'} the scenario's "
                "criteria"
            ),
            True,
        )
    verdict = WIN if home.passed else LOSS
    winner, loser = (home, opponent) if home.passed else (opponent, home)
    reason = f"{winner.vendor} passed; {loser.vendor} did not ({loser.reason})"
    if verdict == WIN and home.evidence and home.evidence.fired is not True:
        # A win we cannot attribute to the mechanism is still a win — but it is not
        # evidence FOR the mechanism, and the report must not let it read as one.
        reason += (
            ". NOTE: the Whissle trace does not establish that the claimed "
            f"mechanism fired ({home.evidence.reason}), so this win does not "
            "support the scenario's hypothesis."
        )
    return ScenarioComparison(
        scenario, outcomes, verdict, reason, True,
        baselines=_scenario_baselines(scenario, outcomes),
    )


def _scenario_baselines(
    scenario: Any, outcomes: dict[str, VendorOutcome],
) -> list[bl.Baseline]:
    """Setup-matched baselines for a comparable scenario, one per vendor."""
    out: list[bl.Baseline] = []
    for vendor, outcome in outcomes.items():
        if not outcome.run.measured or outcome.passed is None:
            continue
        out.append(
            bl.setup_matched(
                vendor=vendor,
                metric=f"{scenario.id}:criteria_passed",
                value=1.0 if outcome.passed else 0.0,
                scenario_ids=[scenario.id],
                notes=outcome.reason,
            )
        )
    return out


# ── whole run ───────────────────────────────────────────────────────────────────


def build_report_data(
    run_id: str,
    vendors: list[str],
    results: list[ScenarioComparison],
    preflights: dict[str, dict[str, Any]],
    *,
    extra_baselines: Optional[list[bl.Baseline]] = None,
) -> ComparisonReportData:
    """Assemble the run-level object, deciding whether this was a comparison at all.

    ``is_comparison`` is False unless at least one scenario produced a matched
    pair. A run with no such scenario is a single-vendor measurement wearing a
    comparison's filename, and every renderer keys off this flag to say so."""
    matched = [r for r in results if r.comparable]
    run_baselines: list[bl.Baseline] = []
    for r in results:
        run_baselines.extend(r.baselines)
    run_baselines.extend(extra_baselines or [])

    if matched:
        not_a_comparison = None
    else:
        absent = sorted(
            {
                (o.run.not_runnable_reason or o.run.error or "produced no turns")
                for r in results
                for v, o in r.outcomes.items()
                if v != HOME_VENDOR and not o.run.measured
            }
        )
        not_a_comparison = bl.NO_SETUP_MATCHED + (
            (" Vendor status: " + " | ".join(absent)) if absent else ""
        )
    return ComparisonReportData(
        run_id=run_id,
        vendors=vendors,
        scenarios=results,
        preflights=preflights,
        is_comparison=bool(matched),
        not_a_comparison_reason=not_a_comparison,
        baselines=run_baselines,
    )
