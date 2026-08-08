"""MedAgentBench adapter — Whissle as the agent under test.

MedAgentBench (Jiang et al., NEJM AI 2025; stanfordmlgroup/MedAgentBench) is a
FHIR-grounded clinical agent benchmark: 300 physician-written tasks over a
virtual EHR seeded with 100 patient profiles.

This package plugs the Whissle brain into that benchmark in two clearly
labelled modes (see `MEDAGENTBENCH.md`) and adds a write-integrity layer the
upstream harness does not have: it separates "the agent said it ordered" from
"the FHIR resource was actually created".
"""

from tau2.health.medagent.data import (
    ACTION_CATEGORIES,
    QUERY_CATEGORIES,
    Case,
    category_of,
    load_cases,
)

__all__ = [
    "ACTION_CATEGORIES",
    "QUERY_CATEGORIES",
    "Case",
    "category_of",
    "load_cases",
]
