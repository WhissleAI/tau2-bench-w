"""Benchmark adapters.

**Adding a benchmark is two steps and neither of them is here-be-dragons:**

1. write ``adapters/<name>.py`` with a class exposing ``benchmark``,
   ``benchmark_title``, ``detect(run_dir)`` and ``build(run_dir, ctx)``;
2. append it to :data:`ADAPTERS`.

Nothing else in the reporting layer knows benchmarks exist. The renderer, the
honesty rules, the cross-run index and the website export all read
:class:`~tau2.reporting.model.RunReport` and nothing else.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .agentclinic import AgentClinicAdapter
from .base import BuildContext, RunAdapter
from .flow_sim import FlowSimAdapter
from .medagent import MedAgentBenchAdapter
from .patientagent import PatientAgentBenchAdapter

#: Order matters only for ambiguous directories; the more specific detectors come
#: first so a generic ``cases/`` directory does not win over a benchmark marker.
ADAPTERS: tuple[type, ...] = (
    MedAgentBenchAdapter,
    AgentClinicAdapter,
    FlowSimAdapter,
    PatientAgentBenchAdapter,
)

BY_NAME = {a.benchmark: a for a in ADAPTERS}


def adapter_for(run_dir: Path) -> Optional[type]:
    """The first adapter that recognises this directory, or ``None``.

    A benchmark hint in the path wins over structural detection, because two
    benchmarks legitimately both write a ``cases/`` directory.
    """
    parts = {p.lower() for p in run_dir.resolve().parts}
    for a in ADAPTERS:
        if a.benchmark in parts and a.detect(run_dir):
            return a
    for a in ADAPTERS:
        if a.detect(run_dir):
            return a
    return None


__all__ = [
    "ADAPTERS",
    "BY_NAME",
    "BuildContext",
    "RunAdapter",
    "adapter_for",
    "AgentClinicAdapter",
    "FlowSimAdapter",
    "MedAgentBenchAdapter",
    "PatientAgentBenchAdapter",
]
