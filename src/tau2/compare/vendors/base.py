# Copyright Sierra
"""The vendor seam: what every adapter must produce, and what it must never invent.

TWO RULES THIS INTERFACE ENFORCES
---------------------------------
1. **A result is either measured or explicitly not runnable.** There is no third
   state and no estimate. :meth:`VendorAdapter.preflight` answers "can this
   vendor be driven at all right now?" *before* anything is attempted, and a
   ``False`` answer produces a :class:`ScenarioRun` with ``runnable=False`` and a
   reason — never a synthesised transcript, a plausible number, or a value
   interpolated from published material. There is deliberately no code path in
   this package that can produce a competitor number we did not observe.

2. **A run carries its trace, or says why it has none.** The deliverable of this
   package is the explanation, not the verdict; a ``ScenarioRun`` whose
   ``diagnostics`` block is absent is a run we cannot reason about. Every adapter
   therefore fills the ``tau2.health.diagnostics`` envelope — the same
   ``SCHEMA``-stamped shape the three health benchmarks emit — including the
   honest unavailable sections when a surface does not exist for that vendor.

``tools_visible`` is the field that keeps criterion evaluation truthful across
vendors. Whissle returns ``tool_events`` on every ``chat/turn``; a vendor that
returns only text has no tool record, and a tool-shaped criterion against it must
resolve to "cannot tell", never to "failed". The evaluator in
:mod:`tau2.compare.criteria` keys off this flag.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

#: Canonical not-runnable reasons. Adapters must use these rather than inventing
#: phrasings, for the same reason ``tau2.health.diagnostics`` centralises its
#: availability reasons: three spellings of one gap read as three gaps.
REASON_CREDENTIALS_ABSENT = (
    "not runnable — credentials absent: the vendor's API key and/or agent id are "
    "not present in the environment, so this vendor was never contacted. No number "
    "is reported for it; an absent competitor is not a competitor that scored zero."
)
REASON_TRANSPORT_UNSUPPORTED = (
    "not runnable — this scenario declares a transport this adapter cannot drive"
)
REASON_VENDOR_NO_TRACE = (
    "this vendor exposes no per-turn state or decision trace, so there is no "
    "mechanism-level evidence to read for it — only the transcript"
)
REASON_VENDOR_NO_TOOL_RECORD = (
    "this vendor's API returns no per-turn tool-call record, so tool-shaped "
    "criteria cannot be evaluated against it (absence of a record, not absence of "
    "a call)"
)


@dataclass
class Preflight:
    """Can this vendor be driven right now, and if not, exactly what is missing."""

    vendor: str
    runnable: bool
    reason: Optional[str] = None
    missing_env: tuple[str, ...] = ()
    checked_env: tuple[str, ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "vendor": self.vendor,
            "runnable": self.runnable,
            "reason": self.reason,
            "missing_env": list(self.missing_env),
            "checked_env": list(self.checked_env),
            **self.detail,
        }


@dataclass
class TurnRecord:
    """One exchange, normalised across vendors."""

    index: int
    user: str
    reply: str
    tools: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: Optional[float] = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "user": self.user,
            "reply": self.reply,
            "tools": self.tools,
            "latency_ms": self.latency_ms,
        }


@dataclass
class ScenarioRun:
    """One vendor's attempt at one scenario — measured, or explicitly not."""

    vendor: str
    scenario_id: str
    runnable: bool
    transport: str = "text"
    not_runnable_reason: Optional[str] = None
    error: Optional[str] = None
    turns: list[TurnRecord] = field(default_factory=list)
    #: A ``tau2.health.diagnostics.build`` envelope, or ``None`` when the vendor
    #: was never contacted.
    diagnostics: Optional[dict[str, Any]] = None
    #: False for a vendor whose API returns no tool-call record. Tool criteria then
    #: evaluate to "cannot tell".
    tools_visible: bool = True
    #: What we could not match about this vendor's setup relative to the others.
    #: Surfaces in the report next to the verdict.
    setup_caveats: tuple[str, ...] = ()
    preflight: Optional[Preflight] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None

    @property
    def measured(self) -> bool:
        """A run that produced observations we can score."""
        return self.runnable and self.error is None and bool(self.turns)

    @property
    def flow_section(self) -> Optional[dict[str, Any]]:
        return (self.diagnostics or {}).get("flow")

    @property
    def flow_available(self) -> bool:
        return bool((self.flow_section or {}).get("available"))

    def transcript(self) -> str:
        lines: list[str] = []
        for t in self.turns:
            lines.append(f"user: {t.user}")
            lines.append(f"agent: {t.reply}")
        return "\n".join(lines)

    def all_tool_calls(self) -> list[dict[str, Any]]:
        return [c for t in self.turns for c in t.tools]

    def to_dict(self) -> dict[str, Any]:
        return {
            "vendor": self.vendor,
            "scenario_id": self.scenario_id,
            "transport": self.transport,
            "runnable": self.runnable,
            "measured": self.measured,
            "not_runnable_reason": self.not_runnable_reason,
            "error": self.error,
            "tools_visible": self.tools_visible,
            "setup_caveats": list(self.setup_caveats),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "preflight": self.preflight.to_dict() if self.preflight else None,
            "turns": [t.to_dict() for t in self.turns],
            "diagnostics": self.diagnostics,
        }


def not_runnable(
    vendor: str,
    scenario_id: str,
    reason: str,
    *,
    preflight: Optional[Preflight] = None,
    transport: str = "text",
) -> ScenarioRun:
    """The only way this package represents a vendor it could not drive.

    Note what is NOT here: no score, no partial transcript, no ``0.0``. A caller
    that wants a number from this object gets ``None``."""
    return ScenarioRun(
        vendor=vendor,
        scenario_id=scenario_id,
        runnable=False,
        transport=transport,
        not_runnable_reason=reason,
        preflight=preflight,
    )


@runtime_checkable
class VendorAdapter(Protocol):
    """Drive one vendor through one scenario.

    Implementations must:
      * answer :meth:`preflight` without side effects and without spending money;
      * return :func:`not_runnable` — never a fabricated run — when preflight
        fails;
      * attach a diagnostics envelope to every run they DO produce.
    """

    name: str

    def preflight(self) -> Preflight: ...

    def run_scenario(self, scenario: Any) -> ScenarioRun: ...
