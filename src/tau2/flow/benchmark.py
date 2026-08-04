# Copyright Sierra
"""Conversation-FLOW state-machine bench suite (multi-turn, multi-tool).

Verifies that Whissle's in-call flow engine drives correctly over long,
multi-tool, multi-state sessions. Each scenario (data/flow/*.json) authors a flow
onto a throwaway agent, drives scripted user turns over the deterministic TEXT
channel, and asserts the observable outcome (verbatim say-markers + tool calls +
per-state tool-gating) plus — when the ``flow-step-trace`` field is deployed — the
exact state sequence, fired transitions and guard trips.

This is the flow analogue of ``voice/transcription/benchmark.py``: a standalone
Typer app driving the SAME product surface a customer hits (POST
/api/agents/{id}/chat/turn), reading WHISSLE_BASE + WHISSLE_API_KEY from .env.

Design: GRACEFUL DEGRADATION. The step-trace field lands with a parallel backend
PR. Observable assertions (replies, tools_used) run and gate the suite today; the
trace-dependent assertions report ``skipped-pending-trace`` until the field ships,
then become strict automatically — no harness change needed.

Usage:
    python -m tau2.flow.benchmark list                 # scenarios + what they assert
    python -m tau2.flow.benchmark run                  # all scenarios
    python -m tau2.flow.benchmark run --scenario marker # one scenario (the canary)
    python -m tau2.flow.benchmark run --keep-agent      # skip teardown (debugging)

Requires WHISSLE_API_KEY (a wsk_ secret key) + WHISSLE_BASE in the environment
(see run_flow.sh). Uses only base deps (requests/typer/rich/dotenv) — no --extra.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from typer import Option, Typer

from tau2.flow.client import FlowClient, FlowClientError, TurnResult
from tau2.flow.scenarios import (
    Assertion,
    Scenario,
    all_scenario_ids,
    grade_observable,
    grade_trace,
    load_scenario,
)

app = Typer(add_completion=False)
console = Console()

RESULTS_DIR = Path("results/whissle/flow")


# ── one scenario ──────────────────────────────────────────────────────────────

def run_scenario(
    client: FlowClient, scn: Scenario, *, keep_agent: bool = False,
) -> dict[str, Any]:
    """Create the agent, author the flow, drive the turns, grade, ALWAYS clean up.

    Returns a JSON-serializable result dict (also written to disk by the caller).
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RESULTS_DIR / f"{scn.id}_{ts}.jsonl"
    log = log_path.open("w", encoding="utf-8")

    def _emit(rec: dict[str, Any]) -> None:
        log.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        log.flush()

    agent_id: Optional[str] = None
    turns: list[TurnResult] = []
    per_turn_steps: list[dict] = []
    setup_error: Optional[str] = None
    conv_id: Optional[str] = None

    _emit({"event": "scenario_start", "id": scn.id, "title": scn.title,
           "description": scn.description, "ts": ts})

    try:
        agent = client.create_agent(scn.agent)
        agent_id = agent["id"]
        _emit({"event": "agent_created", "agent_id": agent_id,
               "name": agent.get("name")})

        # Author the flow (422 here = a malformed/unsafe flow rejected at write time).
        client.set_flow(agent_id, scn.flow)
        _emit({"event": "flow_set", "agent_id": agent_id,
               "start_state": scn.flow.get("start_state")})

        # Drive the scripted turns on one thread.
        for i, spec in enumerate(scn.turns, start=1):
            res = client.turn(agent_id, spec.user, conversation_id=conv_id)
            conv_id = res.conversation_id or conv_id
            turns.append(res)
            per_turn_steps.extend(res.steps)
            _emit({
                "event": "turn", "n": i, "user": spec.user, "reply": res.reply,
                "tools_used": res.tools_used, "flow_present": res.flow_present,
                "current_state": res.current_state, "steps": res.steps,
            })

    except FlowClientError as e:
        setup_error = str(e)
        _emit({"event": "error", "phase": "setup/drive", "detail": setup_error})
    except Exception as e:  # noqa: BLE001 — record, still tear down below
        setup_error = f"{type(e).__name__}: {e}"
        _emit({"event": "error", "phase": "setup/drive", "detail": setup_error})

    # Pull the full accumulated trace (None until the step-trace PR is deployed).
    full_trace: Optional[dict] = None
    if agent_id and conv_id and not setup_error:
        try:
            full_trace = client.get_trace(agent_id, conv_id)
        except FlowClientError as e:
            _emit({"event": "trace_fetch_failed", "detail": str(e)})

    steps = list((full_trace or {}).get("steps") or []) or per_turn_steps
    trace_present = bool(full_trace) or any(r.flow_present for r in turns)

    # Grade.
    assertions: list[Assertion] = []
    if setup_error:
        assertions.append(Assertion("setup", "observable", "fail", setup_error))
    else:
        assertions += grade_observable(scn, turns)
        assertions += grade_trace(scn, steps, trace_present)

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
    pending = [a for a in assertions if a.status == "skipped-pending-trace"]
    passed = [a for a in assertions if a.status == "pass"]
    result = {
        "id": scn.id,
        "title": scn.title,
        "ts": ts,
        "agent_id": agent_id,
        "trace_present": trace_present,
        "agent_deleted": deleted or keep_agent,
        "counts": {"pass": len(passed), "fail": len(failed),
                   "pending_trace": len(pending), "total": len(assertions)},
        "passed": (not failed),
        "assertions": [a.__dict__ for a in assertions],
        "turns": [
            {"user": s.user, "reply": r.reply, "tools_used": r.tools_used,
             "flow_present": r.flow_present}
            for s, r in zip(scn.turns, turns)
        ],
        "log": str(log_path),
    }
    _emit({"event": "scenario_result", **result["counts"], "passed": result["passed"]})
    log.close()

    # Latest-result JSON (overwrite), matching results/whissle/*.json convention.
    (RESULTS_DIR / f"{scn.id}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(scn, result, assertions)
    return result


def _write_markdown(scn: Scenario, result: dict, assertions: list[Assertion]) -> None:
    """A human-readable per-scenario summary alongside the JSON/JSONL."""
    lines = [
        f"# Flow scenario: {scn.title}",
        "",
        f"- **id**: `{scn.id}`",
        f"- **agent**: `{result.get('agent_id')}` (deleted: {result['agent_deleted']})",
        f"- **trace present**: {result['trace_present']} "
        f"({'strict' if result['trace_present'] else 'trace assertions SKIPPED-pending-trace'})",
        f"- **result**: {'PASS' if result['passed'] else 'FAIL'} — "
        f"{result['counts']['pass']} pass / {result['counts']['fail']} fail / "
        f"{result['counts']['pending_trace']} pending-trace",
        "",
        scn.description,
        "",
        "## Turns",
        "",
    ]
    for i, t in enumerate(result["turns"], start=1):
        lines += [f"**Turn {i}** — user: `{t['user']}`",
                  f"> {t['reply']}",
                  f"tools_used: `{t['tools_used']}`", ""]
    lines += ["## Assertions", ""]
    glyph = {"pass": "PASS", "fail": "FAIL", "skipped-pending-trace": "PENDING-TRACE"}
    for a in assertions:
        lines.append(f"- [{glyph[a.status]}] ({a.tier}) `{a.name}`"
                     + (f" — {a.detail}" if a.detail and a.status != "pass" else ""))
    (RESULTS_DIR / f"{scn.id}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── CLI ───────────────────────────────────────────────────────────────────────

@app.command("list")
def list_scenarios() -> None:
    """List the flow scenarios and what each asserts."""
    for sid in all_scenario_ids():
        scn = load_scenario(sid)
        console.print(f"[bold]{scn.id}[/bold] — {scn.title}")
        console.print(f"    {scn.description}", style="dim")
        console.print(
            f"    turns={len(scn.turns)}  say_markers={len(scn.expect_say_markers)}  "
            f"state_seq={len(scn.expect_state_sequence)}  "
            f"fired={len(scn.expect_fired_transitions)}"
        )


@app.command()
def run(
    scenario: Optional[str] = Option(
        None, help="one scenario id (e.g. marker); omit to run all"),
    keep_agent: bool = Option(
        False, help="do NOT delete the throwaway agent (debugging only)"),
) -> None:
    """Run the flow suite against the live backend and print a pass/fail summary."""
    client = FlowClient()
    who = client.whoami()
    console.print(
        f"[bold]flow bench[/bold]  org={who.get('organization', {}).get('name')!r}  "
        f"base={client.base}"
    )

    ids = [scenario] if scenario else all_scenario_ids()
    results = []
    for sid in ids:
        scn = load_scenario(sid)
        console.print(f"\n[bold cyan]▶ {scn.id}[/bold cyan]  {scn.title}")
        t0 = time.time()
        res = run_scenario(client, scn, keep_agent=keep_agent)
        dt = time.time() - t0
        results.append(res)

        c = res["counts"]
        for a in res["assertions"]:
            mark = {"pass": "[green]✓[/green]", "fail": "[red]✗[/red]",
                    "skipped-pending-trace": "[yellow]▷[/yellow]"}[a["status"]]
            line = f"    {mark} ({a['tier']}) {a['name']}"
            if a["status"] != "pass" and a["detail"]:
                line += f"  [dim]{a['detail'][:120]}[/dim]"
            console.print(line)
        verdict = "[green]PASS[/green]" if res["passed"] else "[red]FAIL[/red]"
        console.print(
            f"    {verdict}  {c['pass']} pass / {c['fail']} fail / "
            f"{c['pending_trace']} pending-trace  ({dt:.1f}s)  "
            f"agent_deleted={res['agent_deleted']}  log={res['log']}"
        )

    # Overall + a no-linger guard: report any throwaway agents still present.
    total_fail = sum(r["counts"]["fail"] for r in results)
    total_pending = sum(r["counts"]["pending_trace"] for r in results)
    console.print(
        f"\n[bold]{'ALL PASS' if total_fail == 0 else 'FAILURES'}[/bold]  "
        f"scenarios={len(results)}  fails={total_fail}  pending-trace={total_pending}"
    )
    if not keep_agent:
        _report_lingering(client)
    raise SystemExit(1 if total_fail else 0)


def _report_lingering(client: FlowClient) -> None:
    """Belt-and-suspenders: flag any agent whose name starts with 'flowbench-' that
    survived (a delete that failed). run_scenario already deletes in every path."""
    try:
        import requests

        r = requests.get(
            f"{client.base}/api/agents",
            headers={"Authorization": f"Bearer {client.api_key}"}, timeout=30,
        )
        r.raise_for_status()
        stragglers = [a for a in r.json()
                      if str(a.get("name", "")).startswith("flowbench-")]
    except Exception as e:  # noqa: BLE001
        console.print(f"[dim]could not verify agent cleanup: {e}[/dim]")
        return
    if stragglers:
        console.print(
            f"[red]WARNING: {len(stragglers)} flowbench-* agent(s) still present — "
            f"deleting[/red]")
        for a in stragglers:
            try:
                client.delete_agent(a["id"])
                console.print(f"    deleted {a['id']}")
            except Exception as e:  # noqa: BLE001
                console.print(f"    [red]failed to delete {a['id']}: {e}[/red]")
    else:
        console.print("[dim]cleanup verified: no flowbench-* agents linger[/dim]")


if __name__ == "__main__":
    app()
