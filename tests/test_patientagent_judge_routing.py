"""PatientAgentBench judge routing — offline, against a MOCKED model endpoint.

Two layers are covered and they need different dependencies, so each skips
independently rather than taking the whole file down:

  * ``langchain_core`` — the LangChain ``BaseChatModel`` over ``/api/models/chat``
    (message translation, retry on an empty completion, spend accounting).
  * ``patient_agent_bench`` — the no-fork integration: registry keys added, the model
    factory wrapped so our provider is intercepted and every other provider falls
    through untouched, including in modules that already imported the factory by name.

Both are present in the PatientAgentBench venv, which is where the adapter runs (see
PATIENTAGENTBENCH.md).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import (  # noqa: E402
    AIMessage,
    HumanMessage,
    SystemMessage,
)

from tau2.health import model_router as mr  # noqa: E402
from tau2.health.patientagent import judge_model as jm  # noqa: E402

# ── mocked endpoint ─────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status: int, payload: Any) -> None:
        self.status_code = status
        self._payload = payload
        self.text = payload if isinstance(payload, str) else json.dumps(payload)

    def json(self, strict: bool = True) -> Any:  # noqa: ARG002
        return self._payload


class FakeSession:
    def __init__(self, script: list[tuple[int, Any]]) -> None:
        self.script = script
        self.headers: dict[str, str] = {}
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, json: Any = None, timeout: float = 0.0, **kw: Any):  # noqa: A002
        self.calls.append({"url": url, "json": json})
        status, payload = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        return FakeResponse(status, payload)


def ok(text: str, cost: float = 0.001) -> tuple[int, Any]:
    return (200, {"text": text, "cost_usd": str(cost), "usage": {}})


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHISSLE_API_KEY", "wsk_test")
    monkeypatch.setenv("WHISSLE_BASE", "https://example.invalid/bot")
    monkeypatch.setattr("tau2.flow.usersim.time.sleep", lambda *_: None)
    jm.reset_spend()


def model_with(script: list[tuple[int, Any]]) -> tuple[jm.WhissleJudgeChatModel, FakeSession]:
    m = jm.WhissleJudgeChatModel()
    session = FakeSession(script)
    m.client._s = session  # type: ignore[attr-defined]
    return m, session


# ── the LangChain model ─────────────────────────────────────────────────────────

def test_invoke_returns_the_completion_and_hits_the_model_api() -> None:
    m, session = model_with([ok('{"score": 4, "explanation": "solid"}')])
    reply = m.invoke([SystemMessage(content="grade this"),
                      HumanMessage(content="transcript")])
    assert isinstance(reply, AIMessage)
    assert json.loads(reply.content)["score"] == 4
    assert session.calls[0]["url"].endswith("/api/models/chat")
    assert session.calls[0]["json"]["messages"] == [
        {"role": "system", "content": "grade this"},
        {"role": "user", "content": "transcript"},
    ]


def test_output_cap_is_raised_for_the_sandbox_document() -> None:
    """Their sandbox generator returns a multi-kB JSON document. At the endpoint's
    ~512-token default it arrived UNTERMINATED and every run failed to parse — with no
    error from the endpoint, because a truncated 200 looks like a success."""
    m, session = model_with([ok("{}")])
    m.invoke([HumanMessage(content="generate the EHR")])
    assert session.calls[0]["json"]["max_tokens"] == jm.DEFAULT_MAX_TOKENS >= 4096


def test_message_roles_translate() -> None:
    assert jm.to_chat_messages([
        SystemMessage(content="s"), HumanMessage(content="h"), AIMessage(content="a"),
    ]) == [{"role": "system", "content": "s"}, {"role": "user", "content": "h"},
           {"role": "assistant", "content": "a"}]


def test_content_blocks_are_flattened_to_text() -> None:
    msgs = jm.to_chat_messages([HumanMessage(content=[{"type": "text", "text": "one"},
                                                      {"type": "text", "text": "two"}])])
    assert msgs == [{"role": "user", "content": "one\ntwo"}]


def test_empty_completion_is_retried_not_returned() -> None:
    """The jury hammers this endpoint; a transient empty completion must not become an
    unparseable evaluation and a lost session."""
    m, session = model_with([ok(""), ok(""), ok('{"score": 3}')])
    assert m.invoke([HumanMessage(content="x")]).content == '{"score": 3}'
    assert len(session.calls) == 3


def test_4xx_is_not_retried() -> None:
    m, session = model_with([(403, "forbidden"), ok("never reached")])
    with pytest.raises(mr.ModelError, match="403"):
        m.invoke([HumanMessage(content="x")])
    assert len(session.calls) == 1


def test_spend_is_accumulated_process_wide() -> None:
    """PatientAgentBench builds a fresh model per rubric per evaluator, in parallel, so
    only a process-wide ledger can answer 'what did this run cost'."""
    a, _ = model_with([ok("1", cost=0.01)])
    b, _ = model_with([ok("2", cost=0.02)])
    a.invoke([HumanMessage(content="x")])
    b.invoke([HumanMessage(content="x")])
    assert jm.spend() == {"judge_calls": 2, "judge_cost_usd": 0.03}


def test_async_path_works_for_the_sandbox_generator() -> None:
    import asyncio

    m, _ = model_with([ok("RESULT")])
    assert asyncio.run(m.ainvoke([HumanMessage(content="x")])).content == "RESULT"


# ── role -> registry key selection ──────────────────────────────────────────────

def test_whissle_provider_selects_the_whissle_keys() -> None:
    assert jm.jury_for(mr.WHISSLE) == [jm.WHISSLE_JUDGE_KEY]
    assert jm.patient_for(mr.WHISSLE) == jm.WHISSLE_PATIENT_KEY
    assert jm.sandbox_for(mr.WHISSLE) == jm.WHISSLE_SANDBOX_KEY


def test_whissle_jury_is_k1_and_says_so() -> None:
    """Two calls to the SAME endpoint is one grader sampled twice, not a K=2 jury.
    Claiming K=2 would fake evaluator agreement."""
    assert len(jm.jury_for(mr.WHISSLE)) == 1


def test_external_providers_select_independent_registry_keys() -> None:
    assert jm.jury_for(mr.ANTHROPIC) == ["claude-opus-4.8-bedrock"]
    assert jm.jury_for(mr.OPENAI) == ["gpt-5.5-api"]
    assert jm.patient_for(mr.ANTHROPIC) == "claude-sonnet-5-bedrock"
    assert jm.jury_for(mr.ANTHROPIC, "claude-sonnet-5-bedrock") == ["claude-sonnet-5-bedrock"]


# ── the no-fork integration ─────────────────────────────────────────────────────

pab = pytest.importorskip("patient_agent_bench",
                          reason="run inside the PatientAgentBench venv")


def test_install_adds_registry_keys_without_touching_theirs() -> None:
    from patient_agent_bench.model_registry import MODEL_STORE, get_model_spec

    before = {k: v for k, v in MODEL_STORE.items() if not k.startswith("whissle")}
    keys = jm.install()
    assert set(keys) <= set(MODEL_STORE)
    spec = get_model_spec(jm.WHISSLE_JUDGE_KEY)
    assert spec is not None and spec.provider == jm.WHISSLE_PROVIDER
    # Not one upstream entry changed.
    assert {k: v for k, v in MODEL_STORE.items() if not k.startswith("whissle")} == before


def test_install_is_idempotent() -> None:
    from patient_agent_bench import config as pab_config

    jm.install()
    first = pab_config.create_chat_model
    jm.install()
    assert pab_config.create_chat_model is first


def test_factory_serves_our_provider_and_passes_everything_else_through() -> None:
    from patient_agent_bench.config import ModelConfig, create_chat_model

    jm.install()
    ours = create_chat_model(ModelConfig(model=jm.WHISSLE_JUDGE_KEY))
    assert isinstance(ours, jm.WhissleJudgeChatModel)

    # A bedrock config must still take their path — proven by it demanding the bedrock
    # client we deliberately do not pass, rather than quietly becoming ours.
    theirs = ModelConfig(model="claude-sonnet-5-bedrock")
    assert theirs.provider == "bedrock" and theirs.requires_bedrock
    with pytest.raises(Exception):  # noqa: B017 — their error, not ours; shape is theirs
        create_chat_model(theirs, None)


def test_factory_is_rebound_in_modules_that_imported_it_by_name() -> None:
    """Their consumers do ``from ...config import create_chat_model`` at import time,
    so patching only the config module would leave an already-imported evaluator on the
    original factory — and the run would still demand AWS credentials."""
    import patient_agent_bench.eval.base_rubric as base_rubric  # noqa: PLC0415
    import patient_agent_bench.user_agent.default_agent as user_agent  # noqa: PLC0415
    from patient_agent_bench import config as pab_config

    jm.install()
    assert base_rubric.create_chat_model is pab_config.create_chat_model
    assert user_agent.create_chat_model is pab_config.create_chat_model
    assert hasattr(pab_config.create_chat_model, "__wrapped__")


def test_a_whissle_config_needs_no_bedrock_client() -> None:
    from patient_agent_bench.config import ModelConfig

    jm.install()
    cfg = ModelConfig(model=jm.WHISSLE_PATIENT_KEY)
    assert cfg.requires_bedrock is False
    assert cfg.provider == jm.WHISSLE_PROVIDER


def test_a_rubric_evaluator_builds_on_the_whissle_route() -> None:
    """The end of the chain: their evaluator base class, constructed with our registry
    key, ends up holding our chat model — no AWS, no ANTHROPIC_API_KEY."""
    from patient_agent_bench.config import ModelConfig
    from patient_agent_bench.eval.base_rubric import BaseRubric

    jm.install()
    rubric = BaseRubric(ModelConfig(model=jm.WHISSLE_JUDGE_KEY))
    assert isinstance(rubric.llm, jm.WhissleJudgeChatModel)
    assert rubric._bedrock_client is None
