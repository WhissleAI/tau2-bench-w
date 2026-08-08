# Copyright Sierra
"""Scenario definitions: data on disk, dataclasses here, no scenarios in Python.

A scenario in this package is not "a script that gets a pass or a fail". It is a
falsifiable claim about *mechanism*, carrying three things:

``hypothesis``
    What we expect a cascade (separable ASR → LLM → flow engine → TTS) to do
    here relative to an opaque speech-to-speech model, why, and — the field that
    keeps this honest — a ``falsifier``: what result would show the claim wrong.
``pass_criteria``
    Deterministic, per-transcript checks. No LLM judge: a judge that is also the
    vendor's model is the exact independence problem
    ``tau2.health.model_router.is_independent`` exists to flag, and a comparison
    that needs a judge to see its own result is not a comparison.
``trace_evidence``
    What must appear in the WHISSLE flow trace for the mechanism to have fired.
    This is the part a pass/fail cannot give you. A scenario that passes without
    its trace evidence passed for some other reason, and the report says so
    rather than crediting the mechanism.

The definitions live in ``data/compare/scenarios.json`` so they can be edited,
diffed and extended without touching code — the same shape as
``data/flow/sim_tasks.json``, whose personas informed these.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

#: Where a cascade is expected to land relative to an opaque speech-to-speech
#: system. ``undetermined`` is a first-class answer, not a cop-out: some of these
#: are not decidable on the transport we can drive today.
CASCADE_WINS = "cascade_wins"
CASCADE_LOSES = "cascade_loses"
UNDETERMINED = "undetermined"
EXPECTATIONS = (CASCADE_WINS, CASCADE_LOSES, UNDETERMINED)

TRANSPORT_TEXT = "text"
TRANSPORT_VOICE = "voice"

DEFAULT_SCENARIO_FILE = "scenarios.json"


class ScenarioError(ValueError):
    """A scenario file that cannot be loaded honestly."""


@dataclass(frozen=True)
class Hypothesis:
    """The claim a scenario tests, plus what would disprove it."""

    expectation: str
    claim: str
    mechanism: str
    falsifier: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "expectation": self.expectation,
            "claim": self.claim,
            "mechanism": self.mechanism,
            "falsifier": self.falsifier,
        }


@dataclass(frozen=True)
class Criterion:
    """One deterministic check against a transcript.

    ``check`` is the machine-readable spec consumed by
    :mod:`tau2.compare.criteria`. ``critical`` marks a criterion whose failure
    fails the scenario outright regardless of the others — used for the
    fabrication and write-integrity checks, where a partial pass is meaningless.
    """

    id: str
    description: str
    check: dict[str, Any]
    critical: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "check": self.check,
            "critical": self.critical,
        }


@dataclass(frozen=True)
class TraceEvidence:
    """What must show up in the Whissle flow trace for the mechanism to have fired.

    ``requires_metadata_head`` is the tell that a scenario's mechanism runs through
    the acoustic metadata head — which, per :mod:`tau2.compare.honesty`, is down.
    Such a scenario cannot prove its mechanism today no matter what the pass/fail
    says, and the report must print that instead of quietly claiming the win."""

    description: str
    expect_step_kinds: tuple[str, ...] = ()
    expect_vars_set: tuple[str, ...] = ()
    expect_var_sources: tuple[str, ...] = ()
    expect_transition_fired: bool = False
    expect_transition_reason_contains: tuple[str, ...] = ()
    expect_no_vars_set: tuple[str, ...] = ()
    expect_guard_trip: bool = False
    requires_metadata_head: bool = False
    #: The mechanism lives in the per-turn VOICE signals (barge-in, turn
    #: completeness, response latency). A text run does not have them — it does
    #: not have them at zero, it does not have them at all — so such a scenario
    #: reports "cannot tell" on text rather than a verdict.
    requires_voice_signals: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "expect_step_kinds": list(self.expect_step_kinds),
            "expect_vars_set": list(self.expect_vars_set),
            "expect_var_sources": list(self.expect_var_sources),
            "expect_transition_fired": self.expect_transition_fired,
            "expect_transition_reason_contains": list(
                self.expect_transition_reason_contains
            ),
            "expect_no_vars_set": list(self.expect_no_vars_set),
            "expect_guard_trip": self.expect_guard_trip,
            "requires_metadata_head": self.requires_metadata_head,
            "requires_voice_signals": self.requires_voice_signals,
        }


@dataclass(frozen=True)
class Scenario:
    """One comparison scenario."""

    id: str
    title: str
    agent_type: str
    system_prompt: str
    hypothesis: Hypothesis
    turns: tuple[str, ...]
    pass_criteria: tuple[Criterion, ...]
    trace_evidence: TraceEvidence
    transports: tuple[str, ...] = (TRANSPORT_TEXT,)
    #: What a text-channel run does and does NOT measure, for scenarios whose real
    #: mechanism is acoustic. Printed beside the result — a proxy silently standing
    #: in for the real thing is how a comparison becomes a lie.
    proxy_note: Optional[str] = None
    persona: Optional[str] = None
    notes: Optional[str] = None
    raw: dict[str, Any] = field(default_factory=dict)

    def supports(self, transport: str) -> bool:
        return transport in self.transports

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "agent_type": self.agent_type,
            "hypothesis": self.hypothesis.to_dict(),
            "transports": list(self.transports),
            "proxy_note": self.proxy_note,
            "persona": self.persona,
            "notes": self.notes,
            "n_turns": len(self.turns),
            "turns": list(self.turns),
            "pass_criteria": [c.to_dict() for c in self.pass_criteria],
            "trace_evidence": self.trace_evidence.to_dict(),
        }


# ── loading ─────────────────────────────────────────────────────────────────────


def default_data_dir() -> str:
    """``data/compare`` resolved from this file, so the CLI works from any cwd."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", "..", ".."))
    return os.path.join(root, "data", "compare")


def default_path() -> str:
    return os.path.join(default_data_dir(), DEFAULT_SCENARIO_FILE)


def _require(node: dict[str, Any], key: str, where: str) -> Any:
    if key not in node or node[key] in (None, ""):
        raise ScenarioError(f"{where}: missing required field {key!r}")
    return node[key]


def _parse_hypothesis(node: Any, where: str) -> Hypothesis:
    if not isinstance(node, dict):
        raise ScenarioError(f"{where}: 'hypothesis' must be an object")
    expectation = _require(node, "expectation", where)
    if expectation not in EXPECTATIONS:
        raise ScenarioError(
            f"{where}: hypothesis.expectation {expectation!r} not in {EXPECTATIONS}"
        )
    return Hypothesis(
        expectation=expectation,
        claim=_require(node, "claim", where),
        mechanism=_require(node, "mechanism", where),
        # Required on purpose: a hypothesis nobody can disprove is marketing.
        falsifier=_require(node, "falsifier", where),
    )


def _parse_criterion(node: Any, where: str) -> Criterion:
    if not isinstance(node, dict):
        raise ScenarioError(f"{where}: each pass_criteria entry must be an object")
    check = _require(node, "check", where)
    if not isinstance(check, dict) or "kind" not in check:
        raise ScenarioError(f"{where}: criterion 'check' needs a 'kind'")
    return Criterion(
        id=_require(node, "id", where),
        description=_require(node, "description", where),
        check=dict(check),
        critical=bool(node.get("critical", False)),
    )


def _parse_trace_evidence(node: Any, where: str) -> TraceEvidence:
    if not isinstance(node, dict):
        raise ScenarioError(f"{where}: 'trace_evidence' must be an object")
    return TraceEvidence(
        description=_require(node, "description", where),
        expect_step_kinds=tuple(node.get("expect_step_kinds") or ()),
        expect_vars_set=tuple(node.get("expect_vars_set") or ()),
        expect_var_sources=tuple(node.get("expect_var_sources") or ()),
        expect_transition_fired=bool(node.get("expect_transition_fired", False)),
        expect_transition_reason_contains=tuple(
            node.get("expect_transition_reason_contains") or ()
        ),
        expect_no_vars_set=tuple(node.get("expect_no_vars_set") or ()),
        expect_guard_trip=bool(node.get("expect_guard_trip", False)),
        requires_metadata_head=bool(node.get("requires_metadata_head", False)),
        requires_voice_signals=bool(node.get("requires_voice_signals", False)),
    )


def parse_scenario(node: dict[str, Any]) -> Scenario:
    sid = node.get("id") or "<unnamed>"
    where = f"scenario {sid!r}"
    turns = _require(node, "turns", where)
    if not isinstance(turns, list) or not all(isinstance(t, str) for t in turns):
        raise ScenarioError(f"{where}: 'turns' must be a list of user utterances")
    criteria = _require(node, "pass_criteria", where)
    if not isinstance(criteria, list) or not criteria:
        raise ScenarioError(f"{where}: 'pass_criteria' must be a non-empty list")
    transports = tuple(node.get("transports") or (TRANSPORT_TEXT,))
    unknown = [t for t in transports if t not in (TRANSPORT_TEXT, TRANSPORT_VOICE)]
    if unknown:
        raise ScenarioError(f"{where}: unknown transport(s) {unknown}")
    return Scenario(
        id=sid,
        title=_require(node, "title", where),
        agent_type=_require(node, "agent_type", where),
        system_prompt=_require(node, "system_prompt", where),
        hypothesis=_parse_hypothesis(_require(node, "hypothesis", where), where),
        turns=tuple(turns),
        pass_criteria=tuple(_parse_criterion(c, where) for c in criteria),
        trace_evidence=_parse_trace_evidence(
            _require(node, "trace_evidence", where), where
        ),
        transports=transports,
        proxy_note=node.get("proxy_note"),
        persona=node.get("persona"),
        notes=node.get("notes"),
        raw=dict(node),
    )


def load(path: Optional[str] = None) -> list[Scenario]:
    """Load every scenario from the JSON file (default ``data/compare``)."""
    p = path or default_path()
    if not os.path.exists(p):
        raise ScenarioError(f"scenario file not found: {p}")
    with open(p, encoding="utf-8") as handle:
        payload = json.load(handle)
    nodes = payload.get("scenarios") if isinstance(payload, dict) else payload
    if not isinstance(nodes, list) or not nodes:
        raise ScenarioError(f"{p}: expected a non-empty 'scenarios' list")
    scenarios = [parse_scenario(n) for n in nodes]
    seen: set[str] = set()
    for s in scenarios:
        if s.id in seen:
            raise ScenarioError(f"{p}: duplicate scenario id {s.id!r}")
        seen.add(s.id)
    return scenarios


def load_map(path: Optional[str] = None) -> dict[str, Scenario]:
    return {s.id: s for s in load(path)}


def select(ids: Optional[list[str]], path: Optional[str] = None) -> list[Scenario]:
    """Scenarios by id, preserving the file's order. Unknown ids raise rather than
    silently shrinking the run — a comparison that quietly dropped a scenario is a
    comparison whose denominator moved."""
    all_scenarios = load(path)
    if not ids:
        return all_scenarios
    known = {s.id for s in all_scenarios}
    missing = [i for i in ids if i not in known]
    if missing:
        raise ScenarioError(
            f"unknown scenario id(s): {', '.join(missing)}. "
            f"Known: {', '.join(sorted(known))}"
        )
    wanted = set(ids)
    return [s for s in all_scenarios if s.id in wanted]
