"""MedAgentBench task data: fetch, load, filter.

The task set and the FHIR function catalogue live in the upstream repo
(stanfordmlgroup/MedAgentBench, MIT). We do not vendor them — `fetch_data()`
pulls them into `data/medagentbench/` so a run is always measured against
upstream's current files rather than a copy that silently drifts.

Task ids are `taskN_M`; `taskN` is the category. The paper reports an overall
success rate plus a Query/Action split, where Query categories are the
read-only ones and Action categories are the ones that can require a write.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

DATA_DIR = Path("data/medagentbench")
TASKS_FILE = DATA_DIR / "test_data_v2.json"
FUNCS_FILE = DATA_DIR / "funcs_v1.json"

_RAW_BASE = "https://raw.githubusercontent.com/stanfordmlgroup/MedAgentBench/main/data/medagentbench"

# Read-only categories: the grader rejects the trajectory if it contains ANY
# POST. Action categories: a write is required, or is required conditionally.
QUERY_CATEGORIES = ("task1", "task2", "task4", "task6", "task7")
ACTION_CATEGORIES = ("task3", "task5", "task8", "task9", "task10")
ALL_CATEGORIES = tuple(
    sorted(QUERY_CATEGORIES + ACTION_CATEGORIES, key=lambda c: int(c[4:]))
)

# Published baselines (MedAgentBench, NEJM AI 2025) — success rate in %, over
# the full 300-task set. Carried here so our report can print the comparison
# inline instead of asking the reader to go find the paper.
PUBLISHED_BASELINES: dict[str, dict[str, float]] = {
    "Claude 3.5 Sonnet v2": {"overall": 69.67, "query": 85.33, "action": 54.00},
    "GPT-4o": {"overall": 64.00},
    "DeepSeek-V3": {"overall": 62.67},
    "Gemini-1.5 Pro": {"overall": 62.00},
    "GPT-4o-mini": {"overall": 56.33},
    "o3-mini": {"overall": 51.67},
    "Qwen2.5": {"overall": 51.33},
    "Llama 3.3": {"overall": 46.33},
    "Gemini 2.0 Flash": {"overall": 38.33},
    "Gemma2": {"overall": 19.33},
    "Mistral v0.3": {"overall": 4.00},
}


@dataclass(frozen=True)
class Case:
    """One MedAgentBench task."""

    id: str
    instruction: str
    context: str
    # Only the 30 task1 cases carry `sol`; every other category's expected
    # answer is recomputed from live chart state at grading time.
    sol: Optional[list[Any]]
    # Absent on the two task1 "patient does not exist" cases — there is no
    # patient to evaluate against.
    eval_mrn: Optional[str] = None

    @property
    def category(self) -> str:
        return category_of(self.id)

    @property
    def is_action(self) -> bool:
        return self.category in ACTION_CATEGORIES

    @property
    def raw(self) -> dict[str, Any]:
        """The dict shape the upstream graders (and `refsol.py`) expect."""
        d: dict[str, Any] = {
            "id": self.id,
            "instruction": self.instruction,
            "context": self.context,
        }
        # Mirror upstream exactly: keys the source file omits stay omitted, so
        # a grader that probes with `in` behaves the same either way.
        if self.eval_mrn is not None:
            d["eval_MRN"] = self.eval_mrn
        if self.sol is not None:
            d["sol"] = self.sol
        return d


def category_of(task_id: str) -> str:
    """`task10_7` -> `task10`."""
    return task_id.split("_")[0]


def fetch_data(dest: Path = DATA_DIR, force: bool = False) -> tuple[Path, Path]:
    """Download the upstream task + function files if not already present."""
    import requests

    dest.mkdir(parents=True, exist_ok=True)
    out = []
    for name in ("test_data_v2.json", "funcs_v1.json"):
        path = dest / name
        if path.exists() and not force:
            out.append(path)
            continue
        r = requests.get(f"{_RAW_BASE}/{name}", timeout=60)
        r.raise_for_status()
        path.write_text(r.text, encoding="utf-8")
        out.append(path)
    return out[0], out[1]


def load_funcs(path: Path = FUNCS_FILE) -> list[dict[str, Any]]:
    """The 9-entry FHIR function catalogue shown to the agent verbatim."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `medagentbench fetch` first."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def load_cases(
    path: Path = TASKS_FILE,
    *,
    categories: Optional[Iterable[str]] = None,
    task_ids: Optional[Iterable[str]] = None,
    limit: Optional[int] = None,
    stratified: bool = True,
) -> list[Case]:
    """Load tasks, optionally filtered.

    `limit` with `stratified=True` takes a round-robin slice across categories
    so a cheap subset run still covers every category (and both Query and
    Action) rather than 10 consecutive task1 cases. Always report N.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — run `medagentbench fetch` first."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    cases = [
        Case(
            id=c["id"],
            instruction=c["instruction"],
            context=c.get("context", ""),
            sol=c.get("sol"),
            eval_mrn=c.get("eval_MRN"),
        )
        for c in raw
    ]

    if task_ids:
        wanted = set(task_ids)
        # Accept both exact ids (`task3_4`) and bare categories (`task3`).
        cases = [
            c for c in cases if c.id in wanted or c.category in wanted
        ]
    if categories:
        cats = set(categories)
        cases = [c for c in cases if c.category in cats]

    if limit is not None and limit < len(cases):
        cases = _stratify(cases, limit) if stratified else cases[:limit]
    return cases


def _stratify(cases: list[Case], limit: int) -> list[Case]:
    """Round-robin across categories, preserving in-category order."""
    buckets: dict[str, list[Case]] = {}
    for c in cases:
        buckets.setdefault(c.category, []).append(c)
    order = sorted(buckets, key=lambda c: int(c[4:]))
    out: list[Case] = []
    idx = 0
    while len(out) < limit:
        progressed = False
        for cat in order:
            if idx < len(buckets[cat]):
                out.append(buckets[cat][idx])
                progressed = True
                if len(out) == limit:
                    return out
        if not progressed:
            break
        idx += 1
    return out


def fhir_api_base() -> str:
    """Benchmark FHIR base URL, always with a trailing slash.

    Upstream's `fhir_api_base` ends in `/` and every grader builds URLs by
    direct concatenation (`f"{base}Observation"`), so the trailing slash is
    load-bearing for URL equality checks.
    """
    base = os.getenv("MEDAGENTBENCH_FHIR_BASE", "http://localhost:8080/fhir/")
    return base if base.endswith("/") else base + "/"
