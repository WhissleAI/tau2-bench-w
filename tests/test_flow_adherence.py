"""Flow Adherence Score — the scorer on top of the analyzer.

The analyzer's own correctness is covered by `test_flow_analyze.py`. This covers
the layer that turns its findings into a number somebody will put on a page:
what goes in the denominator, what counts as a violation, and — the part with
the most ways to go quietly wrong — when two arms may be compared at all.
"""

from __future__ import annotations

import glob
import json
from pathlib import Path

import pytest

from tau2.flow.adherence import (
    ADHERENCE_TYPES,
    adherence_findings,
    exclusion_bucket,
    score_parity,
    score_sessions,
)
from tau2.reporting.adapters.flow_sim import _infra_reason

RESULTS = Path(__file__).resolve().parents[1] / "results" / "whissle" / "flow_sim"


def session(task_id="t", turns=5, findings=(), *, infra=False, err=None, mode="text"):
    return {
        "task_id": task_id,
        "mode": mode,
        "metadata": {"num_turns": turns, "infra_fail": infra, "setup_error": err},
        "outcome": {"task_success": True},
        "analyzer_findings": [dict(f) for f in findings],
    }


def finding(ftype, severity="high"):
    return {"type": ftype, "severity": severity, "detail": "", "evidence": {}}


class TestDenominator:
    def test_a_session_that_never_ran_leaves_the_denominator(self):
        r = score_sessions(
            [
                session("a", turns=6),
                session("b", turns=0, err="ModelError: HTTP 402: Insufficient credit"),
            ]
        )
        assert r.scored_sessions == 1
        assert r.excluded_sessions == 1
        assert r.exclusion_breakdown == {"credit_exhausted": 1}

    def test_an_excluded_session_is_not_averaged_in_as_a_zero(self):
        """The failure mode that put a billing outage on the page as an agent failure."""
        clean = [session(f"s{i}", turns=5) for i in range(4)]
        dead = [session("dead", turns=0, err="HTTP 402: Insufficient credit")]
        assert score_sessions(clean).clean_session_rate == 100.0
        assert score_sessions(clean + dead).clean_session_rate == 100.0

    def test_density_is_per_turn_so_hanging_up_early_does_not_help(self):
        """A short call has fewer chances to violate the spec; per-session counts
        would reward an agent for ending the conversation, which this suite
        treats as a failure elsewhere."""
        long_call = score_sessions([session("a", turns=20, findings=[finding("say_fidelity")])])
        short_call = score_sessions([session("b", turns=4, findings=[finding("say_fidelity")])])
        assert long_call.findings_per_100_turns < short_call.findings_per_100_turns


class TestWhatCounts:
    def test_coverage_findings_are_not_violations(self):
        r = score_sessions([session("a", findings=[finding("coverage", "info")])])
        assert r.findings_by_type == {}
        assert r.spotless_sessions == 1

    def test_infra_findings_are_not_violations(self):
        assert "infra_fail" not in ADHERENCE_TYPES

    def test_high_severity_sinks_a_session_but_medium_does_not(self):
        high = score_sessions([session("a", findings=[finding("tool_leakage", "high")])])
        med = score_sessions([session("b", findings=[finding("say_fidelity", "medium")])])
        assert high.clean_sessions == 0
        assert med.clean_sessions == 1
        # ...but medium still costs the strict bar.
        assert med.spotless_sessions == 0

    def test_headline_has_no_tunable_weight(self):
        """A rate, not a weighted composite — see the module docstring.

        Ten medium findings must not be able to add up to the same verdict as
        one high one, because that arithmetic is exactly the editorial knob the
        design refuses to have.
        """
        many_medium = score_sessions(
            [session("a", findings=[finding("say_fidelity", "medium")] * 10)]
        )
        one_high = score_sessions([session("b", findings=[finding("dead_end", "high")])])
        assert many_medium.clean_session_rate == 100.0
        assert one_high.clean_session_rate == 0.0

    def test_severity_defaults_when_a_finding_omits_it(self):
        r = score_sessions([session("a", findings=[{"type": "tool_leakage", "detail": ""}])])
        assert r.findings_by_severity == {"high": 1}
        assert r.clean_sessions == 0

    def test_adherence_findings_ignores_junk(self):
        s = session("a")
        s["analyzer_findings"] = [None, "nope", 3, finding("dead_end")]
        assert len(adherence_findings(s)) == 1


class TestParity:
    def test_refuses_to_compare_arms_that_did_not_cover_the_same_set(self):
        p = score_parity(
            [session("a"), session("b"), session("c")],
            [session("a", mode="voice"), session("b", mode="voice")],
        )
        assert p.comparable is False
        assert p.clean_rate_gap is None
        assert "NOT a parity measurement" in p.note
        assert p.text_only == ["c"]

    def test_a_voice_session_lost_to_transport_does_not_count_as_shared(self):
        """The confound this type exists to prevent.

        Voice is the arm that loses sessions to transport failures, so the
        survivors are systematically the ones that connected. If an excluded
        voice session counted as coverage, the comparison would silently be
        text-on-everything against voice-on-the-easy-ones.
        """
        p = score_parity(
            [session("a"), session("b")],
            [
                session("a", mode="voice"),
                session("b", turns=0, err="VoiceInfraError: data channel dead", mode="voice"),
            ],
        )
        assert p.comparable is False
        assert p.text_only == ["b"]

    def test_reports_a_gap_when_the_arms_really_do_match(self):
        p = score_parity(
            [session("a"), session("b")],
            [
                session("a", mode="voice", findings=[finding("dead_end")]),
                session("b", mode="voice"),
            ],
        )
        assert p.comparable is True
        assert p.text.clean_session_rate == 100.0
        assert p.voice.clean_session_rate == 50.0
        assert p.clean_rate_gap == 50.0
        assert "the difference is the modality" in p.note

    def test_no_shared_scenarios_states_that_rather_than_dividing_by_zero(self):
        p = score_parity([session("a")], [session("z", mode="voice")])
        assert p.comparable is False
        assert p.clean_rate_gap is None
        assert "share no scenarios" in p.note


class TestEmptyAndDegenerate:
    def test_no_sessions_yields_none_not_zero(self):
        """A rate over nothing is unknown, not 0% — and 0% would render as a
        catastrophic result for a suite that simply has not run."""
        r = score_sessions([])
        assert r.clean_session_rate is None
        assert r.findings_per_100_turns is None
        assert r.exclusion_rate is None

    def test_junk_sessions_are_skipped_not_scored(self):
        r = score_sessions([None, "x", 7, session("a")])
        assert r.scored_sessions == 1


@pytest.mark.skipif(not RESULTS.is_dir(), reason="published artifacts not checked out")
class TestAgainstTheRealArtifacts:
    def test_agrees_with_the_reporting_adapter_on_what_is_excluded(self):
        """Two implementations of one rule, kept honest against each other.

        `adherence.py` is pure and must not import the reporting layer, so the
        exclusion rule exists twice. Duplication is fine; divergence is not, and
        divergence is what this catches.
        """
        checked = 0
        for p in glob.glob(str(RESULTS / "*" / "*.session.json")):
            s = json.loads(Path(p).read_text())
            assert bool(exclusion_bucket(s)) == bool(_infra_reason(s)), p
            assert exclusion_bucket(s) == _infra_reason(s), p
            checked += 1
        assert checked > 50, "expected the published sessions to be present"

    def test_scores_the_published_suite_without_crashing(self):
        sessions = [
            json.loads(Path(p).read_text())
            for p in glob.glob(str(RESULTS / "*" / "*.session.json"))
        ]
        r = score_sessions(sessions)
        assert r.scored_sessions > 0
        assert r.turns > 0
        assert 0.0 <= (r.clean_session_rate or 0.0) <= 100.0
        # Every excluded session lands in a named bucket.
        assert sum(r.exclusion_breakdown.values()) == r.excluded_sessions
