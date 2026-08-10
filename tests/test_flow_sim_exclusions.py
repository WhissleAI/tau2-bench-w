"""A session that never ran must never be scored as an agent failure.

WHY THIS FILE EXISTS
--------------------
It was, on the published page, for two whole domains.

The flow-sim report adapter decided whether a session was measurable by reading
``metadata.infra_fail``. That flag was added partway through the suite's life,
and every sidecar written before it carries ``infra_fail: null`` — falsey. So a
session that never exchanged a single word, because the simulated caller's own
model calls were refused for insufficient credit, was counted in the denominator
as a task the agent had failed.

In the 2026-08-06/07 sweep that put debt collection on the public benchmark page
at 9.1% when 7 of its 11 sessions were a payment error against our own workspace
and only 4 were conversations, and car rental at 45.5% when 6 of 11 never
started. Two other domains in the same sweep were unaffected only because their
sessions happened to be written after the flag existed — which is the tell that
this was never about the agent.

Corrected, the suite reads 61.2% rather than 46.2%. That is a large move in our
own favour, which is exactly why the numbers below are pinned to the artifacts
on disk rather than left to a reviewer's eye.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tau2.reporting.adapters.flow_sim import _infra_reason, _latest_per_task

RESULTS = Path(__file__).resolve().parents[1] / "results" / "whissle" / "flow_sim"

# attempted, excluded, passed — recomputed from the session sidecars and
# published on whissle.ai/benchmark. If a re-run changes these, the page changes
# with them; it must not change silently.
PUBLISHED = {
    "appointment_scheduling": (11, 0, 5),
    "car_rental": (11, 6, 5),
    "customer_support": (11, 0, 7),
    "debt_collection": (11, 7, 1),
    "dental_receptionist": (11, 2, 5),
    "headache_enrollment": (10, 1, 7),
}


def _sessions(domain: str) -> list[dict]:
    d = RESULTS / domain
    return [json.loads(p.read_text()) for p in d.glob("*.session.json")]


class TestInfraReason:
    """The classifier, on the shapes that actually appear on disk."""

    def test_zero_turn_session_is_excluded_even_without_the_flag(self):
        # The exact shape of a pre-flag sidecar: no infra_fail key at all.
        s = {
            "metadata": {
                "num_turns": 0,
                "setup_error": 'ModelError: models/chat -> HTTP 402: {"detail":"Insufficient credit."}',
            },
            "outcome": {"task_success": None},
        }
        assert _infra_reason(s) == "credit_exhausted"

    def test_a_session_that_talked_is_the_agents_to_own(self):
        s = {"metadata": {"num_turns": 9, "infra_fail": False}, "outcome": {"task_success": False}}
        assert _infra_reason(s) == ""

    def test_a_failed_task_is_not_an_exclusion(self):
        """The failure mode in the other direction, which would be worse.

        If this ever starts returning a reason, we would be excluding genuine
        agent failures from our own denominator — which is the accusation this
        whole page exists to be immune to.
        """
        s = {"metadata": {"num_turns": 14}, "outcome": {"task_success": False}}
        assert _infra_reason(s) == ""

    @pytest.mark.parametrize(
        "err,expected",
        [
            ('HTTP 402: {"detail":"Insufficient credit."}', "credit_exhausted"),
            ("VoiceInfraError: bot audio is flowing but no transcript events arrived", "voice_transport"),
            ("TimeoutError: ", "timeout"),
            ('HTTP 502: {"detail":"LLM call failed"}', "provider_failure"),
            ("something nobody has seen before", "infra_fail"),
        ],
    )
    def test_buckets_by_cause_because_they_have_different_owners(self, err, expected):
        s = {"metadata": {"num_turns": 0, "setup_error": err}}
        assert _infra_reason(s) == expected

    def test_survives_junk(self):
        for junk in (None, [], "", 0):
            assert _infra_reason(junk) == ""

    def test_a_malformed_sidecar_gets_its_own_bucket(self):
        """Dropped, but never wearing the costume of a diagnosed outage.

        A session with no turns and no recorded error is not a measurement, so
        it leaves the denominator. But it is also not a known outage, and
        labelling it `infra_fail` would let an unreadable file exit the sample
        looking like something we had diagnosed. It gets its own name so the
        report can say a case was dropped for a reason nobody can state.
        """
        assert _infra_reason({"metadata": None}) == "unmeasurable"
        assert _infra_reason({"metadata": {"num_turns": 0}}) == "unmeasurable"


@pytest.mark.skipif(not RESULTS.is_dir(), reason="published artifacts not checked out")
class TestPublishedNumbers:
    """The artifacts on disk still produce the numbers on the page."""

    @pytest.mark.parametrize("domain", sorted(PUBLISHED))
    def test_domain_matches_what_was_published(self, domain):
        attempted, excluded, passed = PUBLISHED[domain]
        latest = _latest_per_task(_sessions(domain), None)
        infra = [x for x in latest if _infra_reason(x)]
        ok = sum(1 for x in latest if (x.get("outcome") or {}).get("task_success"))
        assert len(latest) == attempted
        assert len(infra) == excluded
        assert ok == passed

    def test_the_suite_headline_and_its_floor(self):
        att = exc = ok = 0
        for domain in PUBLISHED:
            latest = _latest_per_task(_sessions(domain), None)
            att += len(latest)
            exc += sum(1 for x in latest if _infra_reason(x))
            ok += sum(1 for x in latest if (x.get("outcome") or {}).get("task_success"))
        assert (att, exc, ok) == (65, 16, 30)
        assert round(100 * ok / (att - exc), 1) == 61.2
        # The floor is published beside the headline precisely because the
        # exclusions move the number our way.
        assert round(100 * ok / att, 1) == 46.2

    def test_the_exclusions_are_ours_not_the_benchmarks(self):
        """13 billing, 3 dead data channel — every one of them our own."""
        causes: dict[str, int] = {}
        for domain in PUBLISHED:
            for x in _latest_per_task(_sessions(domain), None):
                r = _infra_reason(x)
                if r:
                    causes[r] = causes.get(r, 0) + 1
        assert causes == {"credit_exhausted": 13, "voice_transport": 3}
