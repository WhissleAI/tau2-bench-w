# Copyright Sierra
"""Offline tests for health-benchmark judge routing.

Everything here runs against a MOCKED ``/api/models/chat`` — no network, no key, no
spend. What is covered:

  * provider selection (``whissle`` default, external providers, bad names, the
    fail-fast missing-key check),
  * the retry policy the benchmarks inherit from ``usersim.WhissleModel``: an EMPTY
    completion and a 5xx are retried, a 4xx is not,
  * AgentClinic's moderator constrained to EXACTLY ``yes`` — decorated replies
    canonicalize, a verbose reply is retried, an unconstrainable reply falls back to
    upstream's strict rule and is FLAGGED rather than guessed,
  * the judge provider (and the independence caveat) landing in the run reports of
    both adapters,
  * per-run judge cost being visible.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import pytest
import requests

from tau2.health import model_router as mr
from tau2.health.agentclinic.agents import (
    MODERATOR_ALLOWED,
    MODERATOR_SYSTEM,
    compare_results,
    moderate,
    moderator_says_yes,
)
from tau2.health.agentclinic.scoring import CaseScore, aggregate, summary_markdown

# ── mocked model endpoint ───────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status: int, payload: Any) -> None:
        self.status_code = status
        self._payload = payload
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self, strict: bool = True) -> Any:  # noqa: ARG002
        return self._payload


class FakeSession:
    """Stands in for ``requests.Session`` inside ``WhissleModel``.

    ``script`` is a list of (status, payload) tuples served in order; the last entry
    repeats once exhausted, so a test only has to script the interesting prefix.
    """

    def __init__(self, script: list[tuple[int, Any]]) -> None:
        self.script = script
        self.headers: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, json: Any = None, timeout: float = 0.0, **kw: Any):  # noqa: A002
        self.calls.append({"url": url, "json": json})
        i = min(len(self.calls) - 1, len(self.script) - 1)
        status, payload = self.script[i]
        if isinstance(payload, Exception):
            raise payload
        return FakeResponse(status, payload)


def ok(text: str, cost: float = 0.002) -> tuple[int, Any]:
    return (200, {"text": text, "cost_usd": str(cost), "usage": {}, "latency_ms": 10})


@pytest.fixture()
def whissle_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHISSLE_API_KEY", "wsk_test")
    monkeypatch.setenv("WHISSLE_BASE", "https://example.invalid/bot")


def judge_with(script: list[tuple[int, Any]]) -> tuple[mr.WhissleJudgeLLM, FakeSession]:
    llm = mr.make_judge_llm(mr.WHISSLE)
    session = FakeSession(script)
    llm._m._s = session  # type: ignore[attr-defined]
    # Keep the retry backoff out of the test's wall clock; the POLICY is what matters.
    llm._m.__dict__.setdefault("_no_sleep", True)
    return llm, session


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """usersim's backoff is 3-12s per attempt; tests assert the policy, not the wait."""
    monkeypatch.setattr("tau2.flow.usersim.time.sleep", lambda *_: None)


# ── provider selection ──────────────────────────────────────────────────────────

def test_default_provider_is_whissle(whissle_key: None) -> None:
    llm = mr.make_judge_llm()
    assert isinstance(llm, mr.WhissleJudgeLLM)
    assert llm.provider == mr.WHISSLE
    assert llm.name == "whissle:/api/models/chat"
    assert mr.is_independent(mr.WHISSLE) is False


@pytest.mark.parametrize("provider,model", [("openai", "gpt-4o"),
                                            ("anthropic", "claude-sonnet-4-5")])
def test_external_providers_still_work(provider: str, model: str,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """The external path must never be deleted — it is what a published number uses."""
    monkeypatch.setenv(mr.PROVIDER_KEY_ENV[provider], "sk-test")
    llm = mr.make_judge_llm(provider)
    assert isinstance(llm, mr.LiteLLMJudgeLLM)
    assert llm.provider == provider
    assert llm.model == model
    assert mr.is_independent(provider) is True


def test_external_provider_without_key_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        mr.make_judge_llm("openai")


def test_litellm_spec_passthrough_keeps_the_old_flag_working() -> None:
    llm = mr.make_judge_llm("litellm:gpt-4o-mini")
    assert isinstance(llm, mr.LiteLLMJudgeLLM)
    assert llm.model == "gpt-4o-mini"


def test_unknown_provider_rejected() -> None:
    with pytest.raises(ValueError, match="unknown judge provider"):
        mr.make_judge_llm("gemini")


def test_whissle_without_key_is_a_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WHISSLE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="WHISSLE_API_KEY"):
        mr.make_judge_llm(mr.WHISSLE)


# ── the inherited retry policy ──────────────────────────────────────────────────

def test_retries_empty_completion(whissle_key: None) -> None:
    """The endpoint intermittently returns 200 with an empty ``text``. A single one of
    those used to kill a whole session; both benchmarks now inherit the retry."""
    llm, session = judge_with([ok(""), ok(""), ok("yes")])
    assert llm.complete("sys", "user") == "yes"
    assert len(session.calls) == 3


def test_retries_5xx(whissle_key: None) -> None:
    llm, session = judge_with([(502, "bad gateway"), (500, "boom"), ok("no")])
    assert llm.complete("sys", "user") == "no"
    assert len(session.calls) == 3


def test_does_not_retry_4xx(whissle_key: None) -> None:
    """A 4xx is our bug, not load. Retrying it would hide a broken adapter."""
    llm, session = judge_with([(401, "unauthorized"), ok("yes")])
    with pytest.raises(mr.ModelError, match="401"):
        llm.complete("sys", "user")
    assert len(session.calls) == 1


def test_retries_connection_errors(whissle_key: None) -> None:
    llm, session = judge_with([(0, requests.ConnectionError("reset")), ok("yes")])
    assert llm.complete("sys", "user") == "yes"
    assert len(session.calls) == 2


def test_gives_up_and_raises_after_all_attempts(whissle_key: None) -> None:
    llm, _ = judge_with([ok("")])
    with pytest.raises(mr.ModelError, match="empty completion"):
        llm.complete("sys", "user")


def test_output_cap_is_sent_so_long_replies_are_not_truncated(whissle_key: None) -> None:
    """``/api/models/chat`` caps output at ~512 tokens by DEFAULT and truncates
    SILENTLY — the caller sees a 200 with a cut-off body, which downstream reads as
    unparseable JSON rather than as an error. It cost a full PatientAgentBench run
    (their sandbox generator emits a multi-kB JSON document) before it was found."""
    llm, session = judge_with([ok("fine")])
    llm.complete("sys", "user")
    assert session.calls[0]["json"]["max_tokens"] == mr.WhissleJudgeLLM.DEFAULT_MAX_TOKENS


def test_flow_sim_callers_are_unchanged_when_no_cap_is_asked_for(whissle_key: None) -> None:
    """``max_tokens`` is opt-in: an existing WhissleModel caller sends the same body it
    always did, so flow-sim's numbers cannot shift under this change."""
    from tau2.flow.usersim import WhissleModel

    m = WhissleModel()
    m._s = FakeSession([ok("hi")])  # type: ignore[attr-defined]
    m.chat([{"role": "user", "content": "x"}])
    assert m._s.calls[0]["json"] == {"messages": [{"role": "user", "content": "x"}]}  # type: ignore[attr-defined]


def test_cost_and_calls_are_tracked(whissle_key: None) -> None:
    llm, _ = judge_with([ok("yes", cost=0.004)])
    llm.complete("sys", "user")
    llm.complete("sys", "user")
    assert llm.calls == 2
    assert llm.cost_usd == pytest.approx(0.008)


# ── the moderator's exact-`yes` constraint ──────────────────────────────────────

class ScriptedLLM:
    """A support LLM that replays a fixed list of replies."""

    provider = "test"
    model = "scripted"
    name = "test:scripted"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.prompts: list[tuple[str, str]] = []
        self.calls = 0
        self.cost_usd = 0.0

    def complete(self, system: str, user: str) -> str:
        self.prompts.append((system, user))
        self.calls += 1
        return self.replies[min(self.calls - 1, len(self.replies) - 1)]


def test_upstream_rule_is_exactly_yes() -> None:
    assert moderator_says_yes("yes") is True
    for bad in ("Yes.", "yes.", "yes, they are the same", "YES", ""):
        assert moderator_says_yes(bad) is False, bad


@pytest.mark.parametrize("raw", ["yes", "Yes", "Yes.", " yes\n", "**Yes**", "`yes`",
                                 "yes!", '"Yes"'])
def test_decorated_yes_canonicalizes_without_a_retry(raw: str) -> None:
    """Punctuation is formatting, not a verdict. Letting it score a correct diagnosis
    WRONG would measure the grader's decoder rather than the doctor."""
    llm = ScriptedLLM([raw])
    v = moderate("It is pneumonia.", "Pneumonia", llm)
    assert v.value == "yes"
    assert v.resolved is True
    assert v.attempts == 1
    assert moderator_says_yes(v.value) is True


def test_verbose_reply_is_retried_until_it_conforms() -> None:
    llm = ScriptedLLM(["Yes, these describe the same disease.", "yes"])
    v = moderate("It is pneumonia.", "Pneumonia", llm)
    assert v.value == "yes" and v.resolved is True and v.attempts == 2
    # The RETRY nudge goes on the user message of the follow-up only; the benchmark's
    # system prompt is byte-for-byte upstream on every attempt.
    assert all(system == MODERATOR_SYSTEM for system, _ in llm.prompts)
    assert "exactly one word" not in llm.prompts[0][1]
    assert "exactly one word" in llm.prompts[1][1]


def test_no_is_preserved_not_coerced_to_yes() -> None:
    llm = ScriptedLLM(["No."])
    v = moderate("It is asthma.", "Pneumonia", llm)
    assert v.value == "no" and moderator_says_yes(v.value) is False


def test_unconstrainable_reply_falls_back_to_the_strict_rule_and_is_flagged() -> None:
    """We never guess a verdict on the grader's behalf. The strict rule applies and
    the deviation is recorded, so it cannot move a number invisibly."""
    llm = ScriptedLLM(["I cannot determine that from the information given."])
    v = moderate("It is pneumonia.", "Pneumonia", llm, attempts=2)
    assert v.resolved is False
    assert v.attempts == 2
    assert moderator_says_yes(v.value) is False


def test_upstream_prompt_is_unchanged_by_the_constraint() -> None:
    """``compare_results`` (upstream's raw call) and ``moderate`` must send the SAME
    first prompt — the constraint is decode-side only."""
    a, b = ScriptedLLM(["yes"]), ScriptedLLM(["yes"])
    compare_results("It is pneumonia.", "Pneumonia", a)
    moderate("It is pneumonia.", "Pneumonia", b)
    assert a.prompts[0] == b.prompts[0]


def test_canonicalize_choice_rejects_extra_words() -> None:
    assert mr.canonicalize_choice("yes", MODERATOR_ALLOWED) == "yes"
    assert mr.canonicalize_choice("yes indeed", MODERATOR_ALLOWED) is None
    assert mr.canonicalize_choice("", MODERATOR_ALLOWED) is None


# ── provider + cost recorded in the reports ─────────────────────────────────────

def _case(outcome: str, *, correctness: Optional[bool], cost: float, calls: int,
          **score: Any) -> dict[str, Any]:
    s = CaseScore(outcome=outcome, correctness=correctness, doctor_diagnosis="dx",
                  doctor_final_text="DIAGNOSIS READY: dx", **score)
    return {"scenario_id": "c1", "scenario_index": 0, "score": s.as_dict(),
            "infra_fail": False, "inferences_used": 4, "tests_ordered": [],
            "support_llm_cost_usd": cost, "support_llm_calls": calls}


def test_agentclinic_summary_records_provider_caveat_and_cost() -> None:
    meta = mr.judge_provenance(mr.WHISSLE)
    cases = [
        _case("correct", correctness=True, cost=0.02, calls=9, moderator_attempts=1),
        _case("incorrect", correctness=False, cost=0.03, calls=11,
              moderator_attempts=2, moderator_normalized=True),
    ]
    s = aggregate(cases, meta={**meta, "dataset": "MedQA"})

    assert s["judge_provider"] == "whissle"
    assert s["judge_independent"] is False
    assert "NOT an independent judge" in s["judge_independence_note"]
    assert s["judge_calls"] == 20
    assert s["judge_cost_usd"] == pytest.approx(0.05)
    assert s["judge_cost_usd_per_case"] == pytest.approx(0.025)
    assert s["moderator_retried"] == 1
    assert s["moderator_normalized"] == 1
    assert s["moderator_unconstrained"] == 0

    md = summary_markdown(s)
    assert "NOT independent (same vendor as the agent under test)" in md
    assert "judge spend" in md
    assert "materially stronger" in md      # the caveat itself is on the page


def test_agentclinic_summary_says_independent_for_an_external_judge() -> None:
    meta = mr.judge_provenance(mr.OPENAI)
    s = aggregate([_case("correct", correctness=True, cost=0.0, calls=5)],
                  meta={**meta, "dataset": "MedQA"})
    assert s["judge_independent"] is True
    md = summary_markdown(s)
    assert "INDEPENDENT of the agent vendor" in md
    assert "stronger footing for a published comparison" in md


def test_patientagent_report_records_the_judge(tmp_path: Any) -> None:
    from tau2.health.patientagent.report import render_markdown, write_report

    summary = {"label": "Whissle", "mode": "harness_tools", "n_total": 3,
               "n_scored": 3, "n_excluded": 0,
               "excluded_breakdown": {"infra_fail": 0, "agent_error": 0},
               "aggregate": 3.4, "aggregate_ci": (3.1, 3.7), "dimensions": {},
               "weights": {"clinical_safety": 2.0}, "pass_threshold": 3}
    judge = {**mr.judge_provenance(mr.WHISSLE), "jury_k": 1,
             "patient_model": "whissle-patient", "sandbox_model": "whissle-sandbox",
             "judge_calls": 42, "judge_cost_usd": 0.31,
             "judge_calls_per_case": 14.0, "judge_cost_usd_per_case": 0.103}

    md = render_markdown(summary, judge=judge)
    assert "**Judge:** `whissle`" in md
    assert "NOT independent of the agent's vendor" in md
    assert "materially stronger" in md
    assert "judge spend**: 42 calls, $0.3100" in md
    assert "K = 1 (the paper uses K=2)" in md

    paths = write_report(str(tmp_path), summary, judge=judge)
    payload = json.loads(open(paths["summary"], encoding="utf-8").read())
    assert payload["judge_provider"] == "whissle"
    assert payload["judge_independent"] is False


def test_patientagent_report_refuses_to_guess_an_unrecorded_judge() -> None:
    from tau2.health.patientagent.report import render_markdown

    summary = {"label": "", "mode": "harness_tools", "n_total": 1, "n_scored": 1,
               "n_excluded": 0, "excluded_breakdown": {}, "aggregate": 3.0,
               "aggregate_ci": (3.0, 3.0), "dimensions": {}, "weights": {},
               "pass_threshold": 3}
    md = render_markdown(summary, judge=None)
    assert "Judge provider: unrecorded" in md
    assert "Do not publish these numbers" in md


def test_independence_note_differs_by_provider() -> None:
    assert "NOT an independent judge" in mr.independence_note(mr.WHISSLE)
    assert "independent of the agent under test" in mr.independence_note(mr.ANTHROPIC)


def test_cost_ledger_rolls_up_per_case() -> None:
    ledger = mr.CostLedger(provider=mr.WHISSLE)
    ledger.add_raw(10, 0.10).add_raw(6, 0.05)
    d = ledger.as_dict(n_cases=4)
    assert d == {"judge_calls": 16, "judge_cost_usd": 0.15, "judge_cost_priced": True,
                 "judge_calls_per_case": 4.0, "judge_cost_usd_per_case": 0.0375}
