# Copyright Sierra
"""The seam that records what actually served each turn.

WHY A LEDGER AND NOT A RETURN VALUE
-----------------------------------
The evidence we need — ``model``, ``usage``, ``stop_details`` off every
``/api/bench/agent-turn`` response — is produced deep inside each suite's turn-taker
(``WhissleBrain.turn``, the AgentClinic doctor, the PatientAgentBench client) and is
thrown away there, because those functions exist to return a string of text. Threading
a usage object back out through every caller would mean touching every layer in
between, in four suites, for a field none of them care about.

So the turn-taker records into a ledger instead: one line at the call site, no
signature changes, no plumbing. The runner hands the ledger to the archive writer at
the end. A suite that has not been wired yet simply produces an empty ledger, and the
manifest says the served model is :data:`~tau2.archive.schema.NOT_RECORDED` — which is
true, and visibly different from a suite that recorded a single model.

The ledger is deliberately dumb: it appends, it never fetches, it never raises. It is
called once per model turn in the hot path of a benchmark that may run for an hour,
and the one thing it must never do is fail.
"""
from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Optional

from .cost import Usage, merge
from .schema import NOT_RECORDED, ServedRecord

#: Where a ledger entry came from, for the manifest's ``source`` field.
SOURCE_BENCH_TURN = "POST /api/bench/agent-turn response body (model/usage/stop_details)"
SOURCE_MODELS_CHAT = "POST /api/models/chat response body (usage/cost_usd)"
SOURCE_ARTIFACT = "read back from an archived artifact"


@dataclass
class Turn:
    """One model call. ``case_id`` is what makes per-case cost possible — without it
    a run has a total and no way to find the case that produced it."""

    model: str = NOT_RECORDED
    usage: Usage = field(default_factory=Usage)
    stop_reason: Optional[str] = None
    stop_details: Optional[dict[str, Any]] = None
    case_id: Optional[str] = None
    #: Set when the caller asked for a specific model, so a mismatch is visible.
    requested_model: Optional[str] = None
    role: str = "agent"
    latency_ms: Optional[float] = None
    cost_usd: Optional[float] = None

    @property
    def failover(self) -> bool:
        """Did the backend serve a different model than the one asked for?"""
        if not self.requested_model or self.model == NOT_RECORDED:
            return False
        return not str(self.model).startswith(str(self.requested_model))

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "role": self.role,
            "requested_model": self.requested_model,
            "served_model": self.model,
            "failover": self.failover,
            "stop_reason": self.stop_reason,
            "stop_details": self.stop_details,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
            "usage": self.usage.to_dict(),
        }


class ServingLedger:
    """Thread-safe append-only record of every model call in a run.

    Usage at a call site is one line::

        resp = r.json()
        ledger.record_response(resp, case_id=case_id, requested_model=self.model)
        return _extract_text(resp)

    Nothing about the surrounding function changes.
    """

    def __init__(self, *, source: str = SOURCE_BENCH_TURN) -> None:
        self.source = source
        self._turns: list[Turn] = []
        self._lock = threading.Lock()
        #: Dollar figures the harness already knows (judge/simulator calls billed
        #: through /api/models/chat), keyed by role.
        self._extra_usd: dict[str, float] = {}

    # -- recording ---------------------------------------------------------

    def record_response(
        self,
        payload: Any,
        *,
        case_id: Optional[str] = None,
        requested_model: Optional[str] = None,
        role: str = "agent",
        latency_ms: Optional[float] = None,
    ) -> None:
        """Record one ``/api/bench/agent-turn`` response.

        Never raises. A response with no ``model`` key (an older backend) still
        records a turn, marked :data:`~tau2.archive.schema.NOT_RECORDED` — the count
        of turns is itself information, and losing it would make an un-instrumented
        run look like a run with no turns.
        """
        try:
            body = payload if isinstance(payload, dict) else {}
            turn = Turn(
                model=str(body.get("model") or NOT_RECORDED),
                usage=Usage.from_payload(body.get("usage")),
                stop_reason=body.get("stop_reason"),
                stop_details=body.get("stop_details")
                if isinstance(body.get("stop_details"), dict) else None,
                case_id=case_id,
                requested_model=requested_model,
                role=role,
                latency_ms=latency_ms,
            )
            from .cost import usd

            turn.cost_usd = usd(turn.usage, turn.model)
            with self._lock:
                self._turns.append(turn)
        except Exception:  # noqa: BLE001 — telemetry never sinks a benchmark
            pass

    def record_usd(self, role: str, amount: float) -> None:
        """Record spend the caller already knows in dollars (``/api/models/chat``
        returns ``cost_usd`` directly, so there is nothing to price)."""
        try:
            with self._lock:
                self._extra_usd[role] = self._extra_usd.get(role, 0.0) + float(amount)
        except Exception:  # noqa: BLE001
            pass

    # -- reading -----------------------------------------------------------

    @property
    def turns(self) -> list[Turn]:
        with self._lock:
            return list(self._turns)

    def __len__(self) -> int:
        with self._lock:
            return len(self._turns)

    def served(self) -> ServedRecord:
        """The manifest's ``served`` block."""
        turns = self.turns
        agent = [t for t in turns if t.role == "agent"] or turns
        models = Counter(t.model for t in agent)
        stops = Counter(t.stop_reason for t in agent if t.stop_reason)
        details = [
            {"case_id": t.case_id, **(t.stop_details or {})}
            for t in agent if t.stop_details
        ]
        return ServedRecord(
            models=dict(models),
            stop_reasons=dict(stops),
            stop_details=details,
            turns=len(agent),
            source=self.source if agent else NOT_RECORDED,
        )

    def usage_by_model(self) -> dict[str, Usage]:
        return merge((t.model, t.usage) for t in self.turns if t.role == "agent")

    def extra_usd(self) -> dict[str, float]:
        with self._lock:
            return dict(self._extra_usd)

    def by_case(self) -> dict[str, dict[str, Any]]:
        """Per-case tokens and cost — what makes "which cases were expensive"
        answerable. Turns recorded without a ``case_id`` are grouped under
        ``"(uncased)"`` rather than dropped, so the per-case totals still sum to the
        run total."""
        buckets: dict[str, list[Turn]] = {}
        for turn in self.turns:
            buckets.setdefault(turn.case_id or "(uncased)", []).append(turn)

        out: dict[str, dict[str, Any]] = {}
        for case_id, turns in sorted(buckets.items()):
            usage = Usage()
            for t in turns:
                usage = usage + t.usage
            costs = [t.cost_usd for t in turns if t.cost_usd is not None]
            out[case_id] = {
                "turns": len(turns),
                "served_models": dict(Counter(t.model for t in turns)),
                "usage": usage.to_dict(),
                "cost_usd": round(sum(costs), 6) if costs else None,
                "unpriced_turns": sum(1 for t in turns if t.cost_usd is None),
            }
        return out

    def failovers(self) -> list[dict[str, Any]]:
        """Turns the backend served with a model other than the one requested. An
        empty list here alongside a populated ledger is a real finding: it means the
        arm label is trustworthy."""
        return [t.to_dict() for t in self.turns if t.failover]

    def to_dict(self) -> dict[str, Any]:
        turns = self.turns
        return {
            "source": self.source,
            "n_turns": len(turns),
            "served": self.served().to_dict(),
            "failovers": self.failovers(),
            "by_case": self.by_case(),
            "turns": [t.to_dict() for t in turns],
        }


# ── the process-wide default ledger ────────────────────────────────────────────
#
# Most suites can hand a ledger to their turn-taker directly. PatientAgentBench
# cannot: its client is constructed lazily inside a pydantic model inside a LangGraph
# agent, several layers below anything the runner holds, and threading an argument
# down would mean editing every layer in between to carry a field none of them use.
#
# So there is one ledger per process, and a client with no explicit ledger records
# into it. This is a deliberate use of global state, bounded by three properties: it
# is append-only, it is thread-safe, and nothing reads it except the archive writer
# at end of run. The risk it carries — two runs in one process sharing a ledger — is
# handled by :func:`reset_default`, which the runner calls before it starts.

_default: Optional[ServingLedger] = None
_default_lock = threading.Lock()


def default_ledger() -> ServingLedger:
    """The process-wide ledger. Created on first use."""
    global _default
    with _default_lock:
        if _default is None:
            _default = ServingLedger()
        return _default


def reset_default() -> ServingLedger:
    """Start a fresh process-wide ledger and return it.

    Called by a runner at the start of a run so a second run in the same process
    cannot inherit the first one's turns — which would silently double a cost figure
    and mix two arms' served models into one histogram.
    """
    global _default
    with _default_lock:
        _default = ServingLedger()
        return _default


def from_records(
    records: list[dict[str, Any]],
    *,
    model_key: str = "model",
    usage_key: str = "usage",
    case_key: str = "case_id",
    source: str = SOURCE_ARTIFACT,
) -> ServingLedger:
    """Rebuild a ledger from archived per-case records.

    Used by the backfill: historical runs have no live ledger, but a few of them
    persisted the response fields onto their case records, and a recovered ledger is
    strictly better than a ``not recorded``.
    """
    ledger = ServingLedger(source=source)
    for record in records:
        if not isinstance(record, dict):
            continue
        payload = {
            "model": record.get(model_key),
            "usage": record.get(usage_key),
            "stop_reason": record.get("stop_reason"),
            "stop_details": record.get("stop_details"),
        }
        if payload["model"] or payload["usage"]:
            ledger.record_response(payload, case_id=str(record.get(case_key) or "") or None)
    return ledger
