"""Tests for the metadata ablation.

The tests that matter most here are not the graders — they are the **guards**.
An ablation fails silently: arms that differ in more than the variable, or a
metadata block that quietly went empty, both produce a complete run with
confident numbers about nothing. Those paths are tested first and hardest,
because in production they are the ones that will actually fire.
"""

from __future__ import annotations

import json

import pytest

from tau2.ablation import arms as A
from tau2.ablation import corpus as C
from tau2.ablation import grade as G
from tau2.ablation import stats as S
from tau2.ablation import substrate as SUB
from tau2.ablation.perception import Perception


# ---------------------------------------------------------------------------
# corpus
# ---------------------------------------------------------------------------


def test_corpus_is_deterministic():
    a = C.build_corpus(20260808)
    b = C.build_corpus(20260808)
    assert [x.to_dict() for x in a] == [x.to_dict() for x in b]
    assert C.corpus_digest(a) == C.corpus_digest(b)


def test_corpus_shape():
    cases = C.build_corpus()
    assert len(cases) == 100
    counts = {s: sum(1 for c in cases if c.slice == s) for s in
              ("entity", "intent", "emotion")}
    assert counts == {"entity": 40, "intent": 30, "emotion": 30}
    assert all(c.gold_route in C.ROUTES for c in cases)
    assert all(k in C.SLOT_KEYS for c in cases for k in c.gold_slots)
    assert len({c.case_id for c in cases}) == 100


def test_frozen_corpus_matches_generator(tmp_path):
    p = C.freeze(tmp_path / "corpus.json")
    cases, meta = C.load(p)
    assert meta["digest"] == C.corpus_digest(C.build_corpus(meta["seed"]))
    assert len(cases) == meta["n"]


def test_edited_corpus_is_rejected(tmp_path):
    """A hand-edited corpus silently invalidates every comparison made against
    it, so the loader must refuse it rather than warn."""
    p = C.freeze(tmp_path / "corpus.json")
    doc = json.loads(p.read_text())
    doc["cases"][0]["gold_route"] = "escalate_to_human"
    p.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="digest mismatch"):
        C.load(p)


# ---------------------------------------------------------------------------
# the production metadata block
# ---------------------------------------------------------------------------


def test_block_matches_production_formatter():
    """Reproduces _MetadataContextMixin._format_field: distributions render as
    Top(NN%)/Next(NN%), entries at or below 5% are dropped, and only the first
    four survive."""
    meta = {
        "emotion": "Neutral", "emotion_confidence": 0.86,
        "intent": "Inform", "intent_confidence": 0.6,
        "probs": {
            "emotion": [{"token": "EMOTION_NEUTRAL", "probability": 0.86},
                        {"token": "EMOTION_ANGRY", "probability": 0.09},
                        {"token": "EMOTION_SAD", "probability": 0.03}],
            "intent": [{"token": "INTENT_INFORM", "probability": 0.60},
                       {"token": "INTENT_REQUEST", "probability": 0.22}],
        },
    }
    block = A.speech_analysis_block(meta)
    assert block == ("[User speech analysis: emotion=Neutral(86%)/Angry(9%), "
                     "intent=Inform(60%)/Request(22%)]")
    assert "Sad" not in block          # 3% is below the 5% floor
    assert block.startswith("[User speech analysis: ")


def test_block_falls_back_to_top1_without_a_distribution():
    meta = {"emotion": "Angry", "emotion_confidence": 0.71, "probs": {}}
    assert A.speech_analysis_block(meta) == "[User speech analysis: emotion=Angry(71%)]"


def test_block_is_empty_when_the_head_said_nothing():
    assert A.speech_analysis_block(None) == ""
    assert A.speech_analysis_block({}) == ""
    assert A.speech_analysis_block({"probs": {}}) == ""


def test_only_emotion_and_intent_ever_render():
    """behavior/role/eval are in production's field list but nothing populates
    them; age/gender are populated and deliberately never injected."""
    meta = {"emotion": "Neutral", "emotion_confidence": 0.9,
            "age": "30_45", "gender": "Male", "probs": {}}
    block = A.speech_analysis_block(meta)
    assert "age" not in block and "gender" not in block
    assert "emotion" in block
    assert set(A.PRODUCED_BUT_NOT_INJECTED) >= {"age", "gender", "entity", "hesitation"}


# ---------------------------------------------------------------------------
# the guards — the reason this suite exists
# ---------------------------------------------------------------------------


def test_single_variable_check_rejects_a_second_difference():
    a = A.ArmSpec(key="A", label="a", metadata_mode="off")
    b = A.ArmSpec(key="B", label="b", metadata_mode="production")
    A.assert_single_variable([a, b])          # clean
    with pytest.raises(A.ArmMismatch):
        A.assert_single_variable([a, A.ArmSpec(key="B2", label="b", metadata_mode="off")])


def test_arms_differ_check_catches_a_collapsed_arm_b():
    """The failure this whole design exists to prevent: an empty block turns arm
    B into arm A, the run completes, and every delta is a fake zero."""
    with pytest.raises(A.ArmMismatch, match="identical to arm A"):
        A.assert_arms_differ("c1", {"A": "hello", "B": "hello"})
    A.assert_arms_differ("c1", {"A": "hello", "B": "[User speech analysis: x]\nhello"})


def test_served_model_check_catches_a_silent_failover():
    A.assert_served_model("c1", {"A": "claude-sonnet-5", "B": "claude-sonnet-5"},
                          "claude-sonnet-5")
    with pytest.raises(A.ArmMismatch, match="not matched on model"):
        A.assert_served_model("c1", {"A": "claude-sonnet-5", "B": "gemini-2.5-flash"},
                              "claude-sonnet-5")


def test_arm_a_and_b_prompts_differ_only_by_the_block():
    case = C.build_corpus()[0]
    p = Perception(case_id=case.case_id, spoken=case.spoken, asr_text="heard text",
                   metadata={"emotion": "Neutral", "emotion_confidence": 0.9,
                             "probs": {}}, metadata_available=True)
    a = A.user_content(case, p, A.ARM_A)
    b = A.user_content(case, p, A.ARM_B)
    assert a == "heard text"
    assert b.endswith("\nheard text")
    assert b[: -len("\nheard text")] == "[User speech analysis: emotion=Neutral(90%)]"


def test_messages_open_with_a_user_turn():
    """Anthropic rejects a history that opens with an assistant message, and every
    case opens with the agent's greeting."""
    case = C.build_corpus()[0]
    p = Perception(case_id=case.case_id, spoken=case.spoken, asr_text="x")
    msgs = A.build_messages(case, p, A.ARM_A)
    assert msgs[0]["role"] == "user"
    assert msgs[-1]["role"] == "user"
    assert all(m["role"] in ("user", "assistant") for m in msgs)


def test_noisy_arm_is_deterministic():
    case = C.build_corpus()[70]
    p = Perception(case_id=case.case_id, spoken=case.spoken, asr_text="x")
    first = A.user_content(case, p, A.ARM_B_NOISY)
    assert first == A.user_content(case, p, A.ARM_B_NOISY)


# ---------------------------------------------------------------------------
# grading
# ---------------------------------------------------------------------------


def test_parse_reply_survives_a_code_fence_and_stray_prose():
    p = G.parse_reply('Sure!\n```json\n{"route":"other","slots":{},"reply":"hi"}\n```')
    assert p.ok and p.route == "other"
    bad = G.parse_reply("I cannot answer that.")
    assert not bad.ok and bad.parse_error


@pytest.mark.parametrize("key,got,gold", [
    ("member_id", "48 29 17", "482917"),
    ("phone", "(415) 555-0192", "4155550192"),
    ("amount", "$142.50", "142.50"),
    ("date", "March 14th", "march 14"),
    ("date", "2026-03-14", "march 14"),
    ("caller_name", "priya raghunathan", "Priya Raghunathan"),
])
def test_slot_normalisation(key, got, gold):
    assert G.normalise(key, got) == G.normalise(key, gold)


def test_fabrication_requires_a_value_the_caller_never_said():
    case = C.Case(case_id="t", slice="entity",
                  spoken="My member ID is four eight two nine one seven.",
                  gold_route="other", gold_slots={"member_id": "482917"})
    asr = "My member ID is 482917."
    ok = G.grade_case(case, "A", json.dumps(
        {"route": "other", "slots": {"member_id": "482917"}, "reply": "ok"}), asr)
    assert ok.fabricated_slots == []
    bad = G.grade_case(case, "A", json.dumps(
        {"route": "other", "slots": {"member_id": "999999"}, "reply": "ok"}), asr)
    assert bad.fabricated is True


def test_an_asr_error_is_not_charged_to_the_model_as_fabrication():
    """A value the caller said but the ear mangled is a transcription failure, not
    the model inventing data — it is checked against the gold spoken text too."""
    case = C.Case(case_id="t", slice="entity",
                  spoken="This is Priya Raghunathan.", gold_route="other",
                  gold_slots={"caller_name": "Priya Raghunathan"})
    g = G.grade_case(case, "A", json.dumps(
        {"route": "other", "slots": {"caller_name": "Priya Raghunathan"},
         "reply": "ok"}), "This is Prea Ragunathan.")
    assert g.fabricated_slots == []


def test_digit_and_noun_slots_are_scored_separately():
    case = C.Case(case_id="t", slice="entity", spoken="x", gold_route="other",
                  gold_slots={"member_id": "482917", "caller_name": "Grace Liu"})
    g = G.grade_case(case, "A", json.dumps(
        {"route": "other", "slots": {"member_id": "482917", "caller_name": "Wrong Name"},
         "reply": ""}), "482917 Grace Liu")
    assert (g.digit_correct, g.digit_expected) == (1, 1)
    assert (g.noun_correct, g.noun_expected) == (0, 1)


def test_asr_grade_bounds_what_downstream_can_recover():
    case = C.Case(case_id="t", slice="entity", spoken="x", gold_route="other",
                  gold_slots={"member_id": "482917", "caller_name": "Grace Liu"})
    a = G.grade_asr(case, "member 482917 for Grace Liu")
    assert a.digit_recoverable == 1 and a.noun_recoverable == 1
    b = G.grade_asr(case, "member 999999 for someone")
    assert b.digit_recoverable == 0 and b.noun_recoverable == 0


# ---------------------------------------------------------------------------
# statistics
# ---------------------------------------------------------------------------


def test_mcnemar_uses_only_the_discordant_pairs():
    a = [True] * 40 + [False] * 10
    b = [True] * 40 + [False] * 10
    r = S.mcnemar_exact("m", a, b)
    assert r.b_only == 0 and r.a_only == 0
    assert r.p_value == 1.0
    assert r.delta == 0.0
    assert r.verdict.startswith("no measurable")


def test_mcnemar_detects_a_one_sided_shift():
    a = [False] * 20 + [True] * 30
    b = [True] * 20 + [True] * 30
    r = S.mcnemar_exact("m", a, b)
    assert r.b_only == 20 and r.a_only == 0
    assert r.p_value < 0.001
    assert r.verdict == "gain"


def test_a_regression_is_named_a_regression():
    a = [True] * 15 + [False] * 35
    b = [False] * 15 + [False] * 35
    r = S.mcnemar_exact("m", a, b)
    assert r.verdict == "regression"


def test_higher_is_better_false_flips_the_verdict():
    """Fabrication going UP is a regression even though the number went up."""
    a = [False] * 50
    b = [True] * 15 + [False] * 35
    r = S.mcnemar_exact("fab", a, b, higher_is_better=False)
    assert r.delta > 0
    assert r.verdict == "regression"


def test_wilcoxon_on_identical_arms_reports_identical():
    xs = [0.5, 0.7, 1.0, 0.25] * 5
    r = S.wilcoxon("m", xs, list(xs))
    assert r.p_value == 1.0
    assert "identical" in r.note


def test_bootstrap_ci_brackets_a_real_shift():
    a = [0.0] * 50
    b = [1.0] * 50
    lo, hi = S.bootstrap_ci(b, a)
    assert lo <= -1.0 <= hi or lo <= 1.0 <= hi


def test_mde_grows_when_few_pairs_disagree():
    """Power in a paired binary design comes from the discordant pairs, not from
    N — a run with n=100 and 2 disagreements has the power of a study of 2."""
    many = S.mde_paired_binary(100, discordant=50)
    few = S.mde_paired_binary(100, discordant=2)
    assert few < many          # fewer discordant pairs → smaller estimated pd
    assert S.mde_paired_binary(0, 0) is None


def test_interpret_separates_null_from_underpowered():
    r = S.PairedResult(metric="m", n_pairs=10, delta=0.02, p_value=0.6, mde=0.4)
    assert S.interpret(r) == "underpowered"
    r2 = S.PairedResult(metric="m", n_pairs=400, delta=0.001, p_value=0.9, mde=0.02)
    assert S.interpret(r2).startswith("no measurable")


# ---------------------------------------------------------------------------
# substrate
# ---------------------------------------------------------------------------


def test_mutual_information_is_zero_for_a_constant_channel():
    gold = ["angry", "happy", "sad", "neutral"] * 5
    pred = ["neutral"] * 20
    assert SUB.mutual_information(gold, pred) == 0.0


def test_mutual_information_is_maximal_for_a_perfect_channel():
    gold = ["a", "b", "c", "d"] * 5
    mi = SUB.mutual_information(gold, list(gold))
    assert mi == pytest.approx(2.0, abs=1e-9)


def test_degenerate_channel_is_called_degenerate():
    recs = [{"gold_affect": "angry" if i % 4 else "neutral", "gold_route": "other",
             "slice": "emotion",
             "perception": {"metadata": {"emotion": "Neutral", "emotion_confidence": 0.99,
                                         "intent": "Inform", "intent_confidence": 0.5,
                                         "probs": {}}}}
            for i in range(40)]
    out = SUB.analyse(recs)
    assert out["channels"]["emotion"]["verdict"].startswith("degenerate")
    assert "emotion" not in out["informative_channels"]


def test_off_majority_accuracy_is_reported_beside_the_baseline():
    recs = [{"gold_affect": "neutral", "gold_route": "other", "slice": "emotion",
             "perception": {"metadata": {"emotion": "Neutral", "emotion_confidence": 0.9,
                                         "intent": "Inform", "probs": {}}}}
            for _ in range(18)]
    recs += [{"gold_affect": "angry", "gold_route": "other", "slice": "emotion",
              "perception": {"metadata": {"emotion": "Angry", "emotion_confidence": 0.9,
                                          "intent": "Inform", "probs": {}}}}
             for _ in range(2)]
    ch = SUB.analyse(recs)["channels"]["emotion"]
    assert ch["majority_class_baseline"] == pytest.approx(0.9)
    assert ch["accuracy_off_majority"] == pytest.approx(1.0)
    assert ch["n_off_majority"] == 2
