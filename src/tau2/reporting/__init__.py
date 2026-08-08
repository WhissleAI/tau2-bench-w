"""Research-paper-grade reporting over benchmark run directories.

One report per run, maintained over time, plus a cross-run index and a publishable
export for the website. Nothing in here runs a benchmark: every command is a pure
function of artifacts already on disk, so it is idempotent and safe to re-run.

    from tau2.reporting import build_report
    report, markdown = build_report(Path("results/whissle/medagentbench/brain-parity_mab_100"))

Layout
------
``model``       the benchmark-agnostic ``RunReport`` everything else reads
``adapters/``   one file per benchmark — the only place a benchmark's shape is known
``honesty``     the five rules, executable
``render_md``   ``RunReport`` → REPORT.md
``index``       the accumulating cross-run index and the regression view
``web_export``  the JSON contract the public /benchmark page consumes
``cli``         ``python -m tau2.reporting``
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .model import RunReport

__all__ = ["RunReport", "build_report", "SCHEMA"]

from .model import SCHEMA  # noqa: E402


def build_report(run_dir: Path, repo_root: Optional[Path] = None) -> tuple[RunReport, str]:
    """Build a report for one run directory. Never runs a benchmark."""
    from .adapters import BuildContext, adapter_for
    from .render_md import render

    run_dir = Path(run_dir)
    adapter = adapter_for(run_dir)
    if adapter is None:
        raise ValueError(f"no reporting adapter recognises {run_dir}")
    ctx = BuildContext(
        repo_root=repo_root,
        results_root=(Path(repo_root) / "results" / "whissle") if repo_root else None,
    )
    report = adapter.build(run_dir, ctx)
    return report, render(report)
