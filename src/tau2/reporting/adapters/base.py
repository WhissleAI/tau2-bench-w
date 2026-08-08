"""The adapter interface. Adding a benchmark is implementing this and one
registry line — nothing else in the reporting layer changes.

Two methods:

``detect(run_dir)``  cheap, filesystem-only, must never raise.
``build(run_dir)``   returns a :class:`~tau2.reporting.model.RunReport`, and must
                     **degrade rather than crash** on a partial or malformed run
                     directory: set ``status='partial'``, explain in
                     ``partial_reason``, and report whatever is present.

The second rule matters more than it looks. Run directories are read while runs are
still writing into them, and a generator that throws on a half-written tree is a
generator nobody runs.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from ..model import Artifact, RunReport


@dataclass
class BuildContext:
    """Anything the adapter needs that is not in the run directory."""

    repo_root: Optional[Path] = None
    #: results/whissle — used to compute stable, repo-relative run ids
    results_root: Optional[Path] = None
    warnings: list[str] = field(default_factory=list)

    def repo_commit(self) -> Optional[str]:
        if not self.repo_root:
            return None
        try:
            out = subprocess.run(
                ["git", "-C", str(self.repo_root), "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return out.stdout.strip() or None
        except Exception:
            return None

    def run_id(self, run_dir: Path) -> str:
        root = self.results_root or self.repo_root
        try:
            if root:
                return str(run_dir.resolve().relative_to(Path(root).resolve()))
        except Exception:
            pass
        return run_dir.name


@runtime_checkable
class RunAdapter(Protocol):
    benchmark: str
    benchmark_title: str

    @classmethod
    def detect(cls, run_dir: Path) -> bool: ...

    @classmethod
    def build(cls, run_dir: Path, ctx: BuildContext) -> RunReport: ...


# ---------------------------------------------------------------------------
# Tolerant IO — every read a benchmark adapter does goes through these.
# ---------------------------------------------------------------------------


def read_json(path: Path) -> Optional[Any]:
    """Never raises. A truncated file mid-write returns ``None``, not a traceback."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


def read_json_dir(directory: Path, pattern: str = "*.json") -> tuple[list[Any], list[str]]:
    """Returns ``(records, unreadable_filenames)``."""
    records: list[Any] = []
    bad: list[str] = []
    if not directory.is_dir():
        return records, bad
    for p in sorted(directory.glob(pattern)):
        rec = read_json(p)
        if rec is None:
            bad.append(p.name)
        else:
            records.append(rec)
    return records, bad


def dig(obj: Any, *keys: str, default: Any = None) -> Any:
    """``obj['a']['b']`` that survives ``None`` and non-dicts at every hop."""
    cur = obj
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return default if cur is None else cur


def artifacts_for(run_dir: Path, entries: list[tuple[str, str]]) -> list[Artifact]:
    """``entries`` is ``[(relative_path, description)]``; presence is checked."""
    out = []
    for rel, desc in entries:
        p = run_dir / rel
        out.append(Artifact(path=rel, description=desc, present=p.exists()))
    return out


def wilson_ci(k: int, n: int, z: float = 1.96) -> Optional[tuple[float, float]]:
    """Wilson score interval for a proportion, as percentages.

    Used where an adapter's own summary did not already compute one — a rate
    printed without an interval invites over-reading a 100-case run.
    """
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / denom
    return (round(100 * max(0.0, centre - half), 1), round(100 * min(1.0, centre + half), 1))


def diagnostics_of(record: Any) -> dict[str, Any]:
    """The ``tau2.health.diagnostics/v1`` block, or ``{}`` for older records."""
    d = record.get("diagnostics") if isinstance(record, dict) else None
    return d if isinstance(d, dict) else {}
