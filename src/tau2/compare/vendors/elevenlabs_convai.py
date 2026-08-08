# Copyright Sierra
"""ElevenLabs Conversational AI as a comparison vendor.

WHY THIS FILE IS NOT UNDER ``tau2.voice``
------------------------------------------
``tau2.voice.scripts.elevenlabs`` and ``tau2.voice.utils.elevenlabs_utils``
already own the name in the synthesis namespace — there ElevenLabs is a TTS
*component we use*. Here it is a competing *agent platform we measure*. Same
vendor, opposite role; collapsing them into one module would eventually let a
synthesis credential silently satisfy a comparison preflight.

THE ONE RULE
------------
This adapter has no fallback path. If ``ELEVENLABS_API_KEY`` or
``ELEVENLABS_AGENT_ID`` is absent, :meth:`preflight` fails and
:meth:`run_scenario` returns a structured not-runnable result. It does not
estimate, simulate, interpolate from published material, or fill in a plausible
transcript. There is no code in this file capable of producing an ElevenLabs
number we did not observe over the wire — deliberately, because the single most
tempting bug in a vendor-comparison harness is a "reasonable default" for the
competitor.

TRANSPORT, AND WHAT IT COSTS US IN MATCHING
--------------------------------------------
ElevenLabs exposes no turn-by-turn text endpoint equivalent to Whissle's
``POST /api/agents/{id}/chat/turn``. The closest documented text surface is

    POST /v1/convai/agents/{agent_id}/simulate-conversation

which drives the agent against ElevenLabs' *own LLM-simulated user*. We instruct
that simulated user to deliver our scripted lines verbatim, in order — but a
model asked to be verbatim is not a script. So every run is checked for
utterance parity afterwards (:func:`_verbatim_parity`), and a run whose user
turns drifted is marked ``utterances_matched: False``. The comparison layer
refuses to call such a pair setup-matched: two systems that heard different
sentences did not run the same scenario, however similar the sentences look.

The endpoint is also marked deprecated upstream in favour of
``/v1/convai/agent-testing/create`` + ``/v1/convai/agents/{id}/run-tests``. That
is recorded as a caveat rather than papered over; when this adapter is first run
for real, the migration is the first thing to check.

ElevenLabs publishes no per-turn state or decision trace, so its diagnostics
envelope carries an honest unavailable flow section. That asymmetry is the
comparison's actual subject, not a defect in this adapter.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any, Optional

from tau2.compare import honesty
from tau2.compare.vendors.base import (
    REASON_CREDENTIALS_ABSENT,
    REASON_VENDOR_NO_TRACE,
    Preflight,
    ScenarioRun,
    TurnRecord,
    not_runnable,
)
from tau2.health import diagnostics as diag

VENDOR = "elevenlabs"

ENV_API_KEY = "ELEVENLABS_API_KEY"
ENV_AGENT_ID = "ELEVENLABS_AGENT_ID"

DEFAULT_API_BASE = "https://api.elevenlabs.io"
SIMULATE_PATH = "/v1/convai/agents/{agent_id}/simulate-conversation"

#: Recorded on every run. These are the things we know we could not match; the
#: report prints them beside the verdict.
SETUP_CAVEATS = (
    "ElevenLabs was driven through its own LLM simulated-user endpoint "
    "(POST /v1/convai/agents/{id}/simulate-conversation) because it exposes no "
    "turn-by-turn text endpoint equivalent to Whissle's /chat/turn — the user "
    "utterances are therefore model-delivered rather than literally scripted",
    "that endpoint is marked deprecated upstream in favour of "
    "/v1/convai/agent-testing/*; the migration has not been exercised",
    "the ElevenLabs agent's prompt, tools, LLM and guardrails are whatever the "
    "configured ELEVENLABS_AGENT_ID carries — we did not author them, so a "
    "difference in outcome may be a difference in agent configuration rather than "
    "in platform capability",
    REASON_VENDOR_NO_TRACE,
)

SIMULATED_USER_PROMPT = """\
You are role-playing a caller in a scripted evaluation. You have a fixed script.

Deliver the following lines VERBATIM, one per turn, in order. Do not paraphrase,
do not merge lines, do not add pleasantries, do not skip a line, and do not
invent a line. After your final line, stop.

SCRIPT:
{script}
"""


class ElevenLabsConvAIAdapter:
    """Drive an ElevenLabs Conversational AI agent, or refuse to."""

    name = VENDOR

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        agent_id: Optional[str] = None,
        api_base: Optional[str] = None,
        timeout: float = 180.0,
    ) -> None:
        self.api_key = api_key or os.getenv(ENV_API_KEY) or ""
        self.agent_id = agent_id or os.getenv(ENV_AGENT_ID) or ""
        self.api_base = (api_base or os.getenv("ELEVENLABS_API_BASE")
                         or DEFAULT_API_BASE).rstrip("/")
        self.timeout = timeout

    # ── preflight ────────────────────────────────────────────────────────────

    def preflight(self) -> Preflight:
        """Environment-only. Costs nothing, contacts nobody, signs up for nothing."""
        missing = [
            name
            for name, value in ((ENV_API_KEY, self.api_key),
                                (ENV_AGENT_ID, self.agent_id))
            if not value
        ]
        runnable = not missing
        return Preflight(
            vendor=VENDOR,
            runnable=runnable,
            reason=None if runnable else _credentials_reason(missing),
            missing_env=tuple(missing),
            checked_env=(ENV_API_KEY, ENV_AGENT_ID),
            detail={
                "api_base": self.api_base,
                "transport": "POST " + SIMULATE_PATH,
                "trace_available": False,
                "trace_note": REASON_VENDOR_NO_TRACE,
                "no_estimate_path": (
                    "this adapter has no fallback that produces a number without "
                    "contacting the vendor"
                ),
            },
        )

    # ── driving ──────────────────────────────────────────────────────────────

    def run_scenario(self, scenario: Any) -> ScenarioRun:
        pre = self.preflight()
        if not pre.runnable:
            return not_runnable(
                VENDOR, scenario.id, pre.reason or REASON_CREDENTIALS_ABSENT,
                preflight=pre,
            )

        import requests

        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        run = ScenarioRun(
            vendor=VENDOR,
            scenario_id=scenario.id,
            runnable=True,
            transport="simulate_conversation",
            preflight=pre,
            started_at=started,
            tools_visible=True,
            setup_caveats=SETUP_CAVEATS,
        )

        payload: dict[str, Any] = {}
        try:
            body = self._request_body(scenario)
            t0 = time.time()
            resp = requests.post(
                self.api_base + SIMULATE_PATH.format(agent_id=self.agent_id),
                headers={"xi-api-key": self.api_key,
                         "Content-Type": "application/json"},
                json=body,
                timeout=self.timeout,
            )
            elapsed = (time.time() - t0) * 1000.0
            if resp.status_code >= 300:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            payload = resp.json()
            run.turns = _turns_from_simulation(payload, elapsed_ms=elapsed)
        except Exception as exc:  # noqa: BLE001 — a failed run reports, never invents
            run.error = f"{type(exc).__name__}: {exc}"

        parity = _verbatim_parity(scenario.turns, run.turns)
        run.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        run.setup_caveats = SETUP_CAVEATS + tuple(parity["caveats"])
        run.diagnostics = self._diagnostics(scenario, run, payload, parity)
        return run

    def _request_body(self, scenario: Any) -> dict[str, Any]:
        script = "\n".join(f"{i}. {line}" for i, line in enumerate(scenario.turns, 1))
        return {
            "simulation_specification": {
                "simulated_user_config": {
                    "first_message": scenario.turns[0] if scenario.turns else None,
                    "language": "en",
                    "prompt": {
                        "prompt": SIMULATED_USER_PROMPT.format(script=script),
                        # Deterministic delivery is the whole point; a sampled user
                        # simulator would make the two vendors hear different calls.
                        "temperature": 0,
                    },
                },
            },
        }

    def _diagnostics(
        self,
        scenario: Any,
        run: ScenarioRun,
        payload: dict[str, Any],
        parity: dict[str, Any],
    ) -> dict[str, Any]:
        tool_rows = [
            diag.tool_call(
                c.get("name") or c.get("tool_name"),
                arguments=c.get("params_as_json") or c.get("arguments") or {},
                result=c.get("result"),
                turn=c.get("_turn"),
                call_id=c.get("tool_call_id") or c.get("id"),
            )
            for c in _tool_calls_from_simulation(payload)
        ]
        endpoint = "POST " + SIMULATE_PATH
        return diag.build(
            benchmark="tau2.compare",
            case_id=scenario.id,
            mode=run.transport,
            # The comparison's actual subject: there is nothing to read.
            flow=diag.flow_unavailable(REASON_VENDOR_NO_TRACE),
            signals=diag.signals_unavailable(REASON_VENDOR_NO_TRACE),
            metadata_sidecar=diag.metadata_unavailable(REASON_VENDOR_NO_TRACE),
            tools=diag.tools_section(tool_rows, source=endpoint),
            provenance=diag.provenance(
                "tau2.compare",
                mode=run.transport,
                transport_endpoint=endpoint,
                agent_id=self.agent_id,
                base_url=self.api_base,
                extra={
                    "vendor": VENDOR,
                    "utterances_matched": parity["matched"],
                    "utterance_parity": parity,
                    "differentiator_status": honesty.differentiator_status(),
                },
            ),
            cost=diag.cost_section(
                reason=(
                    "spend on the vendor's side is billed to the vendor account and "
                    "is not visible to this harness"
                ),
                agent_calls=1,
            ),
            turns=[t.to_dict() for t in run.turns],
            extra={"vendor_raw_analysis": (payload or {}).get("analysis")},
        )


# ── helpers ─────────────────────────────────────────────────────────────────────


def _credentials_reason(missing: list[str]) -> str:
    return (
        f"{REASON_CREDENTIALS_ABSENT} Missing: {', '.join(missing)}. "
        f"Set {ENV_API_KEY} and {ENV_AGENT_ID} (a Conversational AI agent id from "
        "the ElevenLabs dashboard) to make this vendor runnable."
    )


def _turns_from_simulation(
    payload: dict[str, Any], *, elapsed_ms: Optional[float] = None,
) -> list[TurnRecord]:
    """Fold ElevenLabs' flat ``simulated_conversation`` into user/agent pairs.

    The endpoint returns one call, not one response per turn, so per-turn latency
    is not observable; ``latency_ms`` is carried only on the first record as the
    whole-call elapsed time and is None elsewhere rather than being divided into
    a fake per-turn figure."""
    items = payload.get("simulated_conversation")
    if not isinstance(items, list):
        return []
    turns: list[TurnRecord] = []
    pending_user: Optional[str] = None
    pending_tools: list[dict[str, Any]] = []
    index = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        message = item.get("message") or ""
        if role == "user":
            if pending_user is not None:
                index += 1
                turns.append(TurnRecord(index=index, user=pending_user, reply="",
                                        tools=[], raw=item))
            pending_user = message
            pending_tools = []
        elif role in ("agent", "assistant"):
            calls = item.get("tool_calls") or []
            pending_tools.extend(c for c in calls if isinstance(c, dict))
            index += 1
            turns.append(
                TurnRecord(
                    index=index,
                    user=pending_user or "",
                    reply=message,
                    tools=[
                        diag.tool_call(
                            c.get("tool_name") or c.get("name"),
                            arguments=c.get("params_as_json") or c.get("arguments"),
                            result=c.get("result"),
                            turn=index,
                        )
                        for c in pending_tools
                    ],
                    latency_ms=elapsed_ms if index == 1 else None,
                    raw=item,
                )
            )
            pending_user = None
            pending_tools = []
    if pending_user is not None:
        index += 1
        turns.append(TurnRecord(index=index, user=pending_user, reply="", raw={}))
    return turns


def _tool_calls_from_simulation(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    items = payload.get("simulated_conversation")
    if not isinstance(items, list):
        return rows
    turn = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("role") in ("agent", "assistant"):
            turn += 1
            for call in item.get("tool_calls") or []:
                if isinstance(call, dict):
                    rows.append({**call, "_turn": turn})
    return rows


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def _verbatim_parity(
    scripted: tuple[str, ...] | list[str], observed: list[TurnRecord],
) -> dict[str, Any]:
    """Did the vendor's simulated user actually say our lines?

    Without this, a drifted paraphrase silently turns a "comparison" into two
    different conversations that happen to share a title. A run that fails parity
    is still reported — it is simply not eligible to be called setup-matched."""
    said = [t.user for t in observed if (t.user or "").strip()]
    scripted = list(scripted)
    mismatches: list[dict[str, Any]] = []
    for i, expected in enumerate(scripted):
        actual = said[i] if i < len(said) else None
        if actual is None or _norm(actual) != _norm(expected):
            mismatches.append({"index": i + 1, "expected": expected,
                               "actual": actual})
    matched = not mismatches and len(said) == len(scripted)
    caveats: list[str] = []
    if not observed:
        matched = False
        caveats.append(
            "no turns were observed, so utterance parity with the Whissle run "
            "could not be established"
        )
    elif not matched:
        caveats.append(
            f"the vendor's simulated user did NOT deliver the script verbatim "
            f"({len(mismatches)} of {len(scripted)} line(s) drifted) — the two "
            "systems did not hear the same call, so this pair is not setup-matched"
        )
    return {
        "matched": matched,
        "n_scripted": len(scripted),
        "n_observed": len(said),
        "mismatches": mismatches,
        "caveats": caveats,
    }
