# Copyright Sierra
"""The Whissle adapter — and the reason this package exists at all.

WHY ``/chat/turn`` AND NOT ``/api/bench/agent-turn``
----------------------------------------------------
``POST /api/bench/agent-turn`` is the cheapest way to drive the Whissle brain and
the wrong one here. It is a STATELESS call: it assembles the real system prompt
and calls the LLM, but runs no ``FlowRuntime`` and mints no conversation row, so
there is no flow trace to read afterwards — the fact
``tau2.health.diagnostics.REASON_BENCH_ENDPOINT`` was written to record.

A pass/fail-only comparison is explicitly out of scope for this package. The
deliverable is the *explanation*: which state the flow was in, which transition
fired and with what stated reason, which variable was written and from what
source. That evidence only exists on the stateful path:

    POST /api/agents/{id}/chat/turn      drive a turn (returns per-turn flow block)
    GET  /api/agents/{id}/flow/trace     the accumulated step trace afterwards

So this adapter defaults to that path. ``transport="bench"`` remains available
for a cheap smoke run, and when it is used the diagnostics envelope carries
``REASON_BENCH_ENDPOINT`` on the flow section — the run is still honest, it just
cannot contribute mechanism evidence, and the report will say so rather than
leaving an unexplained blank.

AGENT LIFECYCLE
---------------
Each scenario names an ``agent_type``. By default the adapter creates a
throwaway agent of that type (the backend auto-attaches that type's default
flow), drives it, reads the trace, and deletes it in a ``finally`` — the same
discipline ``tau2.flow.simulate`` uses. Pass an explicit agent id to drive a
long-lived agent instead; the run records which of the two happened, because
"we ran your published agent" and "we ran a fresh default-flow agent" are
different claims.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from tau2.compare import honesty
from tau2.compare.vendors.base import (
    Preflight,
    ScenarioRun,
    TurnRecord,
    not_runnable,
)
from tau2.health import diagnostics as diag

VENDOR = "whissle"

TRANSPORT_CHAT = "chat_turn"
TRANSPORT_BENCH = "bench_agent_turn"

ENDPOINT_CHAT = "POST /api/agents/{id}/chat/turn"
ENDPOINT_BENCH = "POST /api/bench/agent-turn"

REASON_NO_KEY = (
    "not runnable — credentials absent: WHISSLE_API_KEY is not set, so the Whissle "
    "backend was never contacted"
)


class WhissleAdapter:
    """Drives a Whissle agent through a scenario and keeps the trace."""

    name = VENDOR

    def __init__(
        self,
        *,
        base: Optional[str] = None,
        api_key: Optional[str] = None,
        agent_id: Optional[str] = None,
        transport: str = TRANSPORT_CHAT,
        timeout: float = 120.0,
        keep_agent: bool = False,
    ) -> None:
        self.base = (
            base
            or os.getenv("WHISSLE_BASE")
            or "https://aws-gateway-backend.whissle.ai/bot"
        ).rstrip("/")
        self.api_key = api_key or os.getenv("WHISSLE_API_KEY") or ""
        self.agent_id = agent_id or os.getenv("WHISSLE_COMPARE_AGENT_ID") or None
        self.transport = transport
        self.timeout = timeout
        self.keep_agent = keep_agent

    # ── preflight ────────────────────────────────────────────────────────────

    def preflight(self) -> Preflight:
        missing = [] if self.api_key else ["WHISSLE_API_KEY"]
        return Preflight(
            vendor=VENDOR,
            runnable=not missing,
            reason=None if not missing else REASON_NO_KEY,
            missing_env=tuple(missing),
            checked_env=("WHISSLE_API_KEY", "WHISSLE_BASE", "WHISSLE_COMPARE_AGENT_ID"),
            detail={
                "base": self.base,
                "transport": self.transport,
                "agent": (
                    f"explicit agent {self.agent_id}"
                    if self.agent_id
                    else "throwaway agent created per scenario from its agent_type"
                ),
                "trace_available_on_transport": self.transport == TRANSPORT_CHAT,
            },
        )

    # ── driving ──────────────────────────────────────────────────────────────

    def run_scenario(self, scenario: Any) -> ScenarioRun:
        pre = self.preflight()
        if not pre.runnable:
            return not_runnable(VENDOR, scenario.id, pre.reason or REASON_NO_KEY,
                                preflight=pre)
        if self.transport == TRANSPORT_BENCH:
            return self._run_bench(scenario, pre)
        return self._run_chat(scenario, pre)

    def _client(self):
        from tau2.flow.client import FlowClient

        return FlowClient(base=self.base, api_key=self.api_key, timeout=self.timeout)

    def _run_chat(self, scenario: Any, pre: Preflight) -> ScenarioRun:
        from tau2.flow.client import FlowClientError

        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        run = ScenarioRun(
            vendor=VENDOR,
            scenario_id=scenario.id,
            runnable=True,
            transport=TRANSPORT_CHAT,
            preflight=pre,
            started_at=started,
        )
        client = self._client()
        agent_id = self.agent_id
        created = False
        conversation_id: Optional[str] = None
        tool_records: list[dict[str, Any]] = []
        flow_steps: list[dict[str, Any]] = []
        agent_spec: dict[str, Any] = {}

        try:
            if not agent_id:
                spec = client.create_typed_agent(
                    name=f"compare-{scenario.id}-{int(time.time())}",
                    agent_type=scenario.agent_type,
                    system_prompt=scenario.system_prompt,
                )
                agent_id = spec.get("id") or spec.get("agent_id")
                agent_spec = spec
                created = True
                if not agent_id:
                    raise RuntimeError(
                        f"agent creation returned no id: {str(spec)[:200]}"
                    )

            for i, utterance in enumerate(scenario.turns, start=1):
                t0 = time.time()
                result = client.turn(agent_id, utterance, conversation_id)
                latency = (time.time() - t0) * 1000.0
                conversation_id = result.conversation_id or conversation_id
                calls = _normalise_tool_events(result, turn_index=i)
                tool_records.extend(calls)
                if result.flow_present:
                    flow_steps.extend(result.steps)
                run.turns.append(
                    TurnRecord(
                        index=i,
                        user=utterance,
                        reply=result.reply,
                        tools=calls,
                        latency_ms=round(latency, 1),
                        raw=result.raw,
                    )
                )

            flow = self._flow_section(
                client, agent_id, conversation_id, flow_steps, agent_spec
            )
        except FlowClientError as exc:
            run.error = f"FlowClientError: {exc}"
            flow = diag.flow_unavailable(f"the run errored before a trace was read: {exc}")
        except Exception as exc:  # noqa: BLE001 — a failed run reports, never invents
            run.error = f"{type(exc).__name__}: {exc}"
            flow = diag.flow_unavailable(f"the run errored before a trace was read: {exc}")
        finally:
            if created and agent_id and not self.keep_agent:
                try:
                    client.delete_agent(agent_id, confirm=True)
                except Exception:  # noqa: BLE001 — teardown never sinks a run
                    pass

        run.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        run.diagnostics = self._diagnostics(
            scenario, run, flow, tool_records,
            endpoint=ENDPOINT_CHAT, agent_id=agent_id,
            conversation_id=conversation_id,
        )
        run.setup_caveats = (
            (
                "driven over the TEXT channel: it runs the same FlowRuntime the voice "
                "pipeline runs, with no audio, so nothing acoustic is exercised"
            ),
            (
                "a fresh agent of type "
                f"'{scenario.agent_type}' with its default flow"
                if created
                else f"an existing agent ({agent_id}) with whatever flow it carries"
            ),
        )
        return run

    def _run_bench(self, scenario: Any, pre: Preflight) -> ScenarioRun:
        """The stateless brain path. Kept for cheap smoke runs, and stamped with
        :data:`tau2.health.diagnostics.REASON_BENCH_ENDPOINT` so nobody mistakes
        its empty flow section for a flow that did nothing."""
        import requests

        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        run = ScenarioRun(
            vendor=VENDOR,
            scenario_id=scenario.id,
            runnable=True,
            transport=TRANSPORT_BENCH,
            preflight=pre,
            started_at=started,
            tools_visible=False,
        )
        messages: list[dict[str, str]] = []
        try:
            for i, utterance in enumerate(scenario.turns, start=1):
                messages.append({"role": "user", "content": utterance})
                t0 = time.time()
                resp = requests.post(
                    f"{self.base}/api/bench/agent-turn",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "messages": list(messages),
                        "system_prompt": scenario.system_prompt,
                        "agent_type": scenario.agent_type,
                    },
                    timeout=self.timeout,
                )
                latency = (time.time() - t0) * 1000.0
                if resp.status_code >= 300:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                reply = data.get("reply") or data.get("content") or ""
                messages.append({"role": "assistant", "content": reply})
                run.turns.append(
                    TurnRecord(index=i, user=utterance, reply=reply,
                               latency_ms=round(latency, 1), raw=data)
                )
        except Exception as exc:  # noqa: BLE001
            run.error = f"{type(exc).__name__}: {exc}"

        run.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        run.diagnostics = self._diagnostics(
            scenario, run,
            diag.flow_unavailable(diag.REASON_BENCH_ENDPOINT),
            [], endpoint=ENDPOINT_BENCH, agent_id=None, conversation_id=None,
        )
        run.setup_caveats = (
            "driven over the stateless bench endpoint: no flow engine ran, so this "
            "run carries NO mechanism evidence",
        )
        return run

    # ── diagnostics ──────────────────────────────────────────────────────────

    def _flow_section(
        self,
        client: Any,
        agent_id: Optional[str],
        conversation_id: Optional[str],
        per_turn_steps: list[dict[str, Any]],
        agent_spec: dict[str, Any],
    ) -> dict[str, Any]:
        """The accumulated trace, preferring the server's own view.

        The per-turn ``flow`` blocks the chat endpoint returns are the fallback:
        they are the same steps, but a server-side fetch also carries
        ``current_state`` and survives a turn whose response we failed to parse."""
        flow_spec = agent_spec.get("flow") if isinstance(agent_spec, dict) else None
        if not conversation_id:
            return diag.flow_unavailable(diag.REASON_NO_CONVERSATION_ID)
        payload = None
        fetch_error = None
        try:
            payload = client.get_trace(agent_id, conversation_id)
        except Exception as exc:  # noqa: BLE001 — a diagnostics fetch never sinks a run
            fetch_error = f"{type(exc).__name__}: {exc}"
        if payload:
            return diag.flow_from_trace_response(
                payload,
                source="GET /api/agents/{id}/flow/trace",
                conversation_id=conversation_id,
                flow_spec=flow_spec if isinstance(flow_spec, dict) else None,
            )
        if per_turn_steps:
            return diag.flow_section(
                per_turn_steps,
                source="per-turn flow blocks from POST /api/agents/{id}/chat/turn",
                flow_spec=flow_spec if isinstance(flow_spec, dict) else None,
                conversation_id=conversation_id,
            )
        return diag.flow_unavailable(
            f"{diag.REASON_FETCH_FAILED}: {fetch_error}"
            if fetch_error
            else diag.REASON_TRACE_EMPTY
        )

    def _diagnostics(
        self,
        scenario: Any,
        run: ScenarioRun,
        flow: dict[str, Any],
        tool_records: list[dict[str, Any]],
        *,
        endpoint: str,
        agent_id: Optional[str],
        conversation_id: Optional[str],
    ) -> dict[str, Any]:
        """The ``tau2.health.diagnostics`` envelope for this run.

        The ``metadata_sidecar`` section is where the honesty banner becomes
        machine-readable per case: while the metadata head is down, its reason is
        the OUTAGE, not "text mode". Those are different absences and only one of
        them is fixable by switching transport."""
        outage_reason = honesty.metadata_unavailable_reason()
        metadata = diag.metadata_unavailable(outage_reason or diag.REASON_TEXT_MODE)
        return diag.build(
            benchmark="tau2.compare",
            case_id=scenario.id,
            mode=run.transport,
            flow=flow,
            signals=diag.signals_unavailable(diag.REASON_TEXT_MODE),
            metadata_sidecar=metadata,
            tools=diag.tools_section(
                tool_records, source=endpoint,
                writes_reason=(
                    "write integrity for this package is asserted per-scenario by the "
                    "tool_arg_echoed_in_reply criterion, not by a benchmark-wide "
                    "said/emitted/landed block"
                ),
            ),
            provenance=diag.provenance(
                "tau2.compare",
                mode=run.transport,
                transport_endpoint=endpoint,
                agent_id=agent_id,
                base_url=self.base,
                extra={
                    "vendor": VENDOR,
                    "conversation_id": conversation_id,
                    "agent_type": scenario.agent_type,
                    "differentiator_status": honesty.differentiator_status(),
                },
            ),
            cost=diag.cost_section(reason=diag.REASON_NO_JUDGE,
                                   agent_calls=len(run.turns)),
            turns=[t.to_dict() for t in run.turns],
        )


def _normalise_tool_events(result: Any, *, turn_index: int) -> list[dict[str, Any]]:
    """``chat/turn``'s ``tool_events`` (or bare ``tools_used``) → diagnostics rows."""
    rows: list[dict[str, Any]] = []
    for event in result.tool_events or []:
        if not isinstance(event, dict):
            continue
        rows.append(
            diag.tool_call(
                event.get("name") or event.get("tool"),
                arguments=event.get("arguments") or event.get("args") or {},
                result=event.get("result"),
                error=event.get("error"),
                turn=turn_index,
                call_id=event.get("id"),
            )
        )
    if not rows:
        # Some deployments return names only. A name with no arguments is still a
        # fact worth recording — and the missing arguments make argument-shaped
        # criteria resolve to "cannot tell", which is the correct answer.
        for name in result.tools_used or []:
            rows.append(diag.tool_call(str(name), turn=turn_index))
    return rows
