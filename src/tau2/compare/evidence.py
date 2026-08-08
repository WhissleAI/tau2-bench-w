# Copyright Sierra
"""Did the mechanism fire? Reading the Whissle flow trace for a scenario's claim.

A scenario's ``pass_criteria`` answer "did the agent do the right thing". This
module answers the question that actually justifies a cascade: "did it do the
right thing *for the stated reason*". Those come apart constantly — an agent can
recover a mis-heard name because the LLM guessed well, with the flow engine
having played no part. A comparison that credits the flow engine for that is
selling the wrong component.

So a scenario declares :class:`tau2.compare.scenarios.TraceEvidence`, and this
module checks it against the ``flow`` section of the run's
``tau2.health.diagnostics`` envelope. Three outcomes, and the third matters most:

``found``       the trace shows the declared steps — the mechanism fired.
``absent``      the trace exists and does NOT show them — the outcome, good or
                bad, was produced by something other than the claimed mechanism.
``cannot_tell`` there is no trace to read (bench endpoint, fetch failure, or a
                vendor that publishes nothing), so no claim either way.

``cannot_tell`` is never rendered as a pass. It is also never rendered as a
failure of the agent — it is a failure of *observability*, which for a product
whose pitch is observability is itself the finding.

The excerpt this module extracts is what the report prints. It is the deliverable:
the reader should be able to see, in the agent's own trace, why it did what it did.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from tau2.compare import honesty

FOUND = "found"
ABSENT = "absent"
CANNOT_TELL = "cannot_tell"

#: A mechanism that runs through the acoustic metadata head cannot be proven while
#: the head is down, whatever the transcript shows.
REASON_HEAD_DOWN = (
    "this scenario's mechanism runs through the Whissle acoustic metadata head, "
    "which is disabled (see the banner) — the mechanism therefore CANNOT have "
    "fired on this run, and any pass here was produced by the LLM and flow engine "
    "alone"
)


@dataclass
class EvidenceResult:
    """Whether a scenario's declared trace evidence is present."""

    status: str
    reason: str
    satisfied: list[str] = field(default_factory=list)
    unsatisfied: list[str] = field(default_factory=list)
    excerpt: list[dict[str, Any]] = field(default_factory=list)
    narrative: list[str] = field(default_factory=list)

    @property
    def fired(self) -> Optional[bool]:
        if self.status == CANNOT_TELL:
            return None
        return self.status == FOUND

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "mechanism_fired": self.fired,
            "reason": self.reason,
            "satisfied": self.satisfied,
            "unsatisfied": self.unsatisfied,
            "excerpt": self.excerpt,
            "narrative": self.narrative,
        }


def _contains_any(text: Any, needles: tuple[str, ...]) -> bool:
    t = str(text or "").lower()
    return any(n.lower() in t for n in needles)


#: The backend has spelled a ``var_set``'s field name three ways across versions
#: (``key`` in the live flow engine, ``name``/``var`` elsewhere). Read all three.
#: This matters more than it looks: ``expect_no_vars_set`` is the anti-fabrication
#: evidence, and a name we fail to recognise would make a fabricated write look
#: like a clean run.
_VAR_NAME_KEYS = ("name", "var", "key", "field")


def _var_name(step: dict[str, Any]) -> Optional[str]:
    for key in _VAR_NAME_KEYS:
        value = step.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _vars_set_in(flow: dict[str, Any]) -> set[str]:
    """Field names written during the call, read from the VERBATIM steps.

    Deliberately not from the derived ``var_sets`` rollup: that rollup normalises
    on ``name``/``var`` only, so a step keyed ``key`` arrives with ``name: None``
    and the field silently disappears."""
    out: set[str] = set()
    for step in flow.get("steps") or []:
        if isinstance(step, dict) and step.get("kind") == "var_set":
            name = _var_name(step)
            if name:
                out.add(name)
    for row in flow.get("var_sets") or []:
        if isinstance(row, dict) and isinstance(row.get("name"), str):
            out.add(row["name"])
    return out


def evaluate(scenario: Any, run: Any) -> EvidenceResult:
    """Check a scenario's :class:`TraceEvidence` against one run's flow section."""
    spec = scenario.trace_evidence

    if spec.requires_metadata_head and honesty.DIFFERENTIATOR_OUTAGE is not None:
        return EvidenceResult(
            status=CANNOT_TELL,
            reason=REASON_HEAD_DOWN,
            unsatisfied=["requires_metadata_head"],
        )

    if spec.requires_voice_signals:
        signals = ((getattr(run, "diagnostics", None) or {}).get("signals") or {})
        if not signals.get("available"):
            return EvidenceResult(
                status=CANNOT_TELL,
                reason=(
                    "this scenario's mechanism lives in the per-turn VOICE signals, "
                    "which were not captured on this run — "
                    + str(signals.get("reason") or "no signals section present")
                ),
                unsatisfied=["requires_voice_signals"],
            )

    flow = getattr(run, "flow_section", None)
    if not flow or not flow.get("available"):
        return EvidenceResult(
            status=CANNOT_TELL,
            reason=(
                (flow or {}).get("reason")
                or "no flow trace was captured for this run"
            ),
        )

    satisfied: list[str] = []
    unsatisfied: list[str] = []

    counts = flow.get("step_counts_by_kind") or {}
    for kind in spec.expect_step_kinds:
        (satisfied if counts.get(kind) else unsatisfied).append(f"step:{kind}")

    names_set = _vars_set_in(flow)
    for name in spec.expect_vars_set:
        (satisfied if name in names_set else unsatisfied).append(f"var_set:{name}")
    for name in spec.expect_no_vars_set:
        # The fabrication check, read from the engine's own writes: a field the
        # caller never supplied must not appear as a set variable.
        (satisfied if name not in names_set else unsatisfied).append(
            f"no_var_set:{name}"
        )

    sources = set((flow.get("var_sources") or {}).keys()) | {
        str(s.get("source"))
        for s in (flow.get("steps") or [])
        if isinstance(s, dict) and s.get("kind") == "var_set" and s.get("source")
    }
    for source in spec.expect_var_sources:
        (satisfied if source in sources else unsatisfied).append(
            f"var_source:{source}"
        )

    fired = flow.get("transitions_fired") or []
    if spec.expect_transition_fired:
        (satisfied if fired else unsatisfied).append("transition_fired")
    if spec.expect_transition_reason_contains:
        hit = any(
            _contains_any(t.get("reason"), spec.expect_transition_reason_contains)
            for t in (flow.get("transitions") or [])
        )
        (satisfied if hit else unsatisfied).append("transition_reason")

    if spec.expect_guard_trip:
        (satisfied if flow.get("guard_trips") else unsatisfied).append("guard_trip")

    status = FOUND if not unsatisfied else ABSENT
    reason = (
        "the trace shows every declared step of the mechanism"
        if status == FOUND
        else (
            "the trace was read and does NOT show "
            + ", ".join(unsatisfied)
            + " — whatever happened here, the declared mechanism is not what "
              "produced it"
        )
    )
    return EvidenceResult(
        status=status,
        reason=reason,
        satisfied=satisfied,
        unsatisfied=unsatisfied,
        excerpt=excerpt(flow),
        narrative=narrative(flow),
    )


def excerpt(flow: dict[str, Any], limit: int = 24) -> list[dict[str, Any]]:
    """The steps worth printing: every decision, plus enough state changes to
    follow the path. Chatter (bare ``say_emitted``) is dropped only after the
    interesting kinds are exhausted, so a truncated excerpt still explains."""
    if not flow or not flow.get("available"):
        return []
    steps = flow.get("steps") or []
    priority = {
        "transition_check": 0,
        "var_set": 1,
        "guard_trip": 0,
        "state_divergence": 0,
        "tools_gated": 2,
        "state_enter": 2,
        "flow_end": 1,
        "say_emitted": 3,
    }
    ranked = sorted(
        (s for s in steps if isinstance(s, dict)),
        key=lambda s: (priority.get(str(s.get("kind")), 4),
                       s.get("seq") if isinstance(s.get("seq"), int) else 0),
    )
    kept = sorted(
        ranked[:limit],
        key=lambda s: s.get("seq") if isinstance(s.get("seq"), int) else 0,
    )
    return kept


def narrative(flow: dict[str, Any]) -> list[str]:
    """The trace rendered as sentences a reader can check against the transcript.

    This is the "why did the agent do that" line the report is for. Every sentence
    quotes the engine's own recorded ``reason``; nothing here is inferred."""
    if not flow or not flow.get("available"):
        return []
    lines: list[str] = []
    for step in excerpt(flow):
        kind = str(step.get("kind"))
        turn = step.get("turn")
        prefix = f"turn {turn}" if turn is not None else "—"
        if kind == "state_enter":
            lines.append(f"{prefix}: entered state `{step.get('state')}`")
        elif kind == "transition_check":
            result = step.get("result")
            verb = "FIRED" if result in (True, "fired", "taken") else "did not fire"
            # A transition that did NOT fire carries no target — only the condition
            # it was testing. Printing "→ None" there loses the one thing that makes
            # a non-firing branch legible.
            target = step.get("to") or step.get("target")
            arrow = (
                f"→ `{target}`" if target
                else f"[{step.get('condition') or step.get('transition_id') or '?'}]"
            )
            lines.append(
                f"{prefix}: transition `{step.get('from') or step.get('state')}` "
                f"{arrow} {verb} — reason: {step.get('reason') or '(none recorded)'}"
            )
        elif kind == "var_set":
            lines.append(
                f"{prefix}: set `{_var_name(step)}` = {step.get('value')!r} "
                f"(source: {step.get('source')})"
            )
        elif kind == "guard_trip":
            lines.append(
                f"{prefix}: guard `{step.get('guard')}` tripped — "
                f"{step.get('detail') or step.get('reason') or ''}"
            )
        elif kind == "state_divergence":
            lines.append(
                f"{prefix}: DIVERGENCE — expected `{step.get('expected')}`, "
                f"actual `{step.get('actual')}`"
            )
        elif kind == "tools_gated":
            lines.append(f"{prefix}: tools gated to {step.get('allowed')}")
        elif kind == "flow_end":
            lines.append(
                f"{prefix}: flow ended in `{step.get('state')}` — "
                f"{step.get('reason') or ''}"
            )
        elif kind == "say_emitted":
            lines.append(f"{prefix}: said (state `{step.get('state')}`): "
                         f"{step.get('text')!r}")
    return lines
