# Copyright Sierra
"""Flow scenarios: fixture loading + the pure assertion logic.

A scenario fixture (``data/flow/*.json``) is self-describing: the agent spec, the
flow state-machine definition, the scripted user turns, and the expected outcome
(verbatim say-markers, tool calls, state sequence, fired transitions). This module
turns one into a :class:`Scenario`, and — given the collected turn results and the
(maybe-absent) step trace — grades it into a list of :class:`Assertion` results.

The grading is split into two tiers so the suite is meaningful TODAY and becomes
strict once the ``flow-step-trace`` backend PR ships:

  * ``observable``  — asserted on the agent's real replies + ``tools_used``. These
    run every time. A verbatim say-marker in the reply proves the corresponding
    say-state executed; a tool name in ``tools_used`` proves that tool state fired
    and gating admitted it; a gated-out tool's ABSENCE proves per-state gating.
  * ``trace``       — asserted on ``flow.steps`` / the GET trace (state sequence,
    fired transitions, guard trips). Until the trace field is deployed these are
    reported as ``skipped-pending-trace`` rather than failed.

Pure and I/O-free: the runner (benchmark.py) owns the network + logging.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

DATA_DIR = Path("data/flow")

Status = Literal["pass", "fail", "skipped-pending-trace"]
Tier = Literal["observable", "trace"]


# ── scenario model ────────────────────────────────────────────────────────────

@dataclass
class TurnSpec:
    user: str
    expect_reply_contains: list[str] = field(default_factory=list)
    expect_tools: list[str] = field(default_factory=list)
    expect_tools_absent: list[str] = field(default_factory=list)


@dataclass
class Scenario:
    id: str
    title: str
    description: str
    agent: dict[str, Any]
    flow: dict[str, Any]
    turns: list[TurnSpec]
    expect_say_markers: list[str] = field(default_factory=list)
    expect_state_sequence: list[str] = field(default_factory=list)
    expect_fired_transitions: list[str] = field(default_factory=list)
    expect_guard_trip: Optional[dict[str, Any]] = None

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Scenario":
        return Scenario(
            id=d["id"],
            title=d.get("title", d["id"]),
            description=d.get("description", ""),
            agent=d["agent"],
            flow=d["flow"],
            turns=[TurnSpec(**t) for t in d.get("turns", [])],
            expect_say_markers=d.get("expect_say_markers", []),
            expect_state_sequence=d.get("expect_state_sequence", []),
            expect_fired_transitions=d.get("expect_fired_transitions", []),
            expect_guard_trip=d.get("expect_guard_trip"),
        )


def load_scenario(id_or_path: str) -> Scenario:
    p = Path(id_or_path)
    if not p.exists():
        p = DATA_DIR / f"{id_or_path}.json"
    return Scenario.from_dict(json.loads(p.read_text(encoding="utf-8")))


def all_scenario_ids() -> list[str]:
    return sorted(p.stem for p in DATA_DIR.glob("*.json"))


# ── assertion result ──────────────────────────────────────────────────────────

@dataclass
class Assertion:
    name: str
    tier: Tier
    status: Status
    detail: str = ""

    @property
    def ok(self) -> bool:
        # A pending-trace skip is not a failure — it is a promise the trace deploy
        # will keep. Only an outright ``fail`` sinks the run.
        return self.status != "fail"


# ── trace parsing ─────────────────────────────────────────────────────────────

def _enters(steps: list[dict]) -> list[str]:
    return [s.get("state") for s in steps if s.get("kind") == "state_enter"]


def _fired(steps: list[dict]) -> list[str]:
    return [
        s.get("transition_id") for s in steps
        if s.get("kind") == "transition_check" and s.get("result") == "fired"
    ]


def _guard_trips(steps: list[dict]) -> list[dict]:
    return [s for s in steps if s.get("kind") == "guard_trip"]


def _is_ordered_subsequence(needle: list[str], haystack: list[str]) -> bool:
    """Every element of ``needle`` appears in ``haystack`` in order (gaps allowed).
    Exact for the linear scenarios; tolerant of the loop's repeated re-entries."""
    it = iter(haystack)
    return all(any(h == n for h in it) for n in needle)


# ── grading ───────────────────────────────────────────────────────────────────

def grade_observable(scn: Scenario, turns: list[Any]) -> list[Assertion]:
    """Assertions checkable from replies + tools_used alone. ``turns`` are
    client.TurnResult (duck-typed: ``.reply``, ``.tools_used``)."""
    out: list[Assertion] = []

    # Per-turn reply substrings + tool presence/absence.
    for i, (spec, res) in enumerate(zip(scn.turns, turns), start=1):
        for sub in spec.expect_reply_contains:
            hit = sub in (res.reply or "")
            out.append(Assertion(
                f"turn{i}.reply_contains[{sub!r}]", "observable",
                "pass" if hit else "fail",
                "" if hit else f"marker not in reply: {res.reply!r}",
            ))
        for tool in spec.expect_tools:
            hit = tool in (res.tools_used or [])
            out.append(Assertion(
                f"turn{i}.tool_called[{tool}]", "observable",
                "pass" if hit else "fail",
                "" if hit else f"tools_used={res.tools_used}",
            ))
        for tool in spec.expect_tools_absent:
            absent = tool not in (res.tools_used or [])
            out.append(Assertion(
                f"turn{i}.tool_gated_out[{tool}]", "observable",
                "pass" if absent else "fail",
                "" if absent else f"tool NOT gated out — appeared in tools_used={res.tools_used}",
            ))

    # Scenario-level: every say-marker appears verbatim across the whole transcript.
    transcript = "\n".join(r.reply or "" for r in turns)
    for marker in scn.expect_say_markers:
        hit = marker in transcript
        out.append(Assertion(
            f"say_marker[{marker}]", "observable",
            "pass" if hit else "fail",
            "" if hit else "marker never emitted across the run",
        ))
    return out


def grade_trace(scn: Scenario, steps: list[dict], trace_present: bool) -> list[Assertion]:
    """State-sequence / fired-transition / guard-trip assertions. When the trace
    field is not yet deployed, each is reported as ``skipped-pending-trace``."""
    def skipped(name: str) -> Assertion:
        return Assertion(name, "trace", "skipped-pending-trace",
                         "flow.steps / trace not present — pending flow-step-trace deploy")

    out: list[Assertion] = []

    if scn.expect_state_sequence:
        name = "state_sequence"
        if not trace_present:
            out.append(skipped(name))
        else:
            observed = _enters(steps)
            ok = _is_ordered_subsequence(scn.expect_state_sequence, observed)
            out.append(Assertion(
                name, "trace", "pass" if ok else "fail",
                f"expected(subseq)={scn.expect_state_sequence} observed={observed}",
            ))

    if scn.expect_fired_transitions:
        name = "fired_transitions"
        if not trace_present:
            out.append(skipped(name))
        else:
            fired = _fired(steps)
            missing = [t for t in scn.expect_fired_transitions if t not in fired]
            out.append(Assertion(
                name, "trace", "pass" if not missing else "fail",
                f"missing={missing} fired={fired}",
            ))

    if scn.expect_guard_trip:
        name = "guard_trip"
        if not trace_present:
            out.append(skipped(name))
        else:
            want = scn.expect_guard_trip
            trips = _guard_trips(steps)
            ok = any(
                g.get("guard") == want.get("guard") and g.get("state") == want.get("state")
                for g in trips
            )
            out.append(Assertion(
                name, "trace", "pass" if ok else "fail",
                f"want={want} trips={trips}",
            ))

    return out
