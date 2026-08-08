"""PatientAgentBench adapter — evaluate Whissle agents on Amazon Science's
patient-facing healthcare agent benchmark.

See ``PATIENTAGENTBENCH.md`` at the repo root for setup, the two evaluation modes,
the voice matrix, and the licensing constraint on publishing these numbers.

Submodules import lazily: ``client``, ``scoring``, ``sampling``, ``report`` and
``collect`` are dependency-light and run anywhere, while ``agents``, ``register``
and ``voice_agent`` need the PatientAgentBench package (and, for voice, the tau2
voice extras) and are only imported when used.
"""

from tau2.health.patientagent.scoring import (  # noqa: F401
    INFRA_FAIL,
    RUBRIC_ORDER,
    RUBRIC_WEIGHTS,
    SessionOutcome,
    aggregate_score,
    classify_session,
    compare_runs,
    merge_jury,
    summarize_run,
)

__all__ = [
    "INFRA_FAIL",
    "RUBRIC_ORDER",
    "RUBRIC_WEIGHTS",
    "SessionOutcome",
    "aggregate_score",
    "classify_session",
    "compare_runs",
    "merge_jury",
    "summarize_run",
]
