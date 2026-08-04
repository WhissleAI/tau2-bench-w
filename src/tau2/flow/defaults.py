# Copyright Sierra
"""Default-flow COVERAGE suite — every seeded agent TYPE ships a driving flow.

Sibling of ``flow/benchmark.py``. Where that suite AUTHORS a flow onto a
``text_assistant`` and asserts the state machine runs, this suite supplies **no
flow at all**: it creates one throwaway agent per seeded ``agent_type`` and proves
the backend auto-attached that type's default
``prompts/agent_types/<type>/flow.json`` on creation, then that the attached flow
actually **drives** the conversation.

For each of the 15 seeded types it asserts, against the LIVE backend:

  attach (from ``GET /api/agents/{id}``)
    * ``flow`` is present (a dict)                     — the loader auto-attached it
    * ``flow.enabled == true``                          — attached flow-ENABLED
    * ``flow.states`` is non-empty                      — a real machine, not a stub
    * ``flow.start_state`` names a real state           — the entry point resolves

  drive (from ``POST /api/agents/{id}/chat/turn`` × 3-4 turns)
    * turn-1 ``flow.active == true``                    — the engine took the wheel
    * the flow ENTERS its ``start_state`` first         — "current_state == start
      initially": the first ``state_enter`` in the trace is the start state (a
      ``say``-start advances past it via an ``always`` edge on the same turn, so we
      assert the first ENTRY, not the post-turn ``current_state``)
    * the step trace has ≥1 ``transition_check``        — transitions are evaluated
    * say-start types: the start ``say`` text (read back from the attached flow)
      appears VERBATIM in the turn-1 reply

  gate (debt_collection only)
    * no balance / amount / debt is disclosed on an UNVERIFIED turn, and the flow
      stays in its pre-disclosure states (never enters disclose_balance/pay_now/
      promise_to_pay) — the identity-verification compliance gate holds

Design mirrors ``benchmark.py``: a standalone Typer app driving the SAME product
surface a customer hits, reading WHISSLE_BASE + WHISSLE_API_KEY from .env, cleaning
up EVERY throwaway agent in every exit path plus an end-of-run ``flowcov-*`` sweep.
Base deps only (requests/typer/rich/dotenv).

Unlike ``benchmark.py`` there is no ``skipped-pending-trace`` tier: the whole point
is that the flow (and its step trace) IS present. A missing flow after retries is a
real FAILURE — either the flow.json did not package or the loader did not attach.

Usage:
    python -m tau2.flow.defaults list                       # the 15 types
    python -m tau2.flow.defaults run                        # all types
    python -m tau2.flow.defaults run --agent-type debt_collection  # one type
    python -m tau2.flow.defaults run --keep-agent           # skip teardown (debug)
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from typer import Option, Typer

from tau2.flow.client import FlowClient, FlowClientError, TurnResult
from tau2.flow.scenarios import Assertion

app = Typer(add_completion=False)
console = Console()

RESULTS_DIR = Path("results/whissle/flow_defaults")
FIXTURE = Path("data/flow/defaults.json")

# Retry the GET-for-flow a few times: the auto-attach can trail agent creation by a
# beat, and a rolling deploy may take a moment to serve the new field.
ATTACH_RETRIES = 6
ATTACH_BACKOFF_S = 5.0


# ── fixture model ─────────────────────────────────────────────────────────────

@dataclass
class TypeSpec:
    agent_type: str
    turns: list[str]
    gate: Optional[dict[str, Any]] = None

    @property
    def agent_name(self) -> str:
        return f"flowcov-{self.agent_type}"


@dataclass
class DefaultsFixture:
    system_prompt: str
    types: list[TypeSpec] = field(default_factory=list)

    @staticmethod
    def load() -> "DefaultsFixture":
        d = json.loads(FIXTURE.read_text(encoding="utf-8"))
        return DefaultsFixture(
            system_prompt=d.get("system_prompt", "You are the agent under test."),
            types=[
                TypeSpec(agent_type=t["agent_type"], turns=t["turns"],
                         gate=t.get("gate"))
                for t in d["types"]
            ],
        )


def all_types() -> list[str]:
    return [t.agent_type for t in DefaultsFixture.load().types]


# ── trace helpers (same event kinds as scenarios.py) ──────────────────────────

def _enters(steps: list[dict]) -> list[str]:
    return [s.get("state") for s in steps if s.get("kind") == "state_enter"]


def _has_transition_check(steps: list[dict]) -> bool:
    return any(s.get("kind") == "transition_check" for s in steps)


def _state_by_id(flow: dict, sid: Optional[str]) -> dict:
    for s in flow.get("states") or []:
        if s.get("id") == sid:
            return s
    return {}


# ── grading ───────────────────────────────────────────────────────────────────

def grade_attach(agent: dict[str, Any]) -> list[Assertion]:
    """Assert the type's default flow was auto-attached, flow-enabled, and valid.
    All from the ``GET /api/agents/{id}`` body."""
    out: list[Assertion] = []
    flow = agent.get("flow")

    present = isinstance(flow, dict)
    out.append(Assertion(
        "attach.flow_present", "attach", "pass" if present else "fail",
        "" if present else f"flow absent on GET (value={flow!r}) — default flow did "
        "NOT auto-attach (flow.json not packaged, or loader did not attach)",
    ))
    if not present:
        return out  # nothing else is meaningful without a flow

    enabled = flow.get("enabled") is True
    out.append(Assertion(
        "attach.flow_enabled", "attach", "pass" if enabled else "fail",
        "" if enabled else f"flow.enabled={flow.get('enabled')!r} (expected True)",
    ))

    states = flow.get("states")
    non_empty = isinstance(states, list) and len(states) > 0
    out.append(Assertion(
        "attach.states_non_empty", "attach", "pass" if non_empty else "fail",
        "" if non_empty else f"flow.states={states!r}",
    ))

    start = flow.get("start_state")
    ids = {s.get("id") for s in (states or [])} if isinstance(states, list) else set()
    real = bool(start) and start in ids
    out.append(Assertion(
        "attach.start_state_real", "attach", "pass" if real else "fail",
        "" if real else f"start_state={start!r} not among state ids={sorted(ids)}",
    ))
    return out


def grade_drive(
    flow: dict[str, Any], turns: list[TurnResult],
) -> list[Assertion]:
    """Assert the attached flow actually drove the conversation, from the turn
    responses' ``flow`` step-trace + replies."""
    out: list[Assertion] = []
    start = flow.get("start_state")

    if not turns:
        out.append(Assertion("drive.has_turns", "drive", "fail", "no turns driven"))
        return out

    t1 = turns[0]

    # turn-1 flow.active
    active = bool(t1.flow.get("active")) if t1.flow_present else False
    out.append(Assertion(
        "drive.turn1_flow_active", "drive", "pass" if active else "fail",
        "" if active else (
            "turn-1 response carried no active flow "
            f"(flow_present={t1.flow_present}, flow={t1.flow!r})"
        ),
    ))

    # The flow ENTERS its start_state first (current_state == start "initially").
    # Accumulate steps across turns so a first entry that landed on turn 1 counts
    # even if later turns advance the machine.
    all_steps: list[dict] = []
    for r in turns:
        all_steps.extend(r.steps)
    enters = _enters(all_steps)
    first_ok = bool(enters) and enters[0] == start
    out.append(Assertion(
        "drive.enters_start_state_first", "drive", "pass" if first_ok else "fail",
        "" if first_ok else f"start_state={start!r} first state_enter="
        f"{enters[0] if enters else None!r} enters={enters}",
    ))

    # A state_enter for the start state exists somewhere in the trace.
    entered_start = start in enters
    out.append(Assertion(
        "drive.state_enter_start", "drive", "pass" if entered_start else "fail",
        "" if entered_start else f"no state_enter for start_state={start!r}; "
        f"enters={enters}",
    ))

    # At least one transition was evaluated.
    tc = _has_transition_check(all_steps)
    out.append(Assertion(
        "drive.transition_check_present", "drive", "pass" if tc else "fail",
        "" if tc else "no transition_check event in the step trace",
    ))

    # say-start types: the start say text appears verbatim in turn-1's reply.
    s0 = _state_by_id(flow, start)
    if s0.get("type") == "say":
        say = (s0.get("say") or "").strip()
        hit = bool(say) and say in (t1.reply or "")
        out.append(Assertion(
            "drive.start_say_verbatim", "drive", "pass" if hit else "fail",
            "" if hit else f"start say not verbatim in turn-1 reply. "
            f"say={say[:80]!r} reply={t1.reply[:160]!r}",
        ))
    return out


def grade_gate(
    flow: dict[str, Any], turns: list[TurnResult], gate: dict[str, Any],
) -> list[Assertion]:
    """debt_collection: on UNVERIFIED turns, assert no balance/amount is disclosed
    and the flow never enters a disclosure state."""
    out: list[Assertion] = []
    forbidden = [s.lower() for s in gate.get("forbidden_substrings_lower", [])]
    disclosure_states = set(gate.get("disclosure_states", []))
    pre_states = set(gate.get("pre_disclosure_states", []))

    # No forbidden disclosure substring in ANY reply across the unverified turns.
    transcript = "\n".join(r.reply or "" for r in turns).lower()
    leaked = [w for w in forbidden if w in transcript]
    out.append(Assertion(
        "gate.no_balance_disclosed", "gate", "pass" if not leaked else "fail",
        "" if not leaked else f"disclosed before verification — reply contained "
        f"{leaked}; transcript={transcript[:200]!r}",
    ))

    # The flow never entered a disclosure state on the unverified path.
    all_steps: list[dict] = []
    for r in turns:
        all_steps.extend(r.steps)
    entered = _enters(all_steps)
    reached = [s for s in entered if s in disclosure_states]
    out.append(Assertion(
        "gate.no_disclosure_state_entered", "gate", "pass" if not reached else "fail",
        "" if not reached else f"flow entered disclosure state(s) {reached} without "
        f"verification; enters={entered}",
    ))

    # And the states it DID visit are all pre-disclosure (defensive: catches a
    # rename/added pre-state without failing, but flags a jump past the gate).
    if entered:
        stray = [s for s in entered if s not in pre_states and s not in disclosure_states]
        # stray states are allowed (e.g. an added pre-state); only report for the log.
        detail = f"visited={entered} (stray non-classified: {stray})" if stray else \
            f"visited={entered}"
        out.append(Assertion(
            "gate.stayed_pre_disclosure", "gate",
            "pass" if not reached else "fail", detail,
        ))
    return out


# ── one type ──────────────────────────────────────────────────────────────────

def run_type(
    client: FlowClient, spec: TypeSpec, system_prompt: str, *,
    keep_agent: bool = False,
) -> dict[str, Any]:
    """Create a typed throwaway agent (NO flow), assert auto-attach + driving,
    ALWAYS clean up. Returns a JSON-serializable result dict."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RESULTS_DIR / f"{spec.agent_type}_{ts}.jsonl"
    log = log_path.open("w", encoding="utf-8")

    def _emit(rec: dict[str, Any]) -> None:
        log.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        log.flush()

    agent_id: Optional[str] = None
    agent_flow: dict[str, Any] = {}
    turns: list[TurnResult] = []
    setup_error: Optional[str] = None
    conv_id: Optional[str] = None
    attach_attempts = 0

    _emit({"event": "type_start", "agent_type": spec.agent_type, "ts": ts})

    try:
        created = client.create_typed_agent(
            spec.agent_name, spec.agent_type, system_prompt)
        agent_id = created["id"]
        _emit({"event": "agent_created", "agent_id": agent_id,
               "agent_type": spec.agent_type, "name": created.get("name")})

        # GET the agent and retry until the auto-attached flow appears (the loader
        # can trail creation; a rolling deploy may take a beat to serve the field).
        agent = created
        for attempt in range(1, ATTACH_RETRIES + 1):
            attach_attempts = attempt
            agent = client.get_agent(agent_id)
            if isinstance(agent.get("flow"), dict):
                break
            _emit({"event": "attach_retry", "attempt": attempt,
                   "flow": agent.get("flow")})
            if attempt < ATTACH_RETRIES:
                time.sleep(ATTACH_BACKOFF_S)
        agent_flow = agent.get("flow") if isinstance(agent.get("flow"), dict) else {}
        _emit({"event": "agent_fetched", "attach_attempts": attach_attempts,
               "flow_present": bool(agent_flow),
               "start_state": agent_flow.get("start_state"),
               "enabled": agent_flow.get("enabled"),
               "num_states": len(agent_flow.get("states") or [])})

        # Drive the scripted turns on one thread.
        for i, user in enumerate(spec.turns, start=1):
            res = client.turn(agent_id, user, conversation_id=conv_id)
            conv_id = res.conversation_id or conv_id
            turns.append(res)
            _emit({
                "event": "turn", "n": i, "user": user, "reply": res.reply,
                "tools_used": res.tools_used, "flow_present": res.flow_present,
                "current_state": res.current_state, "steps": res.steps,
            })

    except FlowClientError as e:
        setup_error = str(e)
        _emit({"event": "error", "phase": "setup/drive", "detail": setup_error})
    except Exception as e:  # noqa: BLE001 — record, still tear down below
        setup_error = f"{type(e).__name__}: {e}"
        _emit({"event": "error", "phase": "setup/drive", "detail": setup_error})

    # Grade.
    assertions: list[Assertion] = []
    if setup_error:
        assertions.append(Assertion("setup", "attach", "fail", setup_error))
    else:
        assertions += grade_attach({"flow": agent_flow} if agent_flow else {"flow": None})
        if agent_flow:
            assertions += grade_drive(agent_flow, turns)
            if spec.gate:
                assertions += grade_gate(agent_flow, turns, spec.gate)

    for a in assertions:
        _emit({"event": "assertion", "name": a.name, "tier": a.tier,
               "status": a.status, "detail": a.detail})

    # ── teardown — NEVER leave a throwaway agent behind ──────────────────────
    deleted = False
    if agent_id and not keep_agent:
        try:
            client.delete_agent(agent_id)
            deleted = True
            _emit({"event": "agent_deleted", "agent_id": agent_id})
        except Exception as e:  # noqa: BLE001
            _emit({"event": "agent_delete_failed", "agent_id": agent_id,
                   "detail": str(e)})

    failed = [a for a in assertions if a.status == "fail"]
    passed = [a for a in assertions if a.status == "pass"]
    attached = bool(agent_flow)
    driving = attached and not any(
        a.status == "fail" and a.tier == "drive" for a in assertions)
    result = {
        "agent_type": spec.agent_type,
        "ts": ts,
        "agent_id": agent_id,
        "attach_attempts": attach_attempts,
        "attached": attached,
        "driving": driving,
        "start_state": agent_flow.get("start_state"),
        "start_kind": _state_by_id(agent_flow, agent_flow.get("start_state")).get("type"),
        "agent_deleted": deleted or keep_agent,
        "counts": {"pass": len(passed), "fail": len(failed),
                   "total": len(assertions)},
        "passed": (not failed and not setup_error),
        "assertions": [a.__dict__ for a in assertions],
        "turns": [
            {"user": u, "reply": r.reply, "current_state": r.current_state,
             "flow_present": r.flow_present}
            for u, r in zip(spec.turns, turns)
        ],
        "log": str(log_path),
    }
    _emit({"event": "type_result", **result["counts"],
           "attached": attached, "driving": driving, "passed": result["passed"]})
    log.close()

    (RESULTS_DIR / f"{spec.agent_type}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_type_markdown(spec, result, assertions)
    return result


def _write_type_markdown(
    spec: TypeSpec, result: dict, assertions: list[Assertion],
) -> None:
    lines = [
        f"# Default-flow coverage: `{spec.agent_type}`",
        "",
        f"- **agent**: `{result.get('agent_id')}` (deleted: {result['agent_deleted']})",
        f"- **attached**: {result['attached']}  •  **driving**: {result['driving']}",
        f"- **start_state**: `{result.get('start_state')}` ({result.get('start_kind')})",
        f"- **attach attempts**: {result['attach_attempts']}",
        f"- **result**: {'PASS' if result['passed'] else 'FAIL'} — "
        f"{result['counts']['pass']} pass / {result['counts']['fail']} fail",
        "",
        "## Turns",
        "",
    ]
    for i, t in enumerate(result["turns"], start=1):
        lines += [f"**Turn {i}** — user: `{t['user']}`  (current_state: "
                  f"`{t['current_state']}`)",
                  f"> {t['reply']}", ""]
    lines += ["## Assertions", ""]
    glyph = {"pass": "PASS", "fail": "FAIL"}
    for a in assertions:
        lines.append(f"- [{glyph.get(a.status, a.status)}] ({a.tier}) `{a.name}`"
                     + (f" — {a.detail}" if a.detail and a.status != "pass" else ""))
    (RESULTS_DIR / f"{spec.agent_type}.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


# ── CLI ───────────────────────────────────────────────────────────────────────

@app.command("list")
def list_types() -> None:
    """List the seeded types this suite covers."""
    fx = DefaultsFixture.load()
    for t in fx.types:
        gate = "  (gate: identity-before-disclosure)" if t.gate else ""
        console.print(f"[bold]{t.agent_type}[/bold]  turns={len(t.turns)}{gate}")


@app.command()
def run(
    agent_type: Optional[str] = Option(
        None, help="one seeded type (e.g. debt_collection); omit to run all"),
    keep_agent: bool = Option(
        False, help="do NOT delete the throwaway agents (debugging only)"),
) -> None:
    """Run default-flow coverage against the live backend; print a summary table."""
    fx = DefaultsFixture.load()
    client = FlowClient()
    who = client.whoami()
    console.print(
        f"[bold]flow-defaults[/bold]  org="
        f"{who.get('organization', {}).get('name')!r}  base={client.base}"
    )

    specs = ([t for t in fx.types if t.agent_type == agent_type]
             if agent_type else fx.types)
    if not specs:
        console.print(f"[red]no such type: {agent_type}[/red]")
        raise SystemExit(2)

    results = []
    for spec in specs:
        console.print(f"\n[bold cyan]▶ {spec.agent_type}[/bold cyan]")
        t0 = time.time()
        res = run_type(client, spec, fx.system_prompt, keep_agent=keep_agent)
        dt = time.time() - t0
        results.append(res)
        for a in res["assertions"]:
            mark = {"pass": "[green]✓[/green]",
                    "fail": "[red]✗[/red]"}.get(a["status"], a["status"])
            line = f"    {mark} ({a['tier']}) {a['name']}"
            if a["status"] != "pass" and a["detail"]:
                line += f"  [dim]{a['detail'][:120]}[/dim]"
            console.print(line)
        verdict = "[green]PASS[/green]" if res["passed"] else "[red]FAIL[/red]"
        console.print(
            f"    {verdict}  attached={res['attached']} driving={res['driving']}  "
            f"{res['counts']['pass']}p/{res['counts']['fail']}f  ({dt:.1f}s)  "
            f"agent_deleted={res['agent_deleted']}"
        )

    _write_summary(results)
    _print_summary_table(results)

    if not keep_agent:
        _report_lingering(client)

    total_fail = sum(r["counts"]["fail"] for r in results)
    raise SystemExit(1 if total_fail else 0)


def _print_summary_table(results: list[dict]) -> None:
    console.print("\n[bold]── summary ──────────────────────────────────────[/bold]")
    console.print(f"{'type':24} {'attached':9} {'driving':8} {'result'}")
    for r in results:
        att = "[green]yes[/green]" if r["attached"] else "[red]NO[/red]"
        drv = "[green]yes[/green]" if r["driving"] else "[red]NO[/red]"
        vr = "[green]PASS[/green]" if r["passed"] else "[red]FAIL[/red]"
        console.print(f"{r['agent_type']:24} {att:18} {drv:17} {vr}")
    n = len(results)
    npass = sum(1 for r in results if r["passed"])
    natt = sum(1 for r in results if r["attached"])
    ndrv = sum(1 for r in results if r["driving"])
    console.print(
        f"\n[bold]{'ALL PASS' if npass == n else 'FAILURES'}[/bold]  "
        f"types={n}  attached={natt}/{n}  driving={ndrv}/{n}  pass={npass}/{n}"
    )
    missing = [r["agent_type"] for r in results if not r["attached"]]
    if missing:
        console.print(f"[red]default flow did NOT attach for: {missing}[/red]")


def _write_summary(results: list[dict]) -> None:
    """Combined SUMMARY.json + SUMMARY.md (committed, matching results/whissle/*)."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    n = len(results)
    summary = {
        "suite": "flow_defaults",
        "ts": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "totals": {
            "types": n,
            "attached": sum(1 for r in results if r["attached"]),
            "driving": sum(1 for r in results if r["driving"]),
            "pass": sum(1 for r in results if r["passed"]),
        },
        "not_attached": [r["agent_type"] for r in results if not r["attached"]],
        "not_driving": [r["agent_type"] for r in results
                        if r["attached"] and not r["driving"]],
        "results": [
            {k: r[k] for k in ("agent_type", "attached", "driving", "start_state",
                               "start_kind", "passed", "counts", "agent_id",
                               "agent_deleted", "attach_attempts")}
            for r in results
        ],
    }
    (RESULTS_DIR / "SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    t = summary["totals"]
    lines = [
        "# Default-flow coverage — run summary",
        "",
        f"- **run**: {summary['ts']}",
        f"- **types**: {t['types']}  •  **attached**: {t['attached']}/{t['types']}"
        f"  •  **driving**: {t['driving']}/{t['types']}  •  **pass**: "
        f"{t['pass']}/{t['types']}",
    ]
    if summary["not_attached"]:
        lines.append(f"- **NOT ATTACHED**: {summary['not_attached']}")
    if summary["not_driving"]:
        lines.append(f"- **attached but NOT driving**: {summary['not_driving']}")
    lines += [
        "",
        "| type | start_state (kind) | attached | driving | pass/fail |",
        "|------|--------------------|----------|---------|-----------|",
    ]
    for r in results:
        lines.append(
            f"| `{r['agent_type']}` | `{r.get('start_state')}` "
            f"({r.get('start_kind')}) | {'yes' if r['attached'] else '**NO**'} | "
            f"{'yes' if r['driving'] else '**NO**'} | "
            f"{'PASS' if r['passed'] else '**FAIL**'} "
            f"({r['counts']['pass']}p/{r['counts']['fail']}f) |"
        )
    (RESULTS_DIR / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _report_lingering(client: FlowClient) -> None:
    """Belt-and-suspenders sweep: delete any surviving ``flowcov-*`` agent."""
    try:
        import requests

        r = requests.get(
            f"{client.base}/api/agents",
            headers={"Authorization": f"Bearer {client.api_key}"}, timeout=30,
        )
        r.raise_for_status()
        stragglers = [a for a in r.json()
                      if str(a.get("name", "")).startswith("flowcov-")]
    except Exception as e:  # noqa: BLE001
        console.print(f"[dim]could not verify agent cleanup: {e}[/dim]")
        return
    if stragglers:
        console.print(
            f"[red]WARNING: {len(stragglers)} flowcov-* agent(s) still present — "
            f"deleting[/red]")
        for a in stragglers:
            try:
                client.delete_agent(a["id"])
                console.print(f"    deleted {a['id']}")
            except Exception as e:  # noqa: BLE001
                console.print(f"    [red]failed to delete {a['id']}: {e}[/red]")
    else:
        console.print("[dim]cleanup verified: no flowcov-* agents linger[/dim]")


if __name__ == "__main__":
    app()
