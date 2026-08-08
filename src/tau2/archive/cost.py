# Copyright Sierra
"""What a run cost, computed from the token counts the backend actually reported.

``/api/bench/agent-turn`` returns a real ``usage`` block (backend #664), so cost is
now arithmetic rather than an estimate. Two things make it worth doing carefully:

**Input dominates output roughly 75:1 in our agentic shape.** Each turn resends the
whole transcript plus the system prompt and tool schemas, and gets back a short
action. A measured turn on the bench endpoint: 477 input tokens, 4 output. That
inverts the usual intuition that output pricing is what matters — for these
benchmarks the input column is the bill, which is also why prompt caching is the
lever that moves it and why :func:`compute` prices cached reads separately instead of
folding them into input.

**Cost is per case AND per run.** A run total answers "what did this cost"; a
per-case figure answers "which cases are expensive", which is the one that changes
what you do next — a handful of runaway cases looks identical to a uniformly
expensive suite in the total alone.

PRICES ARE DATED AND PARTIAL, AND SAY SO
----------------------------------------
:data:`PRICES` is a snapshot with a :data:`PRICES_AS_OF` date. A model absent from
the table yields ``cost_usd = None`` and an explicit ``unpriced`` entry rather than a
zero — a zero would silently understate a run's cost and then be summed with real
numbers, which is worse than admitting the gap.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from .schema import NOT_RECORDED

#: When the price table below was last checked against published rates.
PRICES_AS_OF = "2026-06-24"

PRICES_SOURCE = (
    "Anthropic first-party published API rates as of "
    f"{PRICES_AS_OF}. Third-party-served or partner-platform pricing (Bedrock, "
    "Vertex) differs and is not modelled here."
)


@dataclass(frozen=True)
class Price:
    """USD per million tokens."""

    input_per_mtok: float
    output_per_mtok: float
    #: Cached reads bill at ~0.1x input; cache writes at ~1.25x (5-minute TTL).
    cache_read_multiplier: float = 0.1
    cache_write_multiplier: float = 1.25


#: Keyed by model id PREFIX so dated snapshots (``claude-haiku-4-5-20251001``) match
#: their family without a new row per snapshot. Longest prefix wins — see
#: :func:`price_for`.
PRICES: dict[str, Price] = {
    "claude-fable-5": Price(10.0, 50.0),
    "claude-mythos-5": Price(10.0, 50.0),
    "claude-opus-5": Price(5.0, 25.0),
    "claude-opus-4-8": Price(5.0, 25.0),
    "claude-opus-4-7": Price(5.0, 25.0),
    "claude-opus-4-6": Price(5.0, 25.0),
    "claude-opus-4-5": Price(5.0, 25.0),
    "claude-sonnet-5": Price(3.0, 15.0),
    "claude-sonnet-4-6": Price(3.0, 15.0),
    "claude-sonnet-4-5": Price(3.0, 15.0),
    "claude-haiku-4-5": Price(1.0, 5.0),
}

UNPRICED_NOTE = (
    "no published price is recorded for this model in tau2.archive.cost.PRICES, so "
    "its spend is EXCLUDED from the run total rather than counted as zero. A zero "
    "would be summed with real figures and silently understate the run."
)


def price_for(model: Optional[str]) -> Optional[Price]:
    """Longest-prefix match, so ``claude-haiku-4-5-20251001`` prices as Haiku 4.5."""
    if not model:
        return None
    name = str(model).strip().lower()
    best: Optional[tuple[int, Price]] = None
    for prefix, price in PRICES.items():
        if name.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), price)
    return best[1] if best else None


@dataclass
class Usage:
    """One turn's token counts, as the backend reported them."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @classmethod
    def from_payload(cls, payload: Any) -> "Usage":
        """Read a ``usage`` block. Unknown shapes yield zeros rather than raising —
        a malformed usage block must not sink an otherwise-valid run."""
        if not isinstance(payload, dict):
            return cls()
        def num(key: str) -> int:
            value = payload.get(key)
            return int(value) if isinstance(value, (int, float)) else 0
        return cls(
            input_tokens=num("input_tokens"),
            output_tokens=num("output_tokens"),
            cache_read_input_tokens=num("cache_read_input_tokens"),
            cache_creation_input_tokens=num("cache_creation_input_tokens"),
        )

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_input_tokens + other.cache_read_input_tokens,
            self.cache_creation_input_tokens + other.cache_creation_input_tokens,
        )

    @property
    def total_input(self) -> int:
        """Every token the model read, cached or not — the honest denominator for
        "how big was the prompt", which ``input_tokens`` alone understates whenever
        caching is on."""
        return (
            self.input_tokens
            + self.cache_read_input_tokens
            + self.cache_creation_input_tokens
        )

    @property
    def io_ratio(self) -> Optional[float]:
        """Input:output. The number that explains where the money went."""
        return (self.total_input / self.output_tokens) if self.output_tokens else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "total_input_tokens": self.total_input,
            "input_output_ratio": round(self.io_ratio, 1) if self.io_ratio else None,
        }


def usd(usage: Usage, model: Optional[str]) -> Optional[float]:
    """USD for one model's usage, or ``None`` when the model has no published price."""
    price = price_for(model)
    if price is None:
        return None
    return (
        usage.input_tokens / 1e6 * price.input_per_mtok
        + usage.cache_read_input_tokens / 1e6 * price.input_per_mtok
        * price.cache_read_multiplier
        + usage.cache_creation_input_tokens / 1e6 * price.input_per_mtok
        * price.cache_write_multiplier
        + usage.output_tokens / 1e6 * price.output_per_mtok
    )


def compute(
    per_model: dict[str, Usage],
    *,
    n_cases: Optional[int] = None,
    extra_usd: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    """Roll per-model usage into the cost block a manifest carries.

    ``extra_usd`` is spend the harness already knows in dollars rather than tokens —
    judge and simulator calls billed through ``POST /api/models/chat``, which returns
    ``cost_usd`` directly. Kept as a separate line because agent spend and judge
    spend answer different questions: one is what the system under test costs to
    run, the other is what measuring it costs.
    """
    total = Usage()
    priced = 0.0
    unpriced: list[str] = []
    breakdown: list[dict[str, Any]] = []

    for model, usage in sorted(per_model.items()):
        total = total + usage
        amount = usd(usage, model)
        if amount is None:
            unpriced.append(model)
        else:
            priced += amount
        breakdown.append({
            "model": model,
            "usage": usage.to_dict(),
            "cost_usd": round(amount, 6) if amount is not None else None,
            "unpriced_reason": None if amount is not None else UNPRICED_NOTE,
        })

    judge = dict(extra_usd or {})
    judge_total = sum(judge.values()) if judge else 0.0
    agent_total = round(priced, 6) if per_model else None
    grand = None
    if agent_total is not None or judge:
        grand = round((agent_total or 0.0) + judge_total, 6)

    return {
        "available": bool(per_model or judge),
        "reason": None if (per_model or judge) else (
            "no token usage or dollar figure was recorded for this run — it predates "
            "the backend returning a `usage` block, so its cost is unrecoverable"
        ),
        "prices_as_of": PRICES_AS_OF,
        "prices_source": PRICES_SOURCE,
        "agent_usd": agent_total,
        "judge_and_simulator_usd": round(judge_total, 6) if judge else None,
        "judge_breakdown": judge or None,
        "total_usd": grand,
        "usd_per_case": (
            round(grand / n_cases, 6) if grand is not None and n_cases else None
        ),
        "n_cases": n_cases,
        "usage_total": total.to_dict() if per_model else None,
        "input_dominates_output": (
            f"input:output = {total.io_ratio:.0f}:1 for this run — the agentic shape "
            "resends the full transcript every turn and gets back one short action, "
            "so the input column is the bill"
            if total.io_ratio else None
        ),
        "by_model": breakdown or None,
        "unpriced_models": unpriced or None,
    }


def merge(usages: Iterable[tuple[str, Usage]]) -> dict[str, Usage]:
    """Group ``(model, usage)`` pairs into a per-model total."""
    out: dict[str, Usage] = {}
    for model, usage in usages:
        key = model or NOT_RECORDED
        out[key] = out.get(key, Usage()) + usage
    return out
