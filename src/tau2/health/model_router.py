# Copyright Sierra
"""One seam for every SUPPORT LLM the health benchmarks need.

A healthcare benchmark is never just "the agent". Around the agent under test sit
simulators and graders that are themselves LLMs — PatientAgentBench's patient
simulator and its K=2 jury, AgentClinic's patient, measurement reader and moderator.
Upstream reaches for an OpenAI / Anthropic / Bedrock key for each of those. This repo
has none configured, which meant a full matrix run was blocked on credentials that had
nothing to do with what we were measuring.

So the support LLMs route, by default, through Whissle's OWN à-la-carte model API —
the same endpoint ``tau2.flow.usersim`` already drives the flow-sim user simulator and
its two judges with::

    POST {WHISSLE_BASE}/api/models/chat  {"messages":[{role,content}, ...]}
      -> {"text": str, "usage": {...}, "cost_usd": str, "latency_ms": int}

One ``WHISSLE_API_KEY`` and the whole health matrix runs.

Independence caveat — read before publishing a number
-----------------------------------------------------
See :data:`INDEPENDENCE_CAVEAT`. In one line: a Whissle-routed judge is a real
frontier model and is fine for internal diagnostics, regression tracking and
before/after comparisons, but it is *our* infrastructure grading *our* agent, so a
number published against the paper's leaderboard is materially stronger with an
independent judge (``--judge-provider openai|anthropic``). Every run report records
which provider produced its numbers, so the two can never be confused.

Cost — these harnesses are not cheap
------------------------------------
The judge/simulator calls dominate the call count (AgentClinic spends ~2 support calls
per doctor turn plus a moderator call per case; PatientAgentBench spends one patient
turn per exchange plus K=2 jury calls per rubric per session). On the Whissle route
those are metered against our own wallet, so every LLM built here counts its calls and
sums the ``cost_usd`` the endpoint reports, and every summary prints the total.
"""
from __future__ import annotations

import os
import re
from typing import Any, Optional, Protocol, runtime_checkable

from tau2.flow.usersim import ModelError, WhissleModel

# ── provider selection ──────────────────────────────────────────────────────────

WHISSLE = "whissle"
OPENAI = "openai"
ANTHROPIC = "anthropic"
LITELLM = "litellm"

#: Values accepted by ``--judge-provider``. ``whissle`` is the default everywhere.
JUDGE_PROVIDERS = (WHISSLE, OPENAI, ANTHROPIC)

#: Default model per external provider, used when ``--judge-model`` is not given.
DEFAULT_JUDGE_MODELS: dict[str, str] = {
    OPENAI: "gpt-4o",
    ANTHROPIC: "claude-sonnet-4-5",
}

#: Env var that must be present for an external provider to work at all. Checked up
#: front so a 40-case run fails in the first second rather than the fortieth minute.
PROVIDER_KEY_ENV: dict[str, str] = {
    WHISSLE: "WHISSLE_API_KEY",
    OPENAI: "OPENAI_API_KEY",
    ANTHROPIC: "ANTHROPIC_API_KEY",
}

INDEPENDENCE_CAVEAT = (
    "Judge independence: this run's simulators and graders were routed through "
    "Whissle's own model API (`POST /api/models/chat`). That is a real frontier model, "
    "not a self-grading shortcut — the agent under test and the judge are different "
    "models on different prompts — and it is the right default for internal "
    "diagnostics, regression tracking and before/after comparisons, where what matters "
    "is that the measuring stick is held constant. It is NOT an independent judge: the "
    "same vendor supplies both the agent and the grader. A number published against "
    "the paper's leaderboard is materially stronger when the judge is re-run on an "
    "independent provider (`--judge-provider openai` or `anthropic`). Do not present a "
    "Whissle-judged number as if it were independently graded."
)

EXTERNAL_JUDGE_NOTE = (
    "Judge independence: this run's simulators and graders were routed through an "
    "external provider, independent of the agent under test. This is the stronger "
    "footing for a published comparison against the paper's numbers."
)


def independence_note(provider: str) -> str:
    """The caveat that belongs on a report produced by ``provider``."""
    return INDEPENDENCE_CAVEAT if provider == WHISSLE else EXTERNAL_JUDGE_NOTE


def is_independent(provider: str) -> bool:
    """Was the judge independent of the vendor of the agent under test?"""
    return provider != WHISSLE


# ── the seam ────────────────────────────────────────────────────────────────────

@runtime_checkable
class JudgeLLM(Protocol):
    """Anything that can answer a (system, user) prompt with a string.

    Deliberately the narrowest possible interface: every support role in both
    benchmarks (patient, measurement reader, moderator, decline classifier) is a
    single-shot system+user prompt, so nothing more is needed and nothing more can
    silently diverge between backends.
    """

    name: str
    provider: str
    model: str
    calls: int
    cost_usd: float

    def complete(self, system: str, user: str) -> str: ...


class WhissleJudgeLLM:
    """Whissle's own chat model API — the self-contained default (one ``wsk_`` key).

    Retry/error handling is NOT reimplemented here. :class:`tau2.flow.usersim.
    WhissleModel` already owns the one correct policy for this endpoint (retry a 5xx
    and an EMPTY completion with a long backoff, never retry a 4xx), learned the hard
    way when transient empty completions were silently killing ~40% of flow-sim
    sessions. Both benchmarks hammer the same endpoint under the same conditions, so
    they get the same policy by construction rather than by a third copy of it.
    """

    provider = WHISSLE

    #: The endpoint's default output cap is ~512 tokens and it truncates SILENTLY, so
    #: an unlucky verbose grader reply arrives as unparseable JSON rather than as an
    #: error. Every support role here is short; 1024 is headroom, not appetite.
    DEFAULT_MAX_TOKENS = 1024

    def __init__(self, model: Optional[WhissleModel] = None,
                 attempts: int = 6, max_tokens: Optional[int] = None) -> None:
        self._m = model or WhissleModel(max_tokens=max_tokens or self.DEFAULT_MAX_TOKENS)
        self._attempts = attempts
        self.model = os.getenv("WHISSLE_JUDGE_MODEL", "") or "default"
        self.name = "whissle:/api/models/chat"

    def complete(self, system: str, user: str) -> str:
        return self._m.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            attempts=self._attempts,
        )

    @property
    def cost_usd(self) -> float:
        return float(getattr(self._m, "total_cost_usd", 0.0))

    @property
    def calls(self) -> int:
        return int(getattr(self._m, "calls", 0))


class LiteLLMJudgeLLM:
    """An INDEPENDENT judge — OpenAI, Anthropic, or any LiteLLM-routable model.

    Kept fully working (never deleted) because it is the configuration a published
    comparison should use, and because it is how a specific paper configuration is
    reproduced. Needs that provider's key in the environment.
    """

    def __init__(self, model: str, provider: str = LITELLM,
                 temperature: float = 0.05, max_tokens: int = 256) -> None:
        self.model = model
        self.provider = provider
        self.name = f"{provider}:{model}"
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.calls = 0
        self.cost_usd = 0.0

    def complete(self, system: str, user: str) -> str:
        import litellm

        r = litellm.completion(
            model=self.model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        self.calls += 1
        try:
            self.cost_usd += float(litellm.completion_cost(r) or 0.0)
        except Exception:  # noqa: BLE001 — cost is telemetry, never fatal
            pass
        return (r.choices[0].message.content or "").strip()


def require_provider_key(provider: str) -> None:
    """Fail fast, with the exact env var, before a long run burns time on nothing."""
    env = PROVIDER_KEY_ENV.get(provider)
    if env and not os.getenv(env):
        raise RuntimeError(
            f"--judge-provider {provider} needs {env} in the environment. "
            f"The default (--judge-provider whissle) needs only WHISSLE_API_KEY."
        )


def make_judge_llm(provider: str = WHISSLE, model: Optional[str] = None,
                   **kwargs: Any) -> JudgeLLM:
    """Build the support LLM for ``provider``.

    ``provider`` is one of :data:`JUDGE_PROVIDERS`. For back-compatibility with the
    AgentClinic flag that shipped first, a raw spec string is also accepted:
    ``"whissle"`` or ``"litellm:<model>"``.
    """
    spec = (provider or WHISSLE).strip()
    if spec.startswith("litellm:"):
        return LiteLLMJudgeLLM(spec.split(":", 1)[1], provider=LITELLM, **kwargs)
    if spec == WHISSLE:
        return WhissleJudgeLLM(**kwargs)
    if spec in (OPENAI, ANTHROPIC):
        require_provider_key(spec)
        return LiteLLMJudgeLLM(model or DEFAULT_JUDGE_MODELS[spec], provider=spec,
                               **kwargs)
    raise ValueError(
        f"unknown judge provider {provider!r} — expected one of "
        f"{', '.join(JUDGE_PROVIDERS)} or litellm:<model>"
    )


def judge_provenance(provider: str, model: Optional[str] = None,
                     llm: Optional[JudgeLLM] = None) -> dict[str, Any]:
    """The block every run report must carry, so a number can never be mistaken for
    something it isn't."""
    resolved = model or (getattr(llm, "model", None) if llm else None) \
        or DEFAULT_JUDGE_MODELS.get(provider, "default")
    return {
        "judge_provider": provider,
        "judge_model": resolved,
        "judge_endpoint": getattr(llm, "name", None) or (
            "whissle:/api/models/chat" if provider == WHISSLE else f"{provider}:{resolved}"),
        "judge_independent": is_independent(provider),
        "judge_independence_note": independence_note(provider),
    }


# ── cost ────────────────────────────────────────────────────────────────────────

class CostLedger:
    """Run-level roll-up of what the support LLMs cost.

    ``/api/models/chat`` is metered against our own wallet and these harnesses make a
    LOT of judge calls, so the total is printed in every summary rather than buried in
    per-case JSON. External providers report cost through LiteLLM; when a model is
    unpriced the cost reads 0.0 and ``priced`` says so.
    """

    def __init__(self, provider: str = WHISSLE, model: str = "") -> None:
        self.provider = provider
        self.model = model
        self.calls = 0
        self.cost_usd = 0.0

    def add(self, llm: Any) -> "CostLedger":
        self.calls += int(getattr(llm, "calls", 0) or 0)
        self.cost_usd += float(getattr(llm, "cost_usd", 0.0) or 0.0)
        return self

    def add_raw(self, calls: int, cost_usd: float) -> "CostLedger":
        self.calls += int(calls or 0)
        self.cost_usd += float(cost_usd or 0.0)
        return self

    def as_dict(self, n_cases: Optional[int] = None) -> dict[str, Any]:
        d: dict[str, Any] = {
            "judge_calls": self.calls,
            "judge_cost_usd": round(self.cost_usd, 5),
            "judge_cost_priced": self.cost_usd > 0.0,
        }
        if n_cases:
            d["judge_calls_per_case"] = round(self.calls / n_cases, 1)
            d["judge_cost_usd_per_case"] = round(self.cost_usd / n_cases, 5)
        return d


# ── constrained decoding for exact-token graders ────────────────────────────────

_DECORATION = re.compile(r"^[\s*_`\"'.,:;!\-]+|[\s*_`\"'.,:;!\-]+$")


class ConstrainedChoice:
    """The outcome of forcing a grader reply into an exact allowed token."""

    __slots__ = ("value", "raw", "attempts", "normalized", "resolved")

    def __init__(self, value: str, raw: str, attempts: int, normalized: bool,
                 resolved: bool) -> None:
        self.value = value            #: what the caller should grade on
        self.raw = raw                #: the last raw completion, verbatim
        self.attempts = attempts      #: how many model calls it took
        self.normalized = normalized  #: raw differed from value only by decoration
        self.resolved = resolved      #: the reply landed on an allowed token at all

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "raw": self.raw, "attempts": self.attempts,
                "normalized": self.normalized, "resolved": self.resolved}


def canonicalize_choice(raw: str, allowed: tuple[str, ...]) -> Optional[str]:
    """Strip surrounding decoration and return the allowed token, or None.

    Models routed through a chat endpoint answer ``"Yes."``, ``"**Yes**"`` or
    ``"yes\\n"`` where the grader demands the bare token. That is FORMATTING, not a
    different verdict, and letting it deflate a score would be measuring the decoder
    rather than the agent. Anything with actual extra words (``"yes, they are the
    same"``) is NOT canonicalized — that is a genuinely non-conforming grader reply and
    the caller must see it.
    """
    s = _DECORATION.sub("", (raw or "")).strip().lower()
    return s if s in allowed else None


def constrained_choice(
    llm: JudgeLLM,
    system: str,
    user: str,
    allowed: tuple[str, ...],
    *,
    attempts: int = 3,
    nudge: Optional[str] = None,
) -> ConstrainedChoice:
    """Call ``llm`` until its reply is EXACTLY one of ``allowed`` (lowercased).

    Why this exists: AgentClinic's moderator is graded by a substring/equality test
    against the literal string ``yes``. Upstream could rely on that because it pinned a
    specific model to a specific prompt; the moment the moderator is routed through a
    different backend, a reply of ``"Yes."`` scores the case WRONG for reasons that
    have nothing to do with the doctor. So the backend gets constrained instead.

    The constraint is applied on the DECODE side, never by editing the benchmark's
    prompt. Upstream's system prompt is byte-for-byte the benchmark and must not drift;
    the retry nudge is appended to the *user* message of a follow-up attempt only, and
    only after a non-conforming first reply. The returned record says how many attempts
    it took and whether the raw reply had to be normalized, so grader formatting can
    never quietly move a number without being visible in the artifacts.
    """
    nudge = nudge or ("\n\nAnswer with exactly one word, lowercase, no punctuation: "
                      + " or ".join(allowed) + ".")
    raw = ""
    for i in range(max(1, attempts)):
        prompt = user if i == 0 else user + nudge
        raw = llm.complete(system, prompt)
        value = canonicalize_choice(raw, allowed)
        if value is not None:
            # ``normalized`` flags only decoration the caller's own grading rule would
            # NOT have absorbed. AgentClinic lowercases before comparing, so a reply of
            # "Yes" was always fine; "Yes." was not. Flagging case would make the
            # counter meaningless by firing on nearly every case.
            return ConstrainedChoice(
                value=value, raw=raw, attempts=i + 1,
                normalized=(raw or "").strip().lower() != value, resolved=True)
    # Unresolvable: hand back the raw reply so the STRICT upstream rule applies and
    # the deviation is visible, rather than guessing a verdict on the model's behalf.
    return ConstrainedChoice(value=(raw or "").strip().lower(), raw=raw,
                             attempts=max(1, attempts), normalized=False,
                             resolved=False)


__all__ = [
    "ANTHROPIC",
    "CostLedger",
    "ConstrainedChoice",
    "DEFAULT_JUDGE_MODELS",
    "EXTERNAL_JUDGE_NOTE",
    "INDEPENDENCE_CAVEAT",
    "JUDGE_PROVIDERS",
    "JudgeLLM",
    "LiteLLMJudgeLLM",
    "ModelError",
    "OPENAI",
    "PROVIDER_KEY_ENV",
    "WHISSLE",
    "WhissleJudgeLLM",
    "WhissleModel",
    "canonicalize_choice",
    "constrained_choice",
    "independence_note",
    "is_independent",
    "judge_provenance",
    "make_judge_llm",
    "require_provider_key",
]
