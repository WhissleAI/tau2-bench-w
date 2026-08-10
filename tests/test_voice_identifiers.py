"""Spelled-identifier scoring.

The suite this belongs to cannot run without a live workspace, but its scoring
rule can and must be tested without one — the rule is where the benchmark's
honesty lives. Every test below is really one question: does normalisation
absorb a formatting difference (it should) or a hearing failure (it must not)?
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tau2.voice.identifiers import (
    CATEGORIES,
    IdentifierAttempt,
    IdentifierCase,
    char_error_rate,
    normalize,
    score_cases,
)

CORPUS = Path(__file__).resolve().parents[1] / "data" / "voice" / "identifiers.json"


def case(truth, captured, category="order_id", **kw):
    attempts = [IdentifierAttempt(captured=c) for c in (captured if isinstance(captured, list) else [captured])]
    for i, a in enumerate(attempts, 1):
        a.attempt = i
    return IdentifierCase(id="t", category=category, truth=truth, attempts=attempts, **kw)


class TestNormalisationAbsorbsFormatting:
    def test_case_and_whitespace(self):
        assert normalize("  W2378-BPD9 ", "order_id") == normalize("w2378-bpd9", "order_id")

    def test_phone_separators_are_formatting(self):
        for form in ["+1 (215) 555-0147", "+1-215-555-0147", "+1 215 555 0147"]:
            assert normalize(form, "phone") == "+12155550147"

    def test_postcode_internal_space(self):
        assert normalize("SW1A 2AA", "postcode") == normalize("sw1a2aa", "postcode")

    def test_spoken_email_punctuation(self):
        assert normalize("j dot okafor at example dot com", "email") == "j.okafor@example.com"

    def test_accents_fold(self):
        assert normalize("José Álvarez", "person_name") == normalize("Jose Alvarez", "person_name")

    def test_hyphen_in_a_surname_is_formatting(self):
        assert normalize("Aisha Al-Rashid", "person_name") == normalize("Aisha Al Rashid", "person_name")


class TestNormalisationRefusesToAbsorbAMiss:
    """Every one of these is a different database key, so every one is a miss."""

    def test_the_failure_that_started_this(self):
        assert normalize("Youssef Rossi", "person_name") != normalize("Yusuf Rossi", "person_name")

    def test_homophones_stay_distinct(self):
        assert normalize("Sarah Cohen", "person_name") != normalize("Sara Kohen", "person_name")

    def test_al_rashid_is_not_al_rasheed(self):
        assert normalize("Aisha Al-Rashid", "person_name") != normalize("Aisha Al-Rasheed", "person_name")

    def test_confusable_characters_stay_distinct(self):
        assert normalize("W2378-BPD9", "order_id") != normalize("W2378-BPD5", "order_id")
        assert normalize("ORD-4M7N-05", "order_id") != normalize("ORD-4N7M-05", "order_id")

    def test_a_dropped_leading_zero_is_a_miss(self):
        assert normalize("ORD-4M7N-05", "order_id") != normalize("ORD-4M7N-5", "order_id")

    def test_email_local_part_is_not_punctuation(self):
        assert normalize("m_rivera_88@example.co.uk", "email") != normalize(
            "m-rivera-88@example.co.uk", "email"
        )

    def test_country_code_is_information(self):
        assert normalize("+12155550147", "phone") != normalize("2155550147", "phone")


class TestVerdicts:
    def test_exact_match_first_time(self):
        c = case("W2378-BPD9", "w2378-bpd9")
        assert c.first_pass is True
        assert c.final_pass is True
        assert c.recovered is None  # never needed recovering

    def test_a_near_miss_is_still_a_miss(self):
        """Half the characters right returns no customer, not a worse one."""
        c = case("W2378-BPD9", "W2378-BPD5")
        assert c.first_pass is False
        assert 0 < c.best_cer < 0.2

    def test_recovery_after_a_reask(self):
        c = case("Yusuf Rossi", ["Youssef Rossi", "Yusuf Rossi"], category="person_name")
        assert c.first_pass is False
        assert c.final_pass is True
        assert c.recovered is True

    def test_recovery_that_failed(self):
        c = case("Yusuf Rossi", ["Youssef Rossi", "Yousef Rossi"], category="person_name")
        assert c.recovered is False
        assert c.final_pass is False

    def test_nothing_captured_is_a_miss_not_a_crash(self):
        c = case("W2378-BPD9", None)
        assert c.first_pass is False
        assert c.final_pass is False

    def test_a_session_that_never_ran_is_excluded(self):
        c = IdentifierCase(id="x", category="order_id", truth="A1", infra_error="VoiceInfraError: dead channel")
        assert c.scorable is False
        assert c.first_pass is None


class TestCharErrorRate:
    def test_exact_is_zero(self):
        assert char_error_rate("abc", "abc") == 0.0

    def test_can_exceed_one_when_the_agent_captured_a_whole_phrase(self):
        """Not clipped: the worst failures must not look like moderate ones."""
        assert char_error_rate("A1", "the order number is A1 I think") > 1.0

    def test_empty_truth_is_not_a_division_by_zero(self):
        assert char_error_rate("", "") == 0.0
        assert char_error_rate("", "x") == 1.0


class TestReport:
    def test_recovery_denominator_is_the_cases_that_needed_it(self):
        """Otherwise a retry strategy looks better the more often it is
        unnecessary, which is precisely backwards."""
        r = score_cases([
            case("A1", "A1"),                 # first pass, never needed recovery
            case("A2", "A2"),
            case("A3", ["XX", "A3"]),         # needed it, got it
            case("A4", ["XX", "YY"]),         # needed it, missed it
        ])
        assert r.scored == 4
        assert r.first_pass_rate == 50.0
        assert r.needed_recovery == 2
        assert r.recovery_rate == 50.0
        assert r.final_pass_rate == 75.0

    def test_excluded_cases_never_score_as_misses(self):
        good = [case("A1", "A1")]
        dead = [IdentifierCase(id="d", category="order_id", truth="A2", infra_error="VoiceInfraError: x")]
        assert score_cases(good).first_pass_rate == 100.0
        assert score_cases(good + dead).first_pass_rate == 100.0
        assert score_cases(good + dead).excluded == 1

    def test_per_category_because_one_blended_number_hides_the_fix(self):
        r = score_cases([
            case("Yusuf Rossi", "Youssef Rossi", category="person_name"),
            case("19122", "19122", category="postcode"),
        ])
        assert r.category_rate("person_name") == 0.0
        assert r.category_rate("postcode") == 100.0
        assert r.category_rate("email") is None

    def test_empty_is_unknown_not_zero(self):
        r = score_cases([])
        assert r.first_pass_rate is None
        assert r.recovery_rate is None
        assert r.mean_cer is None


class TestCorpus:
    def test_corpus_parses_and_is_well_formed(self):
        d = json.loads(CORPUS.read_text())
        cases = d["cases"]
        assert len(cases) >= 15
        ids = [c["id"] for c in cases]
        assert len(set(ids)) == len(ids), "case ids must be unique"
        for c in cases:
            assert c["category"] in CATEGORIES, c["id"]
            assert c["truth"], c["id"]
            assert c["spoken_as"], c["id"]
            assert c["note"], f"{c['id']} has no note saying what it is for"

    def test_the_real_failure_is_case_one(self):
        """If this case is ever dropped, the suite has stopped measuring the
        thing it was built for."""
        d = json.loads(CORPUS.read_text())
        seed = next(c for c in d["cases"] if c["id"] == "name_yusuf_rossi")
        assert seed["truth"] == "Yusuf Rossi"
        assert "HD_RETAIL" in seed["provenance"]

    def test_every_category_is_exercised(self):
        d = json.loads(CORPUS.read_text())
        covered = {c["category"] for c in d["cases"]}
        assert covered == set(CATEGORIES)

    def test_homophone_pairs_are_genuinely_distinct_under_normalisation(self):
        """A pair that normalised to the same value would silently make one of
        the two cases unscoreable."""
        d = json.loads(CORPUS.read_text())
        by_id = {c["id"]: c for c in d["cases"]}
        a, b = by_id["name_homophone_sara"], by_id["name_homophone_sara_alt"]
        assert normalize(a["truth"], "person_name") != normalize(b["truth"], "person_name")

    @pytest.mark.parametrize("cid", ["postcode_us_19122", "name_yusuf_rossi"])
    def test_cases_from_the_published_run_keep_their_provenance(self, cid):
        d = json.loads(CORPUS.read_text())
        c = next(x for x in d["cases"] if x["id"] == cid)
        assert c["note"]
