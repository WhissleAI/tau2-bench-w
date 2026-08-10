"""Barge-in outcome classification.

The scorer exists before its producer does, so these tests are the whole of the
specification right now. The one they exist for is `test_stopping_without_hearing_is_not_success`:
a naive implementation counts that as a win, and a metric that rewards yielding
without listening would push the product in exactly the wrong direction.
"""

from __future__ import annotations

import pytest

from tau2.voice.bargein import BargeEvent, BargeReport, score_events


def ev(intent="interrupt", stopped=True, heard=True, cut_in=200, **kw):
    return BargeEvent(
        id=kw.pop("id", "e"),
        intent=intent,
        agent_stopped=stopped,
        caller_heard=heard,
        cut_in_ms=cut_in,
        **kw,
    )


class TestOutcomes:
    def test_stopped_and_heard_is_the_only_success(self):
        assert ev(stopped=True, heard=True).outcome == "respected"

    def test_talking_over_the_caller_is_ignored(self):
        assert ev(stopped=False).outcome == "ignored"

    def test_stopping_without_hearing_is_not_success(self):
        """THE ONE THAT MATTERS.

        The agent yielded, which looks like correct behaviour from its own side
        and is what a naive scorer would credit. But the caller's words never
        arrived, so the interruption achieved nothing — and a metric that scored
        this as `respected` would reward an agent for going quiet and ignoring
        whatever was said.
        """
        e = ev(stopped=True, heard=False)
        assert e.outcome == "lost"
        assert e.outcome != "respected"

    def test_continuing_through_a_backchannel_is_correct(self):
        """'mhm' is not a request to stop talking."""
        assert ev(intent="backchannel", stopped=False).outcome == "respected"

    def test_stopping_for_a_backchannel_is_the_failure(self):
        assert ev(intent="backchannel", stopped=True).outcome == "over_eager"

    def test_stopping_for_noise_is_the_failure(self):
        assert ev(intent="noise", stopped=True).outcome == "over_eager"

    def test_an_unmeasurable_event_has_no_outcome(self):
        e = ev(infra_error="VoiceInfraError: dead channel")
        assert e.scorable is False
        assert e.outcome is None


class TestRates:
    def test_respected_rate_counts_only_genuine_interruptions(self):
        """Otherwise an agent improves this number by ignoring more coughs."""
        r = score_events([
            ev(id="a", intent="interrupt", stopped=True, heard=True),
            ev(id="b", intent="interrupt", stopped=False),
            ev(id="c", intent="backchannel", stopped=False),
            ev(id="d", intent="backchannel", stopped=False),
        ])
        assert r.real_interruptions == 2
        assert r.respected_rate == 50.0

    def test_false_barge_rate_counts_only_non_interruptions(self):
        r = score_events([
            ev(id="a", intent="interrupt", stopped=True, heard=True),
            ev(id="b", intent="backchannel", stopped=True),
            ev(id="c", intent="backchannel", stopped=False),
            ev(id="d", intent="noise", stopped=True),
        ])
        assert r.non_interruptions == 3
        assert r.false_barge_rate == pytest.approx(66.7, abs=0.1)

    def test_lost_is_reported_separately_from_ignored(self):
        r = score_events([
            ev(id="a", intent="interrupt", stopped=True, heard=False),
            ev(id="b", intent="interrupt", stopped=False),
            ev(id="c", intent="interrupt", stopped=True, heard=True),
            ev(id="d", intent="interrupt", stopped=True, heard=True),
        ])
        assert r.lost_rate == 25.0
        assert r.respected_rate == 50.0
        assert r.outcomes["ignored"] == 1

    def test_the_two_headline_rates_move_against_each_other(self):
        """The property that makes publishing both mandatory.

        A trigger-happy agent stops for everything: it respects every real
        interruption AND stops for every backchannel. A stubborn one does
        neither. One threshold, both numbers.
        """
        trigger_happy = score_events([
            ev(id="a", intent="interrupt", stopped=True, heard=True),
            ev(id="b", intent="backchannel", stopped=True),
        ])
        stubborn = score_events([
            ev(id="c", intent="interrupt", stopped=False),
            ev(id="d", intent="backchannel", stopped=False),
        ])
        assert trigger_happy.respected_rate == 100.0
        assert trigger_happy.false_barge_rate == 100.0
        assert stubborn.respected_rate == 0.0
        assert stubborn.false_barge_rate == 0.0

    def test_cut_in_is_measured_only_where_the_agent_got_it_right(self):
        """Timing how fast it stopped for a cough is not a useful average."""
        r = score_events([
            ev(id="a", intent="interrupt", stopped=True, heard=True, cut_in=100),
            ev(id="b", intent="interrupt", stopped=True, heard=True, cut_in=300),
            ev(id="c", intent="backchannel", stopped=True, cut_in=50),
            ev(id="d", intent="interrupt", stopped=True, heard=False, cut_in=20),
        ])
        assert sorted(r.cut_in_ms) == [100, 300]
        assert r.median_cut_in_ms == 300

    def test_report_always_carries_the_reading(self):
        d = score_events([ev()]).as_dict()
        assert "trade off against one another" in d["reading"]


class TestDegenerate:
    def test_empty_is_unknown_not_zero(self):
        r = score_events([])
        assert r.respected_rate is None
        assert r.false_barge_rate is None
        assert r.median_cut_in_ms is None

    def test_unmeasurable_events_are_excluded_not_scored(self):
        r = score_events([ev(id="a"), ev(id="b", infra_error="x")])
        assert r.scored == 1
        assert r.excluded == 1

    def test_junk_is_skipped(self):
        r = score_events([None, "x", 3, ev()])
        assert r.scored == 1

    def test_no_producer_exists_yet(self):
        """A guard against the scorer being wired up half-finished.

        If a producer lands, this test should be deleted in the same change —
        deliberately, and with the reviewer noticing.
        """
        import tau2.voice.bargein as m

        assert "STATUS: SCORER ONLY" in (m.__doc__ or "")
        assert isinstance(score_events([]), BargeReport)
