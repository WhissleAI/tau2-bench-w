# Copyright Sierra
"""Flow-edit sensitivity suite — RUNNER, reporting, CLI.

Proves the STUDIO EDIT → PUBLISH → RUNTIME chain end-to-end, per the owner's brief:
*"for an agent like headache, test the conversation by making changes to each step
in the conversation flow and check that the bench picks those changes up — that
proves that when a user edits a flow and publishes it, the live conversation has
the required changes."*

For every mutation in the matrix (``mutations.build_mutations`` — one targeted edit
per step-kind: say text, conversation goal, transition condition/target, tool
gating, state removal, set_variable + expression edge) the runner walks the SAME
path a studio user walks, against a fresh throwaway agent:

  1. create a ``flowsim-*`` agent of ``--agent-type`` (backend auto-attaches the
     type's default flow) and read the baseline flow back;
  2. dry-run the mutated flow through ``POST /flow/validate`` (the studio's
     validator — a mutation must be a legal edit);
  3. stage it as a DRAFT (``PATCH ?target=draft``) and assert the draft is staged
     (``GET ?include=draft``) while the LIVE flow is byte-identical to baseline —
     and (for cheap probes) that a live conversation still shows NO trace of the
     edit: **draft-only must not change the live conversation**;
  4. ``POST /publish`` and assert the live flow now carries the edit: **publish
     must**;
  5. drive a short scripted probe conversation (2–6 turns, text by default; the
     text channel runs the identical FlowRuntime the voice pipeline runs) and run
     the mutation's deterministic CHECK against the transcript + the persisted
     flow step-trace — optionally repeating the probe over the REAL voice pipeline
     (``--mode voice``, or ``--voice-spot-checks`` for one mutation per step-kind),
     where a say-sentinel is additionally verified in the bot-audio re-ASR;
  6. ALWAYS delete the agent (``confirm=true``), even on error.

A mutation PASSES only if every executed phase holds. The report (per-mutation
PASS/FAIL table with expected vs observed) lands under
``results/whissle/flow_mutation/<agent_type>/`` as ``REPORT.md`` + ``report.json``
plus a per-mutation evidence JSON; the process exits non-zero when any mutation
fails — a failing row is a real product bug: an edit a user made in the studio
that did NOT propagate to the live conversation.

Usage:
    python -m tau2.flow.mutation_suite plan --agent-type headache_enrollment
    python -m tau2.flow.mutation_suite run  --agent-type headache_enrollment
    python -m tau2.flow.mutation_suite run  --agent-type headache_enrollment --mode voice
    python -m tau2.flow.mutation_suite run  --agent-type headache_enrollment --voice-spot-checks
    python -m tau2.flow.mutation_suite run  --agent-type headache_enrollment --mutation say_sentinel_greet
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from typer import Option, Typer

from tau2.flow.client import FlowClient, FlowClientError
from tau2.flow.mutations import (
    CheckResult,
    Mutation,
    ProbeResult,
    Skip,
    build_mutations,
    voice_spot_subset,
)

app = Typer(add_completion=False)
console = Console()

RESULTS_ROOT = Path("results/whissle/flow_mutation")
PROBES_FIXTURE = Path("data/flow/mutation_probes.json")
AGENT_PREFIX = "flowsim-"          # the existing cleanup sweep catches this prefix
DEFAULT_SYSTEM_PROMPT = (
    "You are the agent under test. Follow your configured behavior and the "
    "current conversation-flow state instructions exactly.")


# ── fixtures ────────────────────────────────────────────────────────────────────

def probe_lines_for(agent_type: str) -> dict[str, str]:
    """Per-type overrides for the scripted probe lines (optional fixture)."""
    if not PROBES_FIXTURE.exists():
        return {}
    d = json.loads(PROBES_FIXTURE.read_text(encoding="utf-8"))
    return dict(d.get("types", {}).get(agent_type, {}))


def system_prompt_for(agent_type: str) -> str:
    """Reuse the flow-sim fixture's per-type system prompt when it has one."""
    try:
        from tau2.flow.simulate import system_prompt_for as _sim_sp
        return _sim_sp(agent_type)
    except Exception:  # noqa: BLE001 — type not in sim fixture / fixture absent
        return DEFAULT_SYSTEM_PROMPT


# ── helpers ─────────────────────────────────────────────────────────────────────

def _flows_equal(a: Any, b: Any) -> bool:
    """Order-insensitive deep equality via canonical JSON."""
    try:
        return (json.dumps(a, sort_keys=True, ensure_ascii=False)
                == json.dumps(b, sort_keys=True, ensure_ascii=False))
    except (TypeError, ValueError):
        return a == b


def _phase(passed: Optional[bool], expected: str, observed: str) -> dict[str, Any]:
    """One phase verdict. ``passed=None`` marks a phase SKIPPED / not applicable
    (it neither passes nor fails the mutation)."""
    return {"passed": passed, "expected": expected, "observed": observed}


def _phase_from(cr: CheckResult) -> dict[str, Any]:
    return _phase(cr.passed, cr.expected, cr.observed)


# ── probes ──────────────────────────────────────────────────────────────────────

def run_probe_text(client: FlowClient, agent_id: str,
                   script: list[str]) -> ProbeResult:
    """Drive the scripted lines over ``POST /chat/turn`` (the deterministic text
    channel — the same FlowRuntime the voice pipeline executes), then pull the
    full accumulated step-trace."""
    turns: list[dict[str, Any]] = []
    conv_id: Optional[str] = None
    for i, msg in enumerate(script, start=1):
        res = client.turn(agent_id, msg, conversation_id=conv_id)
        conv_id = res.conversation_id or conv_id
        turns.append({"n": i, "user_msg": msg, "reply": res.reply,
                      "current_state": res.current_state,
                      "tools_used": res.tools_used, "steps": res.steps,
                      "ended": bool(res.raw.get("ended"))})
        if res.raw.get("ended"):
            break
    trace: list[dict[str, Any]] = []
    if conv_id:
        try:
            tr = client.get_trace(agent_id, conv_id)
            trace = list((tr or {}).get("steps") or [])
        except FlowClientError:
            pass
    if not trace:  # degrade to the per-turn incremental steps
        for t in turns:
            trace.extend(t["steps"])
    trace.sort(key=lambda s: s.get("seq", 0))
    return ProbeResult(turns=turns, trace=trace)


def run_probe_voice(client: FlowClient, agent_id: str, script: list[str],
                    out_prefix: str) -> ProbeResult:
    """Drive the scripted lines over the REAL voice pipeline (STT → flow-brain →
    TTS over LiveKit) via :class:`VoiceTransport`; capture the greeting, the
    spoken transcript, the persisted voice step-trace, and an independent re-ASR
    of the captured bot audio (say-sentinel evidence)."""
    from tau2.flow.voice_transport import VoiceTransport

    vt = VoiceTransport(agent_id)
    turns: list[dict[str, Any]] = []
    greeting = ""
    bot_reasr: Optional[str] = None
    trace: list[dict[str, Any]] = []
    try:
        greeting = vt.start()
        for i, msg in enumerate(script, start=1):
            res = vt.turn(msg)
            turns.append({"n": i, "user_msg": msg, "reply": res.reply,
                          "current_state": None, "tools_used": [], "steps": [],
                          "ended": False})
        conv_id = vt.conversation_id
        if conv_id:
            for _ in range(5):  # voice persists the trace asynchronously
                try:
                    tr = client.get_trace(agent_id, conv_id)
                    trace = list((tr or {}).get("steps") or [])
                except FlowClientError:
                    break
                if trace:
                    break
                time.sleep(2.0)
        try:
            evidence = vt.finish(out_prefix, transcribe=True)
            reasr = evidence.get("bot_reasr")
            if isinstance(reasr, dict):
                bot_reasr = reasr.get("text") or json.dumps(reasr)
            elif reasr is not None:
                bot_reasr = str(reasr)
        except Exception:  # noqa: BLE001 — audio evidence is best-effort
            pass
    finally:
        try:
            vt.stop()
        except Exception:  # noqa: BLE001
            pass
    trace.sort(key=lambda s: s.get("seq", 0))
    return ProbeResult(turns=turns, trace=trace, greeting=greeting,
                       bot_reasr=bot_reasr)


# ── one mutation, end to end ────────────────────────────────────────────────────

def run_mutation(
    client: FlowClient, mutation: Mutation, agent_type: str, system_prompt: str, *,
    mode: str = "text", draft_behavior_probe: bool = False,
    voice_probe: bool = False, keep_agent: bool = False, out_dir: Path,
) -> dict[str, Any]:
    """Create → mutate via the studio path (draft → publish) → probe → delete.

    Returns the mutation's result record (also written to ``out_dir``); its
    ``passed`` is True only when every executed phase held."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    phases: dict[str, dict[str, Any]] = {}
    agent_id: Optional[str] = None
    probe_capture: dict[str, Any] = {}
    error: Optional[str] = None

    try:
        # 1 ── create + baseline flow ───────────────────────────────────────────
        agent = client.create_typed_agent(
            f"{AGENT_PREFIX}mut-{mutation.id[:40]}", agent_type, system_prompt)
        agent_id = agent["id"]
        baseline: dict[str, Any] = {}
        for _ in range(5):  # auto-attach can lag a beat
            got = client.get_agent(agent_id)
            if isinstance(got.get("flow"), dict):
                baseline = got["flow"]
                break
            time.sleep(3.0)
        phases["baseline_flow_attached"] = _phase(
            bool(baseline), "type default flow auto-attached on create",
            f"flow with {len(baseline.get('states') or [])} states" if baseline
            else "no flow attached")
        if not baseline:
            raise FlowClientError("baseline", 0, "no default flow attached")

        mutated = mutation.apply(baseline)

        # 2 ── studio validator dry-run ─────────────────────────────────────────
        try:
            v = client.validate_flow(agent_id, mutated)
            phases["validate_ok"] = _phase(
                bool(v.get("valid")), "mutated flow passes POST /flow/validate",
                f"valid={v.get('valid')} errors={v.get('errors') or []} "
                f"warnings={len(v.get('warnings') or [])}")
        except FlowClientError as e:
            # An older deploy without the endpoint must not fail the suite —
            # the live PATCH below runs the identical validator anyway.
            phases["validate_ok"] = _phase(
                None if e.status == 404 else False,
                "mutated flow passes POST /flow/validate", str(e))

        # 3 ── stage as DRAFT; live must be untouched ───────────────────────────
        client.set_flow(agent_id, mutated, target="draft")
        with_draft = client.get_agent(agent_id, include="draft")
        draft_flow = (with_draft.get("draft") or {}).get("flow")
        phases["draft_staged"] = _phase(
            bool(with_draft.get("has_draft")) and _flows_equal(draft_flow, mutated),
            "PATCH ?target=draft stages the edit in the pending overlay",
            f"has_draft={with_draft.get('has_draft')} "
            f"draft_matches_mutation={_flows_equal(draft_flow, mutated)}")
        live_now = client.get_agent(agent_id).get("flow")
        phases["live_unchanged_while_draft"] = _phase(
            _flows_equal(live_now, baseline),
            "the LIVE flow is byte-identical to baseline while the edit is "
            "draft-only",
            "live flow unchanged" if _flows_equal(live_now, baseline)
            else "LIVE FLOW CHANGED by a draft-target PATCH (draft leaked!)")

        # 3b ── behavioral draft inertness (optional; cheap probes only) ────────
        # Runs on its OWN throwaway agent, never the main one. The studio text
        # channel resumes the caller's open thread per agent (conversations
        # open_or_resume on external_id "studio:<user>"), so a pre-publish probe
        # on the main agent would leave an open conversation that the
        # post-publish probe silently RESUMES mid-flow — past states (e.g. the
        # mutated greeting) would never replay and the pickup check would fail
        # for a transport reason, not a product one. A dedicated agent gives the
        # draft-phase conversation its own thread and keeps the main agent's
        # post-publish probe pristine.
        if (draft_behavior_probe and mutation.draft_probe
                and mutation.draft_check):
            draft_aid = None
            try:
                d_agent = client.create_typed_agent(
                    f"{AGENT_PREFIX}mutd-{mutation.id[:39]}", agent_type,
                    system_prompt)
                draft_aid = d_agent["id"]
                d_base: dict[str, Any] = {}
                for _ in range(5):
                    got = client.get_agent(draft_aid)
                    if isinstance(got.get("flow"), dict):
                        d_base = got["flow"]
                        break
                    time.sleep(3.0)
                client.set_flow(draft_aid, mutation.apply(d_base),
                                target="draft")
                pr = run_probe_text(client, draft_aid, mutation.draft_probe)
                probe_capture["draft_probe"] = _capture(pr)
                phases["draft_behavior_inert"] = _phase_from(
                    mutation.draft_check(pr))
            finally:
                if draft_aid and not keep_agent:
                    try:
                        client.delete_agent(draft_aid, confirm=True)
                    except Exception as e:  # noqa: BLE001
                        phases["cleanup_draft_agent"] = _phase(
                            False, "draft-probe agent deleted",
                            f"delete failed: {e}")

        # 4 ── PUBLISH; live must now carry the edit ────────────────────────────
        client.publish(agent_id)
        live_flow = client.get_agent(agent_id).get("flow")
        phases["published"] = _phase(
            _flows_equal(live_flow, mutated),
            "POST /publish promotes the draft: live flow == mutated flow",
            "live flow matches the mutation" if _flows_equal(live_flow, mutated)
            else "live flow does NOT match the published mutation")

        # 5 ── behavioral probe(s) ──────────────────────────────────────────────
        if mode == "voice":
            pr = run_probe_voice(client, agent_id, mutation.probe,
                                 str((out_dir / f"{mutation.id}_{ts}").resolve()))
            probe_capture["probe"] = _capture(pr)
            phases["behavior_voice"] = _phase_from(mutation.check(pr))
        else:
            pr = run_probe_text(client, agent_id, mutation.probe)
            probe_capture["probe"] = _capture(pr)
            phases["behavior"] = _phase_from(mutation.check(pr))
            if voice_probe:
                prv = run_probe_voice(
                    client, agent_id, mutation.probe,
                    str((out_dir / f"{mutation.id}_{ts}_voice").resolve()))
                probe_capture["voice_probe"] = _capture(prv)
                phases["behavior_voice"] = _phase_from(mutation.check(prv))

    except FlowClientError as e:
        error = str(e)
        phases["error"] = _phase(False, "no API error during the mutation cycle",
                                 error)
    except Exception as e:  # noqa: BLE001
        error = f"{type(e).__name__}: {e}"
        phases["error"] = _phase(False, "no error during the mutation cycle", error)
    finally:
        deleted = False
        if agent_id and not keep_agent:
            try:
                client.delete_agent(agent_id, confirm=True)
                deleted = True
            except Exception as e:  # noqa: BLE001
                phases["cleanup"] = _phase(False, "throwaway agent deleted",
                                           f"delete failed: {e}")

    executed = [p for p in phases.values() if p["passed"] is not None]
    passed = bool(executed) and all(p["passed"] for p in executed)
    result = {
        "mutation": mutation.id, "kind": mutation.kind, "target": mutation.target,
        "description": mutation.description,
        "expected_signal": mutation.expected_signal,
        "ts": ts, "agent_id": agent_id, "agent_deleted": deleted or keep_agent,
        "mode": mode, "passed": passed, "error": error,
        "phases": phases, "capture": probe_capture,
    }
    (out_dir / f"{mutation.id}_{ts}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")
    return result


def _capture(pr: ProbeResult) -> dict[str, Any]:
    return {"greeting": pr.greeting or None,
            "turns": [{k: t.get(k) for k in
                       ("n", "user_msg", "reply", "current_state", "tools_used",
                        "ended")} for t in pr.turns],
            "states_entered": pr.states_entered,
            "trace_steps": len(pr.trace),
            "trace": pr.trace,
            "bot_reasr": pr.bot_reasr}


# ── reporting ───────────────────────────────────────────────────────────────────

_PHASE_ORDER = ["baseline_flow_attached", "validate_ok", "draft_staged",
                "live_unchanged_while_draft", "draft_behavior_inert", "published",
                "behavior", "behavior_voice", "cleanup", "cleanup_draft_agent",
                "error"]


def _fmt_phase(p: Optional[dict]) -> str:
    if p is None:
        return "—"
    if p["passed"] is None:
        return "skip"
    return "PASS" if p["passed"] else "**FAIL**"


def write_report(agent_type: str, mode: str, results: list[dict],
                 skips: list[Skip], out_dir: Path) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    n_pass = sum(1 for r in results if r["passed"])
    lines = [
        f"# Flow-edit sensitivity report — `{agent_type}` ({mode} mode)",
        "",
        f"- **run**: {ts}  •  **base**: studio API path "
        f"(`PATCH ?target=draft` → `POST /publish`)  •  **probe transport**: {mode}",
        f"- **mutations**: {len(results)}  •  **picked up by the live "
        f"conversation**: {n_pass}/{len(results)}",
        "",
        "A FAIL row is a product bug: an edit made through the same API the "
        "flow-designer UI uses that did **not** manifest in the live conversation "
        "(or a draft that leaked before publish).",
        "",
        "| mutation | kind | target | draft staged | live inert (draft) | "
        "published | behavior | voice | verdict |",
        "|----------|------|--------|--------------|--------------------|"
        "-----------|----------|-------|---------|",
    ]
    for r in results:
        ph = r["phases"]
        draft_inert = ph.get("draft_behavior_inert") or ph.get(
            "live_unchanged_while_draft")
        behavior = ph.get("behavior") or (
            ph.get("behavior_voice") if mode == "voice" else None)
        lines.append(
            f"| `{r['mutation']}` | {r['kind']} | `{r['target']}` | "
            f"{_fmt_phase(ph.get('draft_staged'))} | {_fmt_phase(draft_inert)} | "
            f"{_fmt_phase(ph.get('published'))} | {_fmt_phase(behavior)} | "
            f"{_fmt_phase(ph.get('behavior_voice')) if mode != 'voice' else '—'} | "
            f"{'PASS' if r['passed'] else '**FAIL**'} |")
    lines += ["", "## Expected vs observed", ""]
    for r in results:
        lines += [f"### `{r['mutation']}` — "
                  f"{'PASS' if r['passed'] else 'FAIL'}",
                  "",
                  f"- **edit**: {r['description']}",
                  f"- **expected signal**: {r['expected_signal']}"]
        for name in _PHASE_ORDER:
            p = r["phases"].get(name)
            if p is None:
                continue
            mark = ("skipped" if p["passed"] is None
                    else ("ok" if p["passed"] else "FAILED"))
            lines.append(f"- `{name}` [{mark}]: {p['observed']}")
        if r.get("error"):
            lines.append(f"- **error**: {r['error']}")
        lines.append("")
    if skips:
        lines += ["## Mutation kinds not applicable to this flow", ""]
        for s in skips:
            lines.append(f"- `{s.kind}`: {s.reason}")
        lines.append("")
    out_dir.mkdir(parents=True, exist_ok=True)
    md = out_dir / "REPORT.md"
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "report.json").write_text(json.dumps({
        "agent_type": agent_type, "mode": mode, "ts": ts,
        "mutations": len(results), "passed": n_pass,
        "failed": [r["mutation"] for r in results if not r["passed"]],
        "skipped_kinds": [{"kind": s.kind, "reason": s.reason} for s in skips],
        "results": results,
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return md


# ── CLI ─────────────────────────────────────────────────────────────────────────

def _fetch_baseline_flow(client: FlowClient, agent_type: str,
                         system_prompt: str) -> dict:
    """One throwaway agent solely to read the type's default flow for planning."""
    aid = None
    try:
        agent = client.create_typed_agent(
            f"{AGENT_PREFIX}mutplan-{agent_type}", agent_type, system_prompt)
        aid = agent["id"]
        for _ in range(5):
            got = client.get_agent(aid)
            if isinstance(got.get("flow"), dict):
                return got["flow"]
            time.sleep(3.0)
        return {}
    finally:
        if aid:
            try:
                client.delete_agent(aid, confirm=True)
            except Exception:  # noqa: BLE001
                pass


@app.command()
def plan(
    agent_type: str = Option("headache_enrollment", help="seeded agent type"),
    fixture: Optional[str] = Option(
        None, help="plan offline from a local flow.json instead of the live API"),
) -> None:
    """Print the mutation matrix for a flow without running anything."""
    if fixture:
        flow = json.loads(Path(fixture).read_text(encoding="utf-8"))
    else:
        client = FlowClient()
        flow = _fetch_baseline_flow(client, agent_type,
                                    system_prompt_for(agent_type))
    muts, skips = build_mutations(flow, probe_lines_for(agent_type))
    console.print(f"[bold]{len(muts)} mutations[/bold] for {agent_type}:")
    for m in muts:
        spot = "  [cyan](voice spot-check)[/cyan]" if m.voice_spot else ""
        console.print(f"  [bold]{m.id}[/bold]  kind={m.kind} target={m.target}{spot}")
        console.print(f"    edit: {m.description}")
        console.print(f"    expect: {m.expected_signal}  probe={len(m.probe)} turns")
    for s in skips:
        console.print(f"  [dim]skipped kind={s.kind}: {s.reason}[/dim]")


@app.command()
def run(
    agent_type: str = Option("headache_enrollment", help="seeded agent type"),
    mode: str = Option("text", help="probe transport for the full matrix: 'text' "
                       "(deterministic; same FlowRuntime) or 'voice' (real "
                       "STT→brain→TTS pipeline)"),
    voice_spot_checks: bool = Option(
        False, "--voice-spot-checks",
        help="text mode only: additionally re-probe one mutation per step-kind "
             "over the real voice pipeline"),
    mutation: Optional[str] = Option(
        None, help="run only these mutation id(s), comma-separated"),
    draft_probe: str = Option(
        "default", help="behavioral draft-inertness probe: 'default' (cheap "
        "1-turn probes only), 'all', or 'none' — structural draft assertions "
        "always run"),
    keep_agent: bool = Option(False, help="do NOT delete throwaway agents (debug)"),
) -> None:
    """Run the flow-edit sensitivity matrix against the LIVE backend."""
    if mode not in ("text", "voice"):
        console.print(f"[red]--mode must be 'text' or 'voice', got {mode!r}[/red]")
        raise SystemExit(2)
    if draft_probe not in ("default", "all", "none"):
        console.print("[red]--draft-probe must be default|all|none[/red]")
        raise SystemExit(2)
    client = FlowClient()
    who = client.whoami()
    console.print(
        f"[bold]flow-mutation[/bold]  org={who.get('organization', {}).get('name')!r} "
        f"type={agent_type}  base={client.base}  mode={mode}")

    system_prompt = system_prompt_for(agent_type)
    baseline = _fetch_baseline_flow(client, agent_type, system_prompt)
    if not baseline:
        console.print(f"[red]agent type {agent_type!r} attaches no default flow — "
                      f"nothing to mutate[/red]")
        raise SystemExit(2)
    muts, skips = build_mutations(baseline, probe_lines_for(agent_type))
    if mutation:
        wanted = {m.strip() for m in mutation.split(",") if m.strip()}
        missing = wanted - {m.id for m in muts}
        if missing:
            console.print(f"[red]no such mutation id(s): {sorted(missing)}[/red]")
            raise SystemExit(2)
        muts = [m for m in muts if m.id in wanted]
    spot_ids = {m.id for m in voice_spot_subset(muts)} if voice_spot_checks else set()

    out_dir = RESULTS_ROOT / agent_type
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    for i, m in enumerate(muts, start=1):
        console.print(f"\n[bold cyan]▶ mutation {i}/{len(muts)}[/bold cyan]  "
                      f"{m.id}  ({m.kind} on {m.target})")
        t0 = time.time()
        res = run_mutation(
            client, m, agent_type, system_prompt, mode=mode,
            draft_behavior_probe=(
                draft_probe == "all"
                or (draft_probe == "default" and len(m.draft_probe or []) <= 1)),
            voice_probe=(mode == "text" and m.id in spot_ids),
            keep_agent=keep_agent, out_dir=out_dir)
        results.append(res)
        verdict = "[green]PASS[/green]" if res["passed"] else "[red]FAIL[/red]"
        beh = (res["phases"].get("behavior")
               or res["phases"].get("behavior_voice") or {})
        console.print(f"    {verdict}  ({time.time() - t0:.1f}s)  "
                      f"{beh.get('observed', res.get('error') or '')}")

    md = write_report(agent_type, mode, results, skips, out_dir)
    n_fail = sum(1 for r in results if not r["passed"])
    console.print(f"\n[bold]done[/bold]  mutations={len(results)}  "
                  f"picked_up={len(results) - n_fail}  failed={n_fail}")
    console.print(f"report: {md}")
    if n_fail:
        console.print("[red]FAILURES are product bugs: studio edits that did not "
                      "reach the live conversation.[/red]")
    raise SystemExit(1 if n_fail else 0)


if __name__ == "__main__":
    app()
