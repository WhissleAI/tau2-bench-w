"""Route PatientAgentBench's OWN LLMs — patient simulator, K=2 jury, sandbox — through
Whissle's model API, so the whole benchmark runs on one ``WHISSLE_API_KEY``.

The problem this solves
-----------------------
PatientAgentBench needs four LLMs, and only one of them is the thing being measured:

  * the **assistant** — the agent under test (ours, over ``/api/bench/agent-turn``);
  * the **patient simulator** — plays the patient across the conversation;
  * the **jury** — the paper's K=2 LLM evaluators, one call per rubric per evaluator;
  * the **sandbox** — generates the simulated EHR responses.

Their model registry resolves the last three to Bedrock inference profiles or direct
OpenAI/Anthropic endpoints, so a run demanded AWS credentials or an ``ANTHROPIC_API_KEY``
we do not have configured. That blocked the entire matrix on credentials for the parts
of the harness we are not even measuring.

How it plugs in — no fork
-------------------------
PatientAgentBench is CC-BY-NC-4.0 and explicitly does not accept pull requests, so the
adapter must not patch their tree. It does not need to:

  1. ``model_registry.MODEL_STORE`` is a plain dict → we ADD keys (``whissle``,
     ``whissle-judge``, ``whissle-patient``) rather than change any.
  2. ``config.create_chat_model`` is documented as "the single factory for all LLM
     creation in the project" → we wrap it. Our keys carry
     ``provider="whissle-model-api"``, which the wrapper intercepts; every other
     provider falls straight through to their untouched implementation.

Their consumer modules do ``from patient_agent_bench.config import create_chat_model``
at import time, so rebinding only ``config.create_chat_model`` would miss any module
already imported. :func:`install` therefore rebinds the name in every loaded module
that holds a reference to the original — and is idempotent, so calling it twice is
safe.

What the model does
-------------------
:class:`WhissleJudgeChatModel` is a minimal LangChain ``BaseChatModel`` over ``POST
/api/models/chat``. Minimal is sufficient and verified: nothing on the
patient/jury/sandbox path calls ``bind_tools`` or ``with_structured_output`` — they all
do ``llm.invoke(messages)`` and parse text (the jury regex-extracts a JSON object).
Retries are NOT reimplemented here; the model delegates to
:class:`tau2.flow.usersim.WhissleModel`, the single owner of the retry policy for this
endpoint (5xx and empty completions retried with a long backoff, 4xx never).

Independence caveat
-------------------
Routing the jury through Whissle means our infrastructure grades our agent. That is
fine — better than fine — for internal diagnostics and regression tracking, and the
judge is a real frontier model on a different prompt from the agent. It is NOT an
independent evaluation, and a number published against the paper's leaderboard should
be re-run with ``--judge-provider anthropic`` (or ``openai``). Every report records
which provider produced it. See
:data:`tau2.health.model_router.INDEPENDENCE_CAVEAT`.
"""

from __future__ import annotations

import sys
from typing import Any, Iterator, Optional, Sequence

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr

from tau2.flow.usersim import WhissleModel
from tau2.health.model_router import ANTHROPIC, OPENAI, WHISSLE

# PatientAgentBench builds a fresh model object per rubric, per evaluator, per session
# (and runs them in parallel), so per-instance counters cannot answer "what did this
# run cost". This PROCESS-WIDE ledger can: every completion adds to it, and cmd_run
# prints/records the total. /api/models/chat bills our own wallet and a K=1 jury still
# spends 6 rubric calls plus one patient turn per exchange per session.
_LEDGER_LOCK = __import__("threading").Lock()
_LEDGER = {"calls": 0, "cost_usd": 0.0}


def spend() -> dict[str, Any]:
    """What the Whissle-routed support LLMs have cost this process, so far."""
    with _LEDGER_LOCK:
        return {"judge_calls": _LEDGER["calls"],
                "judge_cost_usd": round(_LEDGER["cost_usd"], 5)}


def reset_spend() -> None:
    with _LEDGER_LOCK:
        _LEDGER["calls"] = 0
        _LEDGER["cost_usd"] = 0.0

#: Access channel our registry entries declare. Deliberately not one of theirs, so the
#: wrapper's interception is unambiguous and their three channels are untouched.
WHISSLE_PROVIDER = "whissle-model-api"

#: Registry keys we add. Three names for the three roles, all one endpoint — separate
#: keys so a run's config (and therefore its artifacts) says which role ran where.
WHISSLE_JUDGE_KEY = "whissle-judge"
WHISSLE_PATIENT_KEY = "whissle-patient"
WHISSLE_SANDBOX_KEY = "whissle-sandbox"

WHISSLE_KEYS = (WHISSLE_JUDGE_KEY, WHISSLE_PATIENT_KEY, WHISSLE_SANDBOX_KEY)

#: The endpoint truncates at ~512 output tokens by DEFAULT and says nothing about it,
#: which shows up downstream as unparseable JSON rather than as an error. Every role
#: here needs more: a rubric returns a scored JSON object, and the sandbox generator
#: returns a multi-kB simulated-EHR document.
DEFAULT_MAX_TOKENS = 4096

#: Registry keys for the external (independent) judges, straight from their registry.
#: These are what ``--judge-provider openai|anthropic`` selects; both already exist
#: upstream, so choosing them adds nothing and needs only that provider's key.
EXTERNAL_JUDGE_KEYS = {
    ANTHROPIC: {"jury": ["claude-opus-4.8-bedrock"], "patient": "claude-sonnet-5-bedrock"},
    OPENAI: {"jury": ["gpt-5.5-api"], "patient": "gpt-5.5-api"},
}

#: The paper's own configuration (Opus 4.8 + GPT-5.5 jury, Sonnet 5 patient). Kept as
#: the literal reproduction target; needs Bedrock + OpenAI credentials.
PAPER_JURY = ["claude-opus-4.8-bedrock", "gpt-5.5-api"]
PAPER_PATIENT = "claude-sonnet-5-bedrock"


# ── the LangChain model ─────────────────────────────────────────────────────────

class WhissleJudgeChatModel(BaseChatModel):
    """``POST {WHISSLE_BASE}/api/models/chat`` as a LangChain ``BaseChatModel``.

    Only ``_generate`` is implemented. The async path LangChain synthesizes from it
    (a thread-pool ``ainvoke``) is what the sandbox generator uses, and the streaming
    path is never exercised — this endpoint is request/response.
    """

    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    base: Optional[str] = None
    api_key: Optional[str] = None
    role: str = "judge"
    attempts: int = 6
    timeout_s: float = 120.0

    #: Running spend, so a run can report what its jury cost. ``/api/models/chat`` is
    #: metered against our own wallet and a K=2 jury spends 12 calls per session.
    total_cost_usd: float = Field(default=0.0)
    call_count: int = Field(default=0)

    _model: Optional[WhissleModel] = PrivateAttr(default=None)

    @property
    def _llm_type(self) -> str:
        return "whissle-model-api"

    @property
    def client(self) -> WhissleModel:
        if self._model is None:
            # ``max_tokens`` matters here. The endpoint's default output cap is ~512
            # tokens and it truncates SILENTLY: their sandbox generator emits a
            # multi-kB JSON document and was failing with "Unterminated string" on
            # every attempt until the cap was raised. The registry spec sets 4096.
            self._model = WhissleModel(base=self.base, api_key=self.api_key,
                                       timeout=self.timeout_s,
                                       max_tokens=self.max_tokens or DEFAULT_MAX_TOKENS)
        return self._model

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        before_calls, before_cost = self.client.calls, self.client.total_cost_usd
        text = self.client.chat(to_chat_messages(messages), attempts=self.attempts)
        self.call_count = self.client.calls
        self.total_cost_usd = self.client.total_cost_usd
        with _LEDGER_LOCK:
            _LEDGER["calls"] += self.client.calls - before_calls
            _LEDGER["cost_usd"] += self.client.total_cost_usd - before_cost
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        raise NotImplementedError(
            "/api/models/chat is request/response; PatientAgentBench never streams "
            "its patient/jury/sandbox turns."
        )

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"endpoint": "/api/models/chat", "role": self.role}


_ROLE_MAP = {"human": "user", "ai": "assistant", "system": "system",
             "tool": "user", "function": "user", "chat": "user"}


def to_chat_messages(messages: Sequence[BaseMessage]) -> list[dict[str, str]]:
    """LangChain messages -> the ``{"role","content"}`` list the endpoint takes.

    ``/api/models/chat`` accepts standard system/user/assistant roles (verified live
    with a ``wsk_`` key). Tool/function messages cannot occur on the patient, jury or
    sandbox paths — none of them binds tools — but are mapped to ``user`` rather than
    dropped, so an upstream change surfaces as a strange prompt rather than as silently
    missing context. List-shaped content (a content-block list) is flattened to its
    text parts, which is all these prompts ever carry.
    """
    out: list[dict[str, str]] = []
    for m in messages:
        role = _ROLE_MAP.get(getattr(m, "type", "human"), "user")
        content = m.content
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
            content = "\n".join(p for p in parts if p)
        out.append({"role": role, "content": str(content or "")})
    return out


# ── registry + factory installation (no fork) ───────────────────────────────────

_INSTALLED = False


def install() -> tuple[str, ...]:
    """Add the Whissle model keys to PatientAgentBench and wrap its model factory.

    Idempotent. Returns the registry keys that are now selectable. Raises
    ``ImportError`` if PatientAgentBench is not installed — the caller is expected to
    be running inside its venv (see PATIENTAGENTBENCH.md).
    """
    global _INSTALLED

    from patient_agent_bench import config as pab_config
    from patient_agent_bench.model_registry import (
        MODEL_STORE,
        ModelCapability,
        ModelSpec,
    )

    for key, role in ((WHISSLE_JUDGE_KEY, "judge"),
                      (WHISSLE_PATIENT_KEY, "patient"),
                      (WHISSLE_SANDBOX_KEY, "sandbox")):
        MODEL_STORE.setdefault(key, ModelSpec(
            model_id=f"whissle-model-api:{role}",
            display_name=f"Whissle model API ({role})",
            provider=WHISSLE_PROVIDER,
            developer="whissle",
            auth="api_key",
            # None → the factory omits `temperature`; our endpoint takes no such knob.
            default_temperature=None,
            default_max_tokens=4096,
            capabilities=(ModelCapability.TEXT,),
            description=("Whissle's own a-la-carte chat model API. Self-contained "
                         "(one WHISSLE_API_KEY) but NOT an independent judge — see "
                         "tau2.health.model_router.INDEPENDENCE_CAVEAT."),
            notes="No tool-use, no image input; text prompts only.",
        ))

    if _INSTALLED:
        return WHISSLE_KEYS

    original = pab_config.create_chat_model

    def create_chat_model(model_config: Any, bedrock_client: Any = None,
                          role_arn: Optional[str] = None) -> Any:
        if getattr(model_config, "provider", None) == WHISSLE_PROVIDER:
            return WhissleJudgeChatModel(
                role=str(getattr(model_config, "model_id", "") or "judge"),
                max_tokens=getattr(model_config, "max_tokens", None),
            )
        return original(model_config, bedrock_client, role_arn)

    create_chat_model.__wrapped__ = original  # type: ignore[attr-defined]
    create_chat_model.__doc__ = (original.__doc__ or "") + (
        "\n\nWrapped by tau2.health.patientagent.judge_model: model configs whose "
        f"provider is {WHISSLE_PROVIDER!r} are served by WhissleJudgeChatModel; "
        "everything else falls through unchanged."
    )

    # Rebind in the config module AND in every module that already imported the name
    # by value (`from ... import create_chat_model`), which is how all six of their
    # consumers do it.
    pab_config.create_chat_model = create_chat_model
    for module in list(sys.modules.values()):
        if module is None or module is pab_config:
            continue
        if getattr(module, "create_chat_model", None) is original:
            module.create_chat_model = create_chat_model

    _INSTALLED = True
    return WHISSLE_KEYS


def jury_for(provider: str, model: Optional[str] = None) -> list[str]:
    """Registry keys for the K=2 jury under ``provider``.

    The paper uses two evaluators and averages them. On the Whissle route both would
    be the same endpoint, which is not a jury — it is one grader sampled twice — so
    the default is a SINGLE Whissle evaluator, and the report says K=1. Pretending
    otherwise would inflate the apparent evaluator agreement.
    """
    if provider == WHISSLE:
        return [WHISSLE_JUDGE_KEY]
    if model:
        return [model]
    return list(EXTERNAL_JUDGE_KEYS[provider]["jury"])


def patient_for(provider: str, model: Optional[str] = None) -> str:
    """Registry key for the patient simulator under ``provider``."""
    if provider == WHISSLE:
        return WHISSLE_PATIENT_KEY
    return model or str(EXTERNAL_JUDGE_KEYS[provider]["patient"])


def sandbox_for(provider: str, model: Optional[str] = None) -> str:
    """Registry key for the sandbox (simulated EHR) model under ``provider``."""
    if provider == WHISSLE:
        return WHISSLE_SANDBOX_KEY
    return model or str(EXTERNAL_JUDGE_KEYS[provider]["patient"])


__all__ = [
    "EXTERNAL_JUDGE_KEYS",
    "PAPER_JURY",
    "PAPER_PATIENT",
    "WHISSLE_JUDGE_KEY",
    "WHISSLE_KEYS",
    "WHISSLE_PATIENT_KEY",
    "WHISSLE_PROVIDER",
    "WHISSLE_SANDBOX_KEY",
    "WhissleJudgeChatModel",
    "install",
    "jury_for",
    "patient_for",
    "reset_spend",
    "sandbox_for",
    "spend",
    "to_chat_messages",
]
