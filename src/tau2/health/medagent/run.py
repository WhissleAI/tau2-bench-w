"""MedAgentBench runner CLI.

    python -m tau2.health.medagent.run fetch
    python -m tau2.health.medagent.run run --limit 10
    python -m tau2.health.medagent.run run --tasks task3,task8 --write-check execute
    python -m tau2.health.medagent.run preflight-mode-b

See MEDAGENTBENCH.md.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from rich.console import Console
from typer import Option, Typer

from tau2.health.medagent.agent_tools_mode import preflight as mode_b_preflight
from tau2.health.medagent.brain import WhissleBrain
from tau2.health.medagent.data import (
    ALL_CATEGORIES,
    fetch_data,
    fhir_api_base,
    load_cases,
    load_funcs,
)
from tau2.health.medagent.episode import Episode, run_episode
from tau2.health.medagent.fhir import FhirWriter, verify_fhir_server
from tau2.health.medagent.grader import builtin_grade, load_refsol
from tau2.health.medagent.integrity import assess
from tau2.health.medagent.report import (
    RESULTS_ROOT,
    TaskResult,
    summarize,
    write_artifacts,
)

load_dotenv()
app = Typer(add_completion=False, help="MedAgentBench — Whissle adapter")
console = Console()

MODE_A = "brain-parity"
MODE_B = "agent-tools"


@app.command()
def fetch(force: bool = Option(False, help="Re-download even if present.")) -> None:
    """Download the upstream task set + FHIR function catalogue."""
    tasks, funcs = fetch_data(force=force)
    cases = load_cases(tasks)
    console.print(f"tasks → {tasks} ({len(cases)} cases)")
    console.print(f"funcs → {funcs} ({len(load_funcs(funcs))} functions)")


@app.command("list")
def list_tasks() -> None:
    """Show the category breakdown of the loaded task set."""
    cases = load_cases()
    console.print(f"[bold]{len(cases)}[/bold] tasks")
    for cat in ALL_CATEGORIES:
        n = sum(1 for c in cases if c.category == cat)
        kind = "action" if any(c.is_action for c in cases if c.category == cat) else "query"
        console.print(f"  {cat:<8} {kind:<7} n={n}")


@app.command("preflight-mode-b")
def preflight_mode_b() -> None:
    """Report whether mode B (the shipped ehr_assistant) can run yet."""
    pf = mode_b_preflight()
    console.print_json(json.dumps(pf.as_dict(), indent=2))
    if not pf.available:
        console.print(
            "\n[yellow]Mode B unavailable — this does not affect mode A.[/yellow]"
        )


@app.command()
def run(
    mode: str = Option(
        MODE_A,
        help=(
            f"'{MODE_A}': benchmark tools + Whissle brain, upstream protocol "
            f"(the publishable number). '{MODE_B}': the shipped ehr_assistant "
            "with its own FHIR tools."
        ),
    ),
    limit: Optional[int] = Option(
        None, help="Run only N tasks, stratified across all 10 categories."
    ),
    tasks: Optional[str] = Option(
        None, help="Comma-separated task ids or categories, e.g. 'task3,task8_1'."
    ),
    categories: Optional[str] = Option(None, help="Comma-separated categories."),
    write_check: str = Option(
        "validate",
        help=(
            "none: upstream parity. validate: ask the EHR whether it would "
            "accept each write (non-mutating, default). execute: really POST "
            "and read the resource back (mutates — use a disposable EHR)."
        ),
    ),
    cleanup_writes: bool = Option(
        True, help="With --write-check execute, delete created resources afterwards."
    ),
    max_round: int = Option(8, help="Round budget per task (upstream default: 8)."),
    concurrency: int = Option(4, help="Tasks in flight."),
    refsol: Optional[str] = Option(
        None, help="Path to the official refsol.py (else the built-in graders)."
    ),
    system_mode: str = Option(
        "neutral", help="neutral | prompt-as-system | agent-default"
    ),
    agent_id: Optional[str] = Option(None, help="Override WHISSLE_AGENT_ID."),
    model: Optional[str] = Option(None, help="Override WHISSLE_MODEL."),
    retry_infra: bool = Option(True, help="Retry an infra-failed task once."),
    save_to: Optional[str] = Option(None, help="Results root (default results/whissle/medagentbench)."),
    run_name: Optional[str] = Option(None, help="Name the run directory."),
) -> None:
    """Run the benchmark and write per-task artifacts + a summary."""
    if mode == MODE_B:
        pf = mode_b_preflight(agent_id=agent_id)
        console.print("[bold]Mode B preflight[/bold]")
        console.print_json(json.dumps(pf.as_dict(), indent=2))
        if not pf.available:
            console.print(
                "\n[yellow]Mode B is not enabled yet — its preconditions are "
                "listed above. Run mode A in the meantime.[/yellow]"
            )
            raise SystemExit(0)
        console.print(
            "[yellow]Mode B execution driver is not wired yet (needs the "
            "ehr_assistant type + a real-tool text turn endpoint).[/yellow]"
        )
        raise SystemExit(0)

    if mode != MODE_A:
        raise SystemExit(f"unknown mode: {mode!r}")

    api_base = fhir_api_base()
    if not verify_fhir_server(api_base):
        raise SystemExit(
            f"virtual EHR unreachable at {api_base}\n"
            "  docker run -d --rm -p 8080:8080 --name medagentbench-fhir "
            "jyxsu6/medagentbench:latest\n"
            "  (set MEDAGENTBENCH_FHIR_BASE if you mapped a different port)"
        )

    cases = load_cases(
        categories=[c.strip() for c in categories.split(",")] if categories else None,
        task_ids=[t.strip() for t in tasks.split(",")] if tasks else None,
        limit=limit,
    )
    if not cases:
        raise SystemExit("no tasks matched the filter")
    funcs = load_funcs()

    brain = WhissleBrain(agent_id=agent_id, model=model, system_mode=system_mode)
    writer = FhirWriter(api_base, mode=write_check)
    grade_fn = load_refsol(refsol) if refsol else builtin_grade

    console.print(
        f"[bold]MedAgentBench[/bold] mode={MODE_A} n={len(cases)} "
        f"ehr={api_base} write_check={write_check} rounds={max_round}"
    )
    if len(cases) < 300:
        console.print(
            f"[yellow]Subset run (N={len(cases)} of 300) — not directly "
            "comparable to the published table.[/yellow]"
        )

    def drive(case) -> Episode:
        ep = run_episode(
            case,
            brain=brain,
            funcs=funcs,
            api_base=api_base,
            writer=writer,
            max_round=max_round,
        )
        if ep.infra_fail and retry_infra:
            console.print(f"  [yellow]{case.id} infra_fail — retrying once[/yellow]")
            ep = run_episode(
                case,
                brain=brain,
                funcs=funcs,
                api_base=api_base,
                writer=writer,
                max_round=max_round,
                attempt=2,
            )
        return ep

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        episodes = list(pool.map(drive, cases))

    results: list[TaskResult] = []
    for ep in episodes:
        integrity = assess(
            ep.case.id,
            ep.trajectory,
            write_attempts=ep.write_attempts,
            write_check_mode=write_check,
        )
        grade = None if ep.infra_fail else grade_fn(ep.case.raw, ep.trajectory, api_base)
        results.append(TaskResult(episode=ep, grade=grade, integrity=integrity))
        mark = (
            "[yellow]infra[/yellow]"
            if ep.infra_fail
            else ("[green]PASS[/green]" if grade and grade.correct else "[red]FAIL[/red]")
        )
        console.print(f"  {ep.case.id:<12} {mark}  {ep.status}")

    if write_check == "execute" and cleanup_writes:
        created = [a for ep in episodes for a in ep.write_attempts if a.created_id]
        if created:
            n = writer.cleanup(created)
            console.print(f"cleaned up {n}/{len(created)} created resources")

    summary = summarize(
        results,
        mode=MODE_A,
        run_meta={
            **brain.describe(),
            "fhir_api_base": api_base,
            "write_check": write_check,
            "max_round": max_round,
            "grader": "refsol" if refsol else "builtin",
            "filters": {
                "limit": limit,
                "tasks": tasks,
                "categories": categories,
            },
        },
    )
    root = Path(save_to) if save_to else RESULTS_ROOT
    run_dir = write_artifacts(results, summary, root=root, run_name=run_name)

    console.print()
    console.print(
        f"[bold]Overall[/bold] {summary['overall']['correct']}/"
        f"{summary['overall']['n']} = {summary['overall']['success_rate_pct']}%  "
        f"(Query {summary['query']['success_rate_pct']}% · "
        f"Action {summary['action']['success_rate_pct']}%)  "
        f"infra_fail={summary['n_infra_fail']} excluded"
    )
    wi = summary["write_integrity"]
    console.print(
        f"[bold]Write integrity[/bold] said-but-did-not-write "
        f"{wi['said_but_did_not_write']['n']}/{wi['n_action_episodes']} "
        f"({wi['said_but_did_not_write']['rate_pct']}%) · "
        f"EHR-rejected payloads {wi['emitted_but_ehr_rejected']['n']}"
    )
    console.print(f"saved → {run_dir}")
    raise SystemExit(0)


if __name__ == "__main__":
    app()
