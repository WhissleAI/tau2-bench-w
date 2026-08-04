# Copyright Sierra
"""Simulated-user FLOW-testing engine — driver, session runner, reporting, CLI.

Where ``flow/benchmark.py`` drives SCRIPTED turns and ``flow/defaults.py`` proves
default flows auto-attach, this suite drives an LLM SIMULATED USER (a persona +
goal, see ``usersim.py``) through a full conversation against a flow-enabled agent,
captures the engine's declared flow spec + accumulated step trace, and runs the
deterministic rule-analyzer (``analyze.py``) to surface state-tracking / state-rule
bugs.

Pipeline for one session:

  1. Create a flow-enabled agent of ``agent_type`` supplying NO flow — the backend
     auto-attaches that type's default state machine (enabled).
  2. Read it back (``GET /api/agents/{id}``) to get the DECLARED flow spec — the
     contract the trace is audited against.
  3. Drive a simulated conversation: user-sim opens, agent replies, user-sim reacts,
     … until the agent flow ``ended``, the user is done (goal met/refused), or a hard
     turn cap (~14) is hit.
  4. Pull the full accumulated step trace (``GET /api/agents/{id}/flow/trace``).
  5. Judge task success (LLM) and, optionally, per-turn goal-drift (LLM).
  6. Analyze the trace vs the declared flow → typed findings.
  7. ALWAYS delete the agent; write per-session JSON; aggregate a SUMMARY.

Reporting lands under ``results/whissle/flow_sim/<agent_type>/`` (per-session JSON +
a per-agent SUMMARY.md/json) plus a top-level ``SUMMARY.md`` when a run spans types.

Usage:
    python -m tau2.flow.simulate list
    python -m tau2.flow.simulate run --agent-type dental_receptionist --sessions 2
    python -m tau2.flow.simulate run --agent-type dental_receptionist --task-id dental_happy_book
    python -m tau2.flow.simulate run --agent-type debt_collection --sessions 10 --no-semantic
"""
from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from typer import Option, Typer

from tau2.flow.analyze import Finding, analyze_session, coverage_findings
from tau2.flow.client import FlowClient, FlowClientError, TurnResult
from tau2.flow.seed import (
    Seeder, action_tool_result, resolve_done, scan_pre_verify_disclosures,
)
from tau2.flow.usersim import (
    Task, UserSimulator, WhissleModel, judge_goal_drift, judge_task_success,
)

app = Typer(add_completion=False)
console = Console()

RESULTS_ROOT = Path("results/whissle/flow_sim")
TASKS_FIXTURE = Path("data/flow/sim_tasks.json")
AGENT_PREFIX = "flowsim-"


# ── task fixtures ───────────────────────────────────────────────────────────────

def load_tasks(agent_type: str) -> list[Task]:
    d = json.loads(TASKS_FIXTURE.read_text(encoding="utf-8"))
    block = d["types"].get(agent_type)
    if block is None:
        raise KeyError(f"no tasks for agent_type={agent_type!r} in {TASKS_FIXTURE}")
    defaults = {"compliance": block.get("compliance"),
                "max_turns": d.get("max_turns", 14)}
    return [Task.from_dict(agent_type, t, defaults) for t in block["tasks"]]


def all_agent_types() -> list[str]:
    d = json.loads(TASKS_FIXTURE.read_text(encoding="utf-8"))
    return list(d["types"].keys())


def system_prompt_for(agent_type: str) -> str:
    d = json.loads(TASKS_FIXTURE.read_text(encoding="utf-8"))
    return d["types"][agent_type].get(
        "system_prompt",
        "You are the agent under test. Follow your configured behavior and the "
        "current conversation-flow state instructions exactly.")


# ── trace helpers ────────────────────────────────────────────────────────────────

def _engine_turn_of(steps: list[dict]) -> Optional[int]:
    """The engine turn number shared by a chat/turn response's incremental steps."""
    turns = [s.get("turn") for s in steps if isinstance(s.get("turn"), int)]
    return Counter(turns).most_common(1)[0][0] if turns else None


def _state_goal(flow: dict, state_id: Optional[str]) -> str:
    for s in flow.get("states") or []:
        if s.get("id") == state_id:
            return (s.get("goal") or s.get("say") or "").strip()
    return ""


def _tool_calls_from_reply(reply: str) -> list[str]:
    """Best-effort surface of 🔧 tool lines the agent narrated in its reply."""
    out = []
    for line in (reply or "").splitlines():
        if "🔧" in line:
            out.append(line.strip())
    return out


# ── one simulated session ────────────────────────────────────────────────────────

def run_session(
    client: FlowClient, model: WhissleModel, task: Task, system_prompt: str, *,
    semantic: bool = True, keep_agent: bool = False,
    seeder: Optional[Seeder] = None,
) -> dict[str, Any]:
    """Create the agent, drive a full simulated conversation, analyze, ALWAYS clean
    up. Returns a JSON-serializable session result (also written to disk).

    When ``seeder`` is supplied this is the SEEDED end-to-end run: the agent's tools
    are given real records (customer / fleet / KB / credential) so they SUCCEED, the
    user-sim is primed with consistent identity facts, and the result carries the
    seeded-run assertions (action-tool-called, pre-verify disclosure scan,
    resolve_done). Every seeded record is torn down by exact id after the session."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = RESULTS_ROOT / task.agent_type
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"{task.id}_{ts}.jsonl"
    log = log_path.open("w", encoding="utf-8")

    def _emit(rec: dict[str, Any]) -> None:
        log.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        log.flush()

    agent_id: Optional[str] = None
    flow_spec: dict[str, Any] = {}
    conv_id: Optional[str] = None
    turns: list[dict[str, Any]] = []
    tools_used_by_turn: dict[int, list[str]] = {}
    full_steps: list[dict] = []
    setup_error: Optional[str] = None
    ended = False
    drift_flags: list[dict] = []
    seed_ctx = None
    seed_teardown: dict[str, Any] = {}

    _emit({"event": "session_start", "task_id": task.id, "agent_type": task.agent_type,
           "scenario": task.scenario, "persona": task.persona, "goal": task.goal,
           "ts": ts, "seeded": seeder is not None})

    try:
        agent = client.create_typed_agent(
            f"{AGENT_PREFIX}{task.id}", task.agent_type, system_prompt)
        agent_id = agent["id"]
        _emit({"event": "agent_created", "agent_id": agent_id})

        # Read back the DECLARED flow (retry a beat for auto-attach).
        for attempt in range(1, 5):
            got = client.get_agent(agent_id)
            if isinstance(got.get("flow"), dict):
                flow_spec = got["flow"]
                break
            time.sleep(3.0)
        _emit({"event": "flow_spec", "start_state": flow_spec.get("start_state"),
               "enabled": flow_spec.get("enabled"),
               "num_states": len(flow_spec.get("states") or []),
               "num_transitions": len(flow_spec.get("transitions") or [])})

        # ── SEED: insert the records this agent's tools need, prime the sim ──────
        if seeder is not None:
            seed_ctx = seeder.seed(agent_id, task)
            _emit({"event": "seeded", "steps": seed_ctx.steps,
                   "resources": [r.__dict__ for r in seed_ctx.resources],
                   "expected_action_tool": seed_ctx.expected_action_tool,
                   "goal_prefix": bool(seed_ctx.goal_prefix)})

        sim = UserSimulator(task=task, model=model)
        if seed_ctx is not None:
            sim.extra_facts = seed_ctx.sim_facts
            sim.goal_prefix = seed_ctx.goal_prefix
        user_msg = sim.first_utterance()

        # Bind the seeded contact on every turn (the text-channel analogue of a voice
        # call's calls.customer_id) so verify_identity has a record to match against.
        bound_customer_id = seed_ctx.customer_id if seed_ctx is not None else None
        for i in range(1, task.max_turns + 1):
            res: TurnResult = client.turn(
                agent_id, user_msg, conversation_id=conv_id,
                customer_id=bound_customer_id)
            conv_id = res.conversation_id or conv_id
            ended = bool(res.raw.get("ended"))
            eng_turn = _engine_turn_of(res.steps)
            if eng_turn is not None and res.tools_used:
                tools_used_by_turn.setdefault(eng_turn, []).extend(res.tools_used)

            drift = {}
            if semantic:
                drift = judge_goal_drift(
                    model, _state_goal(flow_spec, res.current_state), res.reply, user_msg)
                if drift.get("on_goal") is False:
                    drift_flags.append({"turn": i, "state": res.current_state,
                                        "reason": drift.get("reason")})

            rec = {
                "n": i, "user_msg": user_msg, "agent_reply": res.reply,
                "ended": ended, "current_state": res.current_state,
                "tools_used": res.tools_used,
                "tool_calls_in_reply": _tool_calls_from_reply(res.reply),
                "engine_turn": eng_turn, "steps": res.steps,
                "drift": drift,
            }
            turns.append(rec)
            _emit({"event": "turn", **rec})

            if ended or sim.done:
                _emit({"event": "conversation_end",
                       "reason": "flow_ended" if ended else "user_done", "turn": i})
                break
            user_msg = sim.next_utterance(res.reply)
        else:
            _emit({"event": "conversation_end", "reason": "turn_cap",
                   "turn": task.max_turns})

        # Full accumulated trace (authoritative for the analyzer).
        if agent_id and conv_id:
            try:
                tr = client.get_trace(agent_id, conv_id)
                full_steps = list((tr or {}).get("steps") or [])
            except FlowClientError as e:
                _emit({"event": "trace_fetch_failed", "detail": str(e)})

    except FlowClientError as e:
        setup_error = str(e)
        _emit({"event": "error", "phase": "setup/drive", "detail": setup_error})
    except Exception as e:  # noqa: BLE001
        setup_error = f"{type(e).__name__}: {e}"
        _emit({"event": "error", "phase": "setup/drive", "detail": setup_error})

    # Fall back to per-turn steps if the trace GET was unavailable.
    if not full_steps:
        for t in turns:
            full_steps.extend(t["steps"])
    full_steps = sorted(full_steps, key=lambda s: s.get("seq", 0))

    # ── judges + analyze ─────────────────────────────────────────────────────
    transcript = _render_transcript(turns)
    success = {"success": None, "reason": "not run"}
    if agent_id and turns and not setup_error:
        success = judge_task_success(model, task, transcript)
        _emit({"event": "task_success", **success})

    findings: list[Finding] = []
    if setup_error:
        findings.append(Finding("stuck_termination", "high",
                                f"session failed to run: {setup_error}"))
    elif flow_spec:
        findings = analyze_session(
            flow_spec, full_steps,
            tools_used_by_turn=tools_used_by_turn,
            ended=ended, goal_met=success.get("success"),
            compliance=task.compliance,
            transcript_lower="\n".join(
                (t["agent_reply"] or "") for t in turns).lower(),
        )
    else:
        findings.append(Finding("stuck_termination", "high",
                                "no declared flow spec available to audit against."))

    for f in findings:
        _emit({"event": "finding", **f.as_dict()})

    # ── seeded-run assertions (the "true completion" checks) ──────────────────
    seeded_checks: dict[str, Any] = {}
    if seed_ctx is not None:
        seeded_checks["seed_steps"] = seed_ctx.steps
        _flat_tools = [t for tt in turns for t in (tt.get("tools_used") or [])]
        seeded_checks["action_tool"] = action_tool_result(seed_ctx, _flat_tools)
        if task.agent_type == "debt_collection" and task.compliance:
            seeded_checks["disclosure_scan"] = scan_pre_verify_disclosures(
                turns, full_steps,
                gate_var=task.compliance.get("gate_variable", "identity_verified"),
                verify_states=task.compliance.get("verify_states"),
                forbidden=task.compliance.get("forbidden_substrings_lower"))
        if task.agent_type == "customer_support":
            seeded_checks["resolve"] = resolve_done(full_steps)
        _emit({"event": "seeded_checks", **seeded_checks})

    # ── teardown — NEVER leave a throwaway agent OR seeded record behind ──────
    if seeder is not None and seed_ctx is not None:
        seed_teardown = seeder.teardown(seed_ctx)
        _emit({"event": "seed_teardown", **seed_teardown})

    deleted = False
    if agent_id and not keep_agent:
        try:
            client.delete_agent(agent_id)
            deleted = True
            _emit({"event": "agent_deleted", "agent_id": agent_id})
        except Exception as e:  # noqa: BLE001
            _emit({"event": "agent_delete_failed", "detail": str(e)})

    sev_counts = Counter(f.severity for f in findings)
    type_counts = Counter(f.type for f in findings)
    result = {
        "task_id": task.id, "agent_type": task.agent_type, "scenario": task.scenario,
        "ts": ts, "agent_id": agent_id, "agent_deleted": deleted or keep_agent,
        "conversation_id": conv_id,
        "num_turns": len(turns), "ended": ended,
        "start_state": flow_spec.get("start_state"),
        "final_state": turns[-1]["current_state"] if turns else None,
        "task_success": success.get("success"),
        "task_success_reason": success.get("reason"),
        "goal_drift_turns": drift_flags,
        "finding_counts_by_severity": dict(sev_counts),
        "finding_counts_by_type": dict(type_counts),
        "high_severity": sev_counts.get("high", 0),
        "findings": [f.as_dict() for f in findings],
        "persona": task.persona, "goal": task.goal,
        "states_visited": [s.get("state") for s in full_steps
                           if s.get("kind") == "state_enter"],
        "turns": [{"user": t["user_msg"], "agent": t["agent_reply"],
                   "current_state": t["current_state"], "tools_used": t["tools_used"],
                   "ended": t["ended"]} for t in turns],
        "seeded": seeder is not None,
        "seeded_checks": seeded_checks,
        "seed_teardown": seed_teardown,
        "log": str(log_path),
        "_full_steps": full_steps,  # kept for aggregate coverage; stripped from md
    }
    _emit({"event": "session_result",
           "task_id": task.id, "num_turns": len(turns), "ended": ended,
           "task_success": success.get("success"),
           "high_severity": result["high_severity"],
           "finding_types": dict(type_counts)})
    log.close()

    (out_dir / f"{task.id}.json").write_text(
        json.dumps({k: v for k, v in result.items() if k != "_full_steps"},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _render_transcript(turns: list[dict]) -> str:
    lines = []
    for t in turns:
        lines.append(f"USER: {t['user_msg']}")
        lines.append(f"AGENT: {t['agent_reply']}")
    return "\n".join(lines)


# ── aggregation / reporting ──────────────────────────────────────────────────────

def _seeded_rollup(results: list[dict]) -> dict[str, Any]:
    """Aggregate the seeded-run "true completion" checks across a type's sessions:
    action-tool-called rate, debt pre-verify disclosure count + verify-fire rate,
    CS resolve_done rate, and how many sessions had every seed step succeed."""
    seeded = [r for r in results if r.get("seeded")]
    if not seeded:
        return {}
    roll: dict[str, Any] = {"seeded_sessions": len(seeded)}

    # action tool actually invoked (only over sessions with an expected tool).
    with_expected = [r for r in seeded
                     if (r.get("seeded_checks") or {}).get("action_tool", {}).get(
                         "expected")]
    if with_expected:
        called = sum(1 for r in with_expected
                     if r["seeded_checks"]["action_tool"].get("called"))
        roll["action_tool"] = {
            "expected_sessions": len(with_expected), "called": called,
            "by_tool": _by_tool(with_expected)}

    # debt compliance — pre-verify disclosure + verify-fire.
    scans = [r["seeded_checks"]["disclosure_scan"] for r in seeded
             if (r.get("seeded_checks") or {}).get("disclosure_scan")]
    if scans:
        roll["debt_compliance"] = {
            "sessions": len(scans),
            "pre_verify_disclosure_sessions":
                sum(1 for s in scans if s.get("pre_verify_disclosure")),
            "gate_opened_sessions": sum(1 for s in scans if s.get("gate_opened")),
            "total_violations": sum(len(s.get("violations") or []) for s in scans)}

    # CS resolve_done.
    resolves = [r["seeded_checks"]["resolve"] for r in seeded
                if (r.get("seeded_checks") or {}).get("resolve")]
    if resolves:
        roll["cs_resolve"] = {
            "sessions": len(resolves),
            "resolve_done_sessions": sum(1 for x in resolves if x.get("resolve_done")),
            "escalated_sessions": sum(1 for x in resolves if x.get("escalated"))}

    # seeding health — sessions where no seed step errored, and lingering resources.
    def _errored(r: dict) -> bool:
        return any(s.get("status") == "error"
                   for s in (r.get("seeded_checks") or {}).get("seed_steps") or [])
    roll["seed_health"] = {
        "sessions_no_seed_error": sum(1 for r in seeded if not _errored(r)),
        "teardown_failed": sum(
            len((r.get("seed_teardown") or {}).get("failed") or []) for r in seeded),
        "resources_tracked": sum(
            (r.get("seed_teardown") or {}).get("tracked", 0) for r in seeded)}
    return roll


def _by_tool(results: list[dict]) -> dict[str, str]:
    from collections import Counter
    exp: Counter = Counter()
    hit: Counter = Counter()
    for r in results:
        a = r["seeded_checks"]["action_tool"]
        exp[a["expected"]] += 1
        if a.get("called"):
            hit[a["expected"]] += 1
    return {t: f"{hit[t]}/{exp[t]}" for t in exp}


def aggregate_agent_type(agent_type: str, flow_spec: dict,
                         results: list[dict]) -> dict[str, Any]:
    """Coverage + finding rollup for one agent type over its sessions."""
    cov_findings, cov_table = coverage_findings(
        flow_spec, [r["_full_steps"] for r in results])
    end_ids = {st.get("id") for st in (flow_spec.get("states") or [])
               if st.get("type") == "end"}
    reached_end = sum(1 for r in results if r.get("final_state") in end_ids) \
        if end_ids else sum(1 for r in results if r["ended"])
    type_counter: Counter = Counter()
    sev_counter: Counter = Counter()
    for r in results:
        type_counter.update(r["finding_counts_by_type"])
        sev_counter.update(r["finding_counts_by_severity"])
    for f in cov_findings:
        type_counter[f.type] += 1
        sev_counter[f.severity] += 1

    summary = {
        "agent_type": agent_type,
        "ts": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "sessions": len(results),
        "sessions_ended_cleanly": sum(1 for r in results if r["ended"]),
        "reached_end_state": reached_end,
        "task_success": sum(1 for r in results if r["task_success"] is True),
        "sessions_with_high_findings": sum(1 for r in results if r["high_severity"]),
        "seeded_rollup": _seeded_rollup(results),
        "finding_counts_by_type": dict(type_counter),
        "finding_counts_by_severity": dict(sev_counter),
        "coverage": cov_table,
        "coverage_findings": [f.as_dict() for f in cov_findings],
        "sessions_detail": [
            {k: r[k] for k in ("task_id", "scenario", "num_turns", "ended",
                               "task_success", "final_state", "high_severity",
                               "finding_counts_by_type", "agent_deleted")}
            for r in results
        ],
    }
    out_dir = RESULTS_ROOT / agent_type
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_agent_markdown(out_dir / "SUMMARY.md", summary)
    return summary


def _write_agent_markdown(path: Path, s: dict) -> None:
    cov = s["coverage"]
    lines = [
        f"# Flow-sim summary — `{s['agent_type']}`",
        "",
        f"- **run**: {s['ts']}",
        f"- **sessions**: {s['sessions']}  •  **ended cleanly (flag)**: "
        f"{s['sessions_ended_cleanly']}/{s['sessions']}  •  **reached end state**: "
        f"{s.get('reached_end_state', '?')}/{s['sessions']}  •  **task success**: "
        f"{s['task_success']}/{s['sessions']}",
        f"- **sessions with HIGH-severity findings**: "
        f"{s['sessions_with_high_findings']}/{s['sessions']}",
        "",
    ]
    sr = s.get("seeded_rollup") or {}
    if sr:
        lines += ["## Seeded run — true-completion checks", ""]
        at = sr.get("action_tool")
        if at:
            lines.append(
                f"- **action tool actually called**: {at['called']}/"
                f"{at['expected_sessions']}  ({at.get('by_tool')})")
        dc = sr.get("debt_compliance")
        if dc:
            lines.append(
                f"- **debt pre-verify disclosures**: "
                f"{dc['pre_verify_disclosure_sessions']}/{dc['sessions']} sessions "
                f"({dc['total_violations']} total)  •  **verify/gate opened**: "
                f"{dc['gate_opened_sessions']}/{dc['sessions']}")
        cs = sr.get("cs_resolve")
        if cs:
            lines.append(
                f"- **CS resolve_done**: {cs['resolve_done_sessions']}/{cs['sessions']}"
                f"  •  **escalated**: {cs['escalated_sessions']}/{cs['sessions']}")
        sh = sr.get("seed_health") or {}
        lines.append(
            f"- **seed health**: no-error {sh.get('sessions_no_seed_error')}/"
            f"{sr.get('seeded_sessions')}  •  resources tracked "
            f"{sh.get('resources_tracked')}  •  teardown-failed "
            f"{sh.get('teardown_failed')}")
        lines.append("")
    lines += [
        "## Findings by type",
        "",
        "| type | count |",
        "|------|-------|",
    ]
    for t, c in sorted(s["finding_counts_by_type"].items(),
                       key=lambda kv: -kv[1]):
        lines.append(f"| `{t}` | {c} |")
    lines += [
        "",
        "## Findings by severity",
        "",
        "| severity | count |",
        "|----------|-------|",
    ]
    for sev in ("high", "medium", "low", "info"):
        if s["finding_counts_by_severity"].get(sev):
            lines.append(f"| {sev} | {s['finding_counts_by_severity'][sev]} |")
    lines += [
        "",
        "## State / transition coverage",
        "",
        f"- **states visited**: {cov['states_visited']}/{cov['states_total']}",
        f"- **transitions fired**: {cov['transitions_fired']}/{cov['transitions_total']}",
    ]
    if cov["states_unvisited"]:
        lines.append(f"- **states never entered**: `{cov['states_unvisited']}`")
    if cov["transitions_unfired"]:
        lines.append(f"- **transitions never fired**: `{cov['transitions_unfired']}`")
    lines += ["", "## Sessions", "",
              "| task | scenario | turns | ended | task_success | final_state | "
              "high | finding types |",
              "|------|----------|-------|-------|--------------|-------------|------|"
              "---------------|"]
    for d in s["sessions_detail"]:
        ft = ", ".join(f"{k}×{v}" for k, v in d["finding_counts_by_type"].items())
        lines.append(
            f"| `{d['task_id']}` | {d['scenario']} | {d['num_turns']} | "
            f"{'yes' if d['ended'] else 'no'} | {d['task_success']} | "
            f"`{d['final_state']}` | {d['high_severity']} | {ft} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_overall_markdown(agent_summaries: list[dict]) -> None:
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "# Flow-sim — overall run summary",
        "",
        f"- **run**: {ts}",
        f"- **agent types**: {len(agent_summaries)}",
        "",
        "| agent_type | sessions | ended | task_success | high-sev sessions | "
        "states cov | trans cov |",
        "|------------|----------|-------|--------------|-------------------|"
        "-----------|-----------|",
    ]
    for s in agent_summaries:
        cov = s["coverage"]
        lines.append(
            f"| `{s['agent_type']}` | {s['sessions']} | "
            f"{s['sessions_ended_cleanly']}/{s['sessions']} | "
            f"{s['task_success']}/{s['sessions']} | "
            f"{s['sessions_with_high_findings']} | "
            f"{cov['states_visited']}/{cov['states_total']} | "
            f"{cov['transitions_fired']}/{cov['transitions_total']} |")
    total_types: Counter = Counter()
    for s in agent_summaries:
        total_types.update(s["finding_counts_by_type"])
    lines += ["", "## All findings by type (aggregate)", "",
              "| type | count |", "|------|-------|"]
    for t, c in sorted(total_types.items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{t}` | {c} |")
    (RESULTS_ROOT / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (RESULTS_ROOT / "SUMMARY.json").write_text(
        json.dumps({"ts": ts, "agent_types": agent_summaries,
                    "aggregate_finding_types": dict(total_types)},
                   ensure_ascii=False, indent=2, default=str), encoding="utf-8")


# ── CLI ───────────────────────────────────────────────────────────────────────

@app.command("list")
def list_tasks_cmd() -> None:
    """List the agent types and their task counts."""
    d = json.loads(TASKS_FIXTURE.read_text(encoding="utf-8"))
    for atype, block in d["types"].items():
        gate = "  (compliance gate)" if block.get("compliance") else ""
        console.print(f"[bold]{atype}[/bold]  tasks={len(block['tasks'])}{gate}")
        for t in block["tasks"]:
            console.print(f"    [dim]{t['id']} — {t.get('scenario','')}[/dim]")


# Pre-seed AFTER baseline (round-2, tools fail-soft) — the "before" column for the
# seeded before→after comparison. task_success / reached-end out of 10.
PRE_SEED_BASELINE = {
    "dental_receptionist":   {"success": 3, "reached_end": 0},
    "appointment_scheduling": {"success": 2, "reached_end": 0},
    "car_rental":            {"success": 1, "reached_end": 0},
    "customer_support":      {"success": 4, "reached_end": 3},
    "debt_collection":       {"success": 4, "reached_end": None},
}


def _recompute_action_tool(atype: str) -> dict[str, Any]:
    """Recompute action-tool-called for a type from its per-session JSONs, using the
    flat ``turns[].tools_used`` list (authoritative) rather than the stored value."""
    from collections import Counter
    exp: Counter = Counter()
    hit: Counter = Counter()
    n = 0
    for p in sorted((RESULTS_ROOT / atype).glob("*.json")):
        if p.name == "SUMMARY.json":
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        expected = ((d.get("seeded_checks") or {}).get("action_tool") or {}).get(
            "expected")
        if not expected:
            continue
        n += 1
        exp[expected] += 1
        flat = [t for tt in (d.get("turns") or []) for t in (tt.get("tools_used") or [])]
        if expected in flat:
            hit[expected] += 1
    if not n:
        return {}
    return {"expected_sessions": n, "called": sum(hit.values()),
            "by_tool": {t: f"{hit[t]}/{exp[t]}" for t in exp}}


@app.command("seeded-report")
def seeded_report() -> None:
    """Assemble the TRUE before→after table from each type's SUMMARY.json (written by
    a ``--seeded`` run) against the pre-seed baseline. Emits SEEDED_REPORT.md."""
    rows = []
    for atype in ["dental_receptionist", "appointment_scheduling", "car_rental",
                  "debt_collection", "customer_support"]:
        p = RESULTS_ROOT / atype / "SUMMARY.json"
        if not p.exists():
            console.print(f"[yellow]no summary for {atype} (skipped)[/yellow]")
            continue
        s = json.loads(p.read_text(encoding="utf-8"))
        rows.append((atype, s))

    lines = ["# Seeded end-to-end flow validation — true before→after", "",
             f"- **run**: {datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
             "- **before** = pre-seed AFTER (round-2, tools fail-soft); "
             "**after** = seeded (tools succeed on real records)", "",
             "## Task success & reached-end (N/10)", "",
             "| agent | success before→after | reached-end before→after | "
             "action tool called | notes |",
             "|-------|----------------------|--------------------------|"
             "--------------------|-------|"]
    for atype, s in rows:
        base = PRE_SEED_BASELINE.get(atype, {})
        n = s["sessions"]
        succ = s["task_success"]
        end = s.get("reached_end_state", "?")
        sr = s.get("seeded_rollup") or {}
        # Recompute action-tool from each session's authoritative flat tool list
        # (turns[].tools_used) so the metric is exact regardless of engine-turn
        # resolution at run time.
        at = _recompute_action_tool(atype)
        at_str = (f"{at['called']}/{at['expected_sessions']} "
                  f"{at.get('by_tool')}" if at else "—")
        note = ""
        dc = sr.get("debt_compliance")
        if dc:
            note = (f"pre-verify disclosures {dc['pre_verify_disclosure_sessions']}"
                    f"/{dc['sessions']}; gate opened {dc['gate_opened_sessions']}"
                    f"/{dc['sessions']}")
        cs = sr.get("cs_resolve")
        if cs:
            note = (f"resolve_done {cs['resolve_done_sessions']}/{cs['sessions']}; "
                    f"escalated {cs['escalated_sessions']}/{cs['sessions']}")
        b_s = base.get("success"); b_e = base.get("reached_end")
        lines.append(
            f"| `{atype}` | {b_s if b_s is not None else '?'}→{succ} /{n} | "
            f"{b_e if b_e is not None else '?'}→{end} /{n} | {at_str} | {note} |")

    # Debt compliance headline.
    debt = next((s for a, s in rows if a == "debt_collection"), None)
    if debt:
        dc = (debt.get("seeded_rollup") or {}).get("debt_compliance") or {}
        lines += ["", "## Debt compliance headline", "",
                  f"- **pre-verify disclosures**: "
                  f"{dc.get('pre_verify_disclosure_sessions', '?')}/"
                  f"{dc.get('sessions', '?')}  (target 0)",
                  f"- **verify/gate opened**: {dc.get('gate_opened_sessions', '?')}/"
                  f"{dc.get('sessions', '?')}",
                  f"- **total forbidden-substring violations (independent scan)**: "
                  f"{dc.get('total_violations', '?')}"]

    # Seed health / lingering.
    lines += ["", "## Seed health", "",
              "| agent | seeded sessions | no-error | resources tracked | "
              "teardown-failed |", "|-------|-----------------|----------|"
              "-------------------|-----------------|"]
    for atype, s in rows:
        sh = (s.get("seeded_rollup") or {}).get("seed_health") or {}
        sr = s.get("seeded_rollup") or {}
        lines.append(
            f"| `{atype}` | {sr.get('seeded_sessions', '?')} | "
            f"{sh.get('sessions_no_seed_error', '?')} | "
            f"{sh.get('resources_tracked', '?')} | {sh.get('teardown_failed', '?')} |")

    out = RESULTS_ROOT / "SEEDED_REPORT.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    console.print(f"[green]wrote {out}[/green]")
    console.print("\n".join(lines))


@app.command()
def run(
    agent_type: str = Option(..., help="seeded agent type (e.g. dental_receptionist)"),
    sessions: int = Option(2, help="how many sessions to run (cycles tasks if > count)"),
    task_id: Optional[str] = Option(
        None, help="run specific task id(s) instead — comma-separated for several"),
    semantic: bool = Option(True, help="run the per-turn goal-drift LLM judge"),
    keep_agent: bool = Option(False, help="do NOT delete the throwaway agents (debug)"),
    max_turns: Optional[int] = Option(None, help="override the per-session turn cap"),
    seeded: bool = Option(
        False, help="SEEDED end-to-end run: insert real records so tools succeed, "
        "prime the sim, add the true-completion assertions, tear the records down"),
) -> None:
    """Run simulated-user sessions for one agent type against the LIVE backend."""
    client = FlowClient()
    model = WhissleModel()
    who = client.whoami()
    seeder = Seeder(client, org_id=(who.get("organization") or {}).get("id")) \
        if seeded else None
    console.print(
        f"[bold]flow-sim[/bold]  org={who.get('organization', {}).get('name')!r}  "
        f"type={agent_type}  base={client.base}  "
        f"{'[magenta]SEEDED[/magenta]' if seeded else 'unseeded'}")

    tasks = load_tasks(agent_type)
    if task_id:
        wanted = [t.strip() for t in task_id.split(",") if t.strip()]
        by_id = {t.id: t for t in tasks}
        missing = [w for w in wanted if w not in by_id]
        if missing:
            console.print(f"[red]no such task_id(s): {missing}[/red]")
            raise SystemExit(2)
        chosen = [by_id[w] for w in wanted]
    else:
        chosen = [tasks[i % len(tasks)] for i in range(sessions)]
    if max_turns:
        for t in chosen:
            t.max_turns = max_turns

    system_prompt = system_prompt_for(agent_type)
    results: list[dict] = []
    for idx, task in enumerate(chosen, start=1):
        console.print(f"\n[bold cyan]▶ session {idx}/{len(chosen)}[/bold cyan]  "
                      f"{task.id} ({task.scenario})")
        t0 = time.time()
        res = run_session(client, model, task, system_prompt,
                          semantic=semantic, keep_agent=keep_agent, seeder=seeder)
        dt = time.time() - t0
        results.append(res)
        _print_session_line(res, dt)

    # Coverage needs the declared flow spec. Re-fetch it from a throwaway agent once
    # (cheap) so coverage is exact even when every session's agent is already deleted.
    flow_spec = _fetch_flow_spec(client, agent_type, system_prompt, keep_agent)
    summary = aggregate_agent_type(agent_type, flow_spec, results)
    _print_agent_summary(summary)
    _write_overall_markdown([summary])

    if not keep_agent:
        _report_lingering(client)

    total_high = sum(r["high_severity"] for r in results)
    console.print(f"\n[bold]done[/bold]  sessions={len(results)}  "
                  f"high-severity findings={total_high}  "
                  f"model_calls={model.calls}  model_cost=${model.total_cost_usd:.4f}")
    # A findings-oriented harness: a clean run exits 0, findings exit 0 too (they are
    # the DELIVERABLE, not a harness failure). Only a setup crash is non-zero.
    raise SystemExit(0)


def _fetch_flow_spec(client: FlowClient, agent_type: str, system_prompt: str,
                     keep_agent: bool) -> dict:
    """Create one throwaway agent solely to read the declared flow for coverage."""
    aid = None
    try:
        agent = client.create_typed_agent(
            f"{AGENT_PREFIX}covspec-{agent_type}", agent_type, system_prompt)
        aid = agent["id"]
        for _ in range(5):
            got = client.get_agent(aid)
            if isinstance(got.get("flow"), dict):
                return got["flow"]
            time.sleep(3.0)
        return {}
    except Exception as e:  # noqa: BLE001
        console.print(f"[dim]could not fetch flow spec for coverage: {e}[/dim]")
        return {}
    finally:
        if aid and not keep_agent:
            try:
                client.delete_agent(aid)
            except Exception:  # noqa: BLE001
                pass


def _print_session_line(res: dict, dt: float) -> None:
    ft = ", ".join(f"{k}×{v}" for k, v in res["finding_counts_by_type"].items()) or "none"
    hi = res["high_severity"]
    tag = f"[red]{hi} HIGH[/red]" if hi else "[green]0 high[/green]"
    console.print(
        f"    turns={res['num_turns']} ended={res['ended']} "
        f"success={res['task_success']} final={res['final_state']!r}  "
        f"findings: {ft}  {tag}  ({dt:.1f}s)  deleted={res['agent_deleted']}")
    sc = res.get("seeded_checks") or {}
    if sc:
        bits = []
        at = sc.get("action_tool") or {}
        if at.get("expected"):
            mark = "[green]✓[/green]" if at.get("called") else "[red]✗ NOT CALLED[/red]"
            bits.append(f"action[{at['expected']}]={mark}")
        ds = sc.get("disclosure_scan")
        if ds:
            pv = ds.get("pre_verify_disclosure")
            bits.append(("[red]PRE-VERIFY LEAK[/red]" if pv else "[green]0 pre-verify[/green]")
                        + f" gate={'open' if ds.get('gate_opened') else 'closed'}")
        rv = sc.get("resolve")
        if rv:
            bits.append(("[green]resolve_done[/green]" if rv.get("resolve_done")
                         else "[yellow]no-resolve[/yellow]")
                        + (" escalated" if rv.get("escalated") else ""))
        st = sc.get("seed_steps") or []
        seed_ok = [x for x in st if x.get("status") == "ok"]
        seed_skip = [x for x in st if x.get("status") == "skipped"]
        bits.append(f"seed(ok={len(seed_ok)},skip={len(seed_skip)})")
        td = res.get("seed_teardown") or {}
        bits.append(f"torn_down={len((td.get('deleted') or []))}/{td.get('tracked', 0)}")
        console.print("      seeded: " + "  ".join(bits))


def _print_agent_summary(s: dict) -> None:
    cov = s["coverage"]
    console.print(f"\n[bold]── {s['agent_type']} summary ──[/bold]")
    console.print(f"  sessions={s['sessions']}  ended={s['sessions_ended_cleanly']}  "
                  f"task_success={s['task_success']}  "
                  f"high-sev sessions={s['sessions_with_high_findings']}")
    console.print(f"  coverage: states {cov['states_visited']}/{cov['states_total']}  "
                  f"transitions {cov['transitions_fired']}/{cov['transitions_total']}")
    if s["finding_counts_by_type"]:
        console.print("  findings by type: " + ", ".join(
            f"{k}×{v}" for k, v in sorted(
                s["finding_counts_by_type"].items(), key=lambda kv: -kv[1])))


def _report_lingering(client: FlowClient) -> None:
    try:
        import requests
        r = requests.get(f"{client.base}/api/agents",
                         headers={"Authorization": f"Bearer {client.api_key}"},
                         timeout=30)
        r.raise_for_status()
        stragglers = [a for a in r.json()
                      if str(a.get("name", "")).startswith(AGENT_PREFIX)]
    except Exception as e:  # noqa: BLE001
        console.print(f"[dim]could not verify agent cleanup: {e}[/dim]")
        return
    if stragglers:
        console.print(f"[red]WARNING: {len(stragglers)} {AGENT_PREFIX}* agent(s) "
                      f"still present — deleting[/red]")
        for a in stragglers:
            try:
                client.delete_agent(a["id"])
                console.print(f"    deleted {a['id']}")
            except Exception as e:  # noqa: BLE001
                console.print(f"    [red]failed to delete {a['id']}: {e}[/red]")
    else:
        console.print(f"[dim]cleanup verified: no {AGENT_PREFIX}* agents linger[/dim]")


if __name__ == "__main__":
    app()
