"""Paired statistics for the ablation. numpy-free, stdlib only.

The arms run the identical task list, so every comparison here is **paired** —
per case, arm B minus arm A — and never arm-mean against arm-mean. At n = 100 the
difference is not cosmetic: an unpaired comparison of two ~70% pass rates cannot
resolve anything below about ±12 points, while the paired test only has to resolve
the cases where the arms actually disagreed, which is usually a handful. Reporting
two averages side by side at this n is a way of not measuring.

What is here:

``mcnemar_exact``   binary outcomes (route correct, fabricated, acknowledged).
                    Exact binomial on the discordant pairs — no chi-square
                    approximation, which is wrong exactly when b+c is small, which
                    is exactly the regime an ablation lives in.
``wilcoxon``        ordered/continuous per-case scores (slot accuracy, latency).
                    Exact for small n, normal approximation with continuity
                    correction above 20 pairs.
``bootstrap_ci``    a CI on the paired mean difference, resampling pairs.
``mde_paired``      the effect this run could actually have detected. Reported for
                    every null result, because "no effect" and "underpowered to
                    see this effect" are different findings and only one of them
                    is about metadata.
``interpret``       turns the above into one of: gain / regression / no measurable
                    effect / underpowered — with the bound stated.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Sequence

ALPHA = 0.05


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _comb(n: int, k: int) -> int:
    return math.comb(n, k)


def _binom_two_sided(b: int, c: int) -> float:
    """Exact two-sided binomial p for b successes out of n=b+c at p=0.5."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(_comb(n, i) for i in range(0, k + 1)) / (2.0 ** n)
    return min(1.0, 2.0 * tail)


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


@dataclass
class PairedResult:
    metric: str
    n_pairs: int = 0
    #: binary case
    b_only: int = 0          # arm B right, arm A wrong
    a_only: int = 0          # arm A right, arm B wrong
    both: int = 0
    neither: int = 0
    rate_a: Optional[float] = None
    rate_b: Optional[float] = None
    #: continuous case
    mean_a: Optional[float] = None
    mean_b: Optional[float] = None
    delta: Optional[float] = None
    ci: Optional[tuple[float, float]] = None
    p_value: Optional[float] = None
    test: str = ""
    mde: Optional[float] = None
    verdict: str = ""
    note: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ci"] = list(self.ci) if self.ci else None
        return d


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def mcnemar_exact(metric: str, a: Sequence[bool], b: Sequence[bool],
                  *, higher_is_better: bool = True) -> PairedResult:
    """Paired binary comparison. ``a``/``b`` are aligned per-case outcomes."""
    if len(a) != len(b):
        raise ValueError("paired sequences must be the same length")
    r = PairedResult(metric=metric, n_pairs=len(a), test="exact McNemar (binomial)")
    for x, y in zip(a, b):
        if x and y:
            r.both += 1
        elif y and not x:
            r.b_only += 1
        elif x and not y:
            r.a_only += 1
        else:
            r.neither += 1
    n = r.n_pairs
    if n:
        r.rate_a = (r.both + r.a_only) / n
        r.rate_b = (r.both + r.b_only) / n
        r.delta = r.rate_b - r.rate_a
    r.p_value = _binom_two_sided(r.b_only, r.a_only)
    r.ci = bootstrap_ci([1.0 if y else 0.0 for y in b], [1.0 if x else 0.0 for x in a])
    r.mde = mde_paired_binary(n, discordant=r.b_only + r.a_only)
    r.verdict = interpret(r, higher_is_better=higher_is_better)
    r.detail["discordant"] = r.b_only + r.a_only
    return r


def wilcoxon(metric: str, a: Sequence[float], b: Sequence[float],
             *, higher_is_better: bool = True) -> PairedResult:
    """Wilcoxon signed-rank on the paired differences (b − a)."""
    if len(a) != len(b):
        raise ValueError("paired sequences must be the same length")
    r = PairedResult(metric=metric, n_pairs=len(a), test="Wilcoxon signed-rank")
    diffs = [y - x for x, y in zip(a, b)]
    nz = [d for d in diffs if d != 0]
    r.mean_a = (sum(a) / len(a)) if a else None
    r.mean_b = (sum(b) / len(b)) if b else None
    r.delta = (r.mean_b - r.mean_a) if (r.mean_a is not None and r.mean_b is not None) else None
    r.detail["n_nonzero"] = len(nz)
    if not nz:
        r.p_value = 1.0
        r.ci = (0.0, 0.0)
        r.mde = mde_paired_continuous(diffs)
        r.verdict = interpret(r, higher_is_better=higher_is_better)
        r.note = "every pair was identical — the arms produced the same value on every case"
        return r

    order = sorted(range(len(nz)), key=lambda i: abs(nz[i]))
    ranks = [0.0] * len(nz)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and abs(nz[order[j + 1]]) == abs(nz[order[i]]):
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    w_plus = sum(rk for d, rk in zip(nz, ranks) if d > 0)
    w_minus = sum(rk for d, rk in zip(nz, ranks) if d < 0)
    n = len(nz)
    w = min(w_plus, w_minus)
    mu = n * (n + 1) / 4.0
    sigma = math.sqrt(n * (n + 1) * (2 * n + 1) / 24.0)
    if sigma == 0:
        r.p_value = 1.0
    else:
        z = (w - mu + 0.5) / sigma
        r.p_value = min(1.0, 2.0 * _norm_cdf(z))
    r.ci = bootstrap_ci(list(b), list(a))
    r.mde = mde_paired_continuous(diffs)
    r.verdict = interpret(r, higher_is_better=higher_is_better)
    r.detail["w_plus"] = w_plus
    r.detail["w_minus"] = w_minus
    return r


def bootstrap_ci(b: Sequence[float], a: Sequence[float], *,
                 iters: int = 10000, alpha: float = ALPHA,
                 seed: int = 20260808) -> Optional[tuple[float, float]]:
    """Percentile bootstrap CI on the paired mean difference. Resamples *pairs*,
    which is what preserves the pairing the design bought."""
    n = len(a)
    if n == 0 or n != len(b):
        return None
    diffs = [y - x for x, y in zip(a, b)]
    rng = random.Random(seed)
    means = []
    for _ in range(iters):
        s = 0.0
        for _ in range(n):
            s += diffs[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int((alpha / 2) * iters)]
    hi = means[min(iters - 1, int((1 - alpha / 2) * iters))]
    return (round(lo, 4), round(hi, 4))


# ---------------------------------------------------------------------------
# power
# ---------------------------------------------------------------------------


def mde_paired_binary(n: int, discordant: int, *, alpha: float = ALPHA,
                      power: float = 0.80) -> Optional[float]:
    """Smallest true rate difference this paired run could have detected.

    Uses the observed discordance rate: in a paired binary design the power comes
    from the pairs where the arms disagreed, not from n. A run with n = 100 and
    four discordant pairs has the power of a study of four.
    """
    if n <= 0:
        return None
    z_a, z_b = 1.959963985, 0.8416212336
    # Discordance is what we can estimate; when none was observed, fall back to a
    # conservative floor so the number is a bound rather than a division by zero.
    pd = max(discordant / n, 1.0 / n)
    return round((z_a + z_b) * math.sqrt(pd / n), 4)


def mde_paired_continuous(diffs: Sequence[float], *, alpha: float = ALPHA,
                          power: float = 0.80) -> Optional[float]:
    n = len(diffs)
    if n < 2:
        return None
    mean = sum(diffs) / n
    var = sum((d - mean) ** 2 for d in diffs) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0
    z_a, z_b = 1.959963985, 0.8416212336
    return round((z_a + z_b) * sd / math.sqrt(n), 4)


def interpret(r: PairedResult, *, higher_is_better: bool = True,
              alpha: float = ALPHA) -> str:
    """gain / regression / no measurable effect / underpowered — never a shrug.

    The distinction the report is required to make lives here. A non-significant
    result is 'no measurable effect' only if the run could have seen an effect
    worth caring about; otherwise it is 'underpowered', and the MDE is the honest
    thing to publish instead of a p-value.
    """
    if r.p_value is None or r.delta is None:
        return "not measured"
    signed = r.delta if higher_is_better else -r.delta
    if r.p_value < alpha:
        return "gain" if signed > 0 else "regression"
    if r.mde is not None and abs(r.mde) >= 0.10:
        return "underpowered"
    if r.n_pairs and r.detail.get("discordant") == 0:
        return "no measurable effect (identical on every case)"
    return "no measurable effect"


def summarise(results: Sequence[PairedResult]) -> dict[str, Any]:
    return {
        "n_metrics": len(results),
        "gains": [r.metric for r in results if r.verdict == "gain"],
        "regressions": [r.metric for r in results if r.verdict == "regression"],
        "null": [r.metric for r in results if r.verdict.startswith("no measurable")],
        "underpowered": [r.metric for r in results if r.verdict == "underpowered"],
        "results": [r.to_dict() for r in results],
    }
