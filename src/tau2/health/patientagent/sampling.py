"""Seeded, stratified sampling of benchmark cases.

A full PatientAgentBench run is 1,200 scenarios x a multi-turn conversation x a
K-model jury grading 102 criteria. That is expensive enough that most runs will be
samples, and a sample is only worth publishing if it is (a) reproducible and (b) not
skewed relative to the full set.

``--num-cases N`` in their CLI takes the FIRST N cases, which inherits whatever order
the generator produced — the wrong thing for a headline number. This module instead
allocates N across strata in proportion to the full set (largest-remainder, so the
counts sum exactly to N) and picks within each stratum with a seeded RNG. Same seed
plus same case file always yields the same sample, and the report carries the
achieved-vs-population distribution so skew is visible rather than assumed away.

Default strata are ``task_type`` x ``severity_level``: task type drives which tools a
scenario exercises (workflow accuracy) and severity drives the escalation decision
(triage quality, the dimension with the widest spread between models and therefore
the one a lazy sample would distort most).
"""

from __future__ import annotations

import json
import random
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

DEFAULT_STRATA_KEYS: tuple[str, ...] = ("task_type", "severity_level")


def stratum_key(entry: dict[str, Any], keys: Sequence[str]) -> tuple[str, ...]:
    """The stratum a case belongs to. Missing attributes collapse to a shared
    ``"__missing__"`` bucket rather than raising, so a case file with a partially
    populated schema still samples."""
    return tuple(str(entry.get(k, "__missing__")) for k in keys)


def _case_id(entry: dict[str, Any], index: int) -> str:
    for field_name in ("id", "scenario_id", "case_id"):
        value = entry.get(field_name)
        if value:
            return str(value)
    return f"case_{index:05d}"


def _largest_remainder(weights: dict[Any, int], total: int, n: int) -> dict[Any, int]:
    """Apportion ``n`` across strata proportionally to size, summing exactly to n.

    Plain rounding over- or under-shoots; the largest-remainder (Hamilton) method
    hands out the leftover slots to the strata with the biggest fractional parts, so
    the allocation is both exact and closest to proportional.
    """
    if total <= 0 or n <= 0:
        return {k: 0 for k in weights}
    exact = {k: (size * n) / total for k, size in weights.items()}
    floors = {k: int(v) for k, v in exact.items()}
    # Never allocate more than a stratum actually holds.
    floors = {k: min(v, weights[k]) for k, v in floors.items()}
    remaining = n - sum(floors.values())

    # Deterministic tie-break: larger remainder, then larger stratum, then key order.
    order = sorted(
        weights,
        key=lambda k: (-(exact[k] - int(exact[k])), -weights[k], str(k)),
    )
    i = 0
    while remaining > 0 and any(floors[k] < weights[k] for k in weights):
        k = order[i % len(order)]
        if floors[k] < weights[k]:
            floors[k] += 1
            remaining -= 1
        i += 1
        if i > len(order) * (n + 1):  # safety valve against a pathological loop
            break
    return floors


@dataclass
class SampleReport:
    """Everything needed to defend a sampled number: the seed, the strata, N, and
    the achieved distribution against the population."""

    n_requested: int
    n_selected: int
    n_population: int
    seed: int
    strata_keys: list[str]
    case_ids: list[str] = field(default_factory=list)
    distribution: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_requested": self.n_requested,
            "n_selected": self.n_selected,
            "n_population": self.n_population,
            "seed": self.seed,
            "strata_keys": self.strata_keys,
            "case_ids": self.case_ids,
            "distribution": self.distribution,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


def stratified_sample(
    entries: Sequence[dict[str, Any]],
    n: Optional[int],
    *,
    seed: int = 42,
    strata_keys: Sequence[str] = DEFAULT_STRATA_KEYS,
) -> tuple[list[dict[str, Any]], SampleReport]:
    """Draw a reproducible, proportionally stratified sample of ``n`` cases.

    ``n`` of None, 0, or >= len(entries) returns the full set (still reported), so
    the same code path serves smoke runs and the full 1,200.
    """
    population = list(entries)
    keys = list(strata_keys)

    if n is None or n <= 0 or n >= len(population):
        selected = population
    else:
        buckets: dict[tuple[str, ...], list[int]] = defaultdict(list)
        for index, entry in enumerate(population):
            buckets[stratum_key(entry, keys)].append(index)

        sizes = {k: len(v) for k, v in buckets.items()}
        allocation = _largest_remainder(sizes, len(population), n)

        chosen: list[int] = []
        # Sort strata so the RNG stream is stable regardless of dict ordering.
        for key in sorted(buckets):
            take = allocation.get(key, 0)
            if take <= 0:
                continue
            indices = sorted(buckets[key])
            # A per-stratum RNG keeps a stratum's draw independent of the others,
            # so adding a case to one stratum cannot reshuffle the whole sample.
            rng = random.Random(f"{seed}|{'|'.join(key)}")
            chosen.extend(rng.sample(indices, take))
        selected = [population[i] for i in sorted(chosen)]

    return selected, _build_report(population, selected, n, seed, keys)


def _build_report(
    population: Sequence[dict[str, Any]],
    selected: Sequence[dict[str, Any]],
    n_requested: Optional[int],
    seed: int,
    keys: list[str],
) -> SampleReport:
    distribution: dict[str, Any] = {}
    for key in keys:
        pop_counts = Counter(str(e.get(key, "__missing__")) for e in population)
        sel_counts = Counter(str(e.get(key, "__missing__")) for e in selected)
        n_pop, n_sel = len(population) or 1, len(selected) or 1
        distribution[key] = OrderedDict(
            (
                value,
                {
                    "population_pct": round(100.0 * pop_counts[value] / n_pop, 1),
                    "sample_pct": round(100.0 * sel_counts.get(value, 0) / n_sel, 1),
                    "sample_n": sel_counts.get(value, 0),
                },
            )
            for value in sorted(pop_counts)
        )
    return SampleReport(
        n_requested=n_requested or len(population),
        n_selected=len(selected),
        n_population=len(population),
        seed=seed,
        strata_keys=keys,
        case_ids=[_case_id(e, i) for i, e in enumerate(selected)],
        distribution=distribution,
    )


def load_cases(path: str) -> list[dict[str, Any]]:
    """Load a benchmark case file (a JSON list of entries)."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        data = data.get("cases") or data.get("entries") or []
    if not isinstance(data, list):
        raise ValueError(f"{path} does not contain a list of benchmark entries")
    return data


def write_cases(path: str, entries: Iterable[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(list(entries), handle, indent=2)
