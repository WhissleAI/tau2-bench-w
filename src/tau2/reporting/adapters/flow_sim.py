"""Flow-sim (voice conversation-flow suite) → RunReport.

Run dir shape: ``results/whissle/flow_sim/<agent_type>/`` containing
``SUMMARY.json`` plus ``<task>_<ts>.session.json`` sidecars (and the audio).

This is the only benchmark in the set that is ours rather than a paper's, and the
only one that runs over real audio — so it is the only one where the voice signal
sections of the diagnostics block are populated, and the only one with no external
comparator by construction.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from ..model import (
    BaselineSet,
    Exclusions,
    FailureCategory,
    FailureExample,
    Judge,
    Limitation,
    Metric,
    Provenance,
    Reproduction,
    RunReport,
    Sampling,
    Table,
)
from .base import BuildContext, artifacts_for, dig, read_json, read_json_dir, wilson_ci


class FlowSimAdapter:
    benchmark = "flow_sim"
    benchmark_title = "Whissle conversation-flow suite"

    @classmethod
    def detect(cls, run_dir: Path) -> bool:
        try:
            if "flow_sim" not in str(run_dir):
                return False
            return (run_dir / "SUMMARY.json").is_file() or any(
                run_dir.glob("*.session.json")
            )
        except Exception:
            return False

    @classmethod
    def build(cls, run_dir: Path, ctx: BuildContext) -> RunReport:
        s = read_json(run_dir / "SUMMARY.json") or {}
        sessions, unreadable = read_json_dir(run_dir, "*.session.json")

        # Only the latest attempt of each task belongs in a run report — the
        # directory accumulates every historical session for the same task id.
        latest = _latest_per_task(sessions, s.get("ts"))

        warnings: list[str] = []
        status = "complete"
        partial_reason = ""
        if not s:
            status = "partial"
            partial_reason = (
                "SUMMARY.json is missing; figures were recomputed from the session "
                "sidecars, and the run boundary had to be inferred from timestamps"
            )
        if unreadable:
            status = "partial"
            warnings.append(f"{len(unreadable)} session file(s) unreadable: {unreadable[:5]}")

        detail = s.get("sessions_detail") or []
        n_sessions = s.get("sessions") or len(latest)
        n_infra = s.get("sessions_infra")
        if n_infra is None:
            n_infra = sum(1 for x in latest if dig(x, "metadata", "infra_fail"))
        n_ran = s.get("sessions_ran") or (n_sessions - n_infra)
        n_success = s.get("task_success")
        if n_success is None:
            n_success = sum(1 for x in latest if dig(x, "outcome", "task_success"))
        n_closed = s.get("sessions_ended_cleanly")
        if n_closed is None:
            n_closed = sum(1 for x in latest if dig(x, "outcome", "ended"))

        exclusions = Exclusions(
            n_total=int(n_sessions or 0),
            n_scored=int(n_ran or 0),
            n_excluded=int(n_infra or 0),
            breakdown={"infra_fail": int(n_infra)} if n_infra else {},
            reason_examples=sorted(
                {
                    str(dig(x, "metadata", "setup_error"))
                    for x in latest
                    if dig(x, "metadata", "infra_fail") and dig(x, "metadata", "setup_error")
                }
            ),
        )

        headline = Metric(
            key="task_success",
            label="Task success",
            value=round(100.0 * n_success / n_ran, 1) if n_ran else None,
            unit="pct",
            ci=wilson_ci(int(n_success or 0), int(n_ran or 0)),
            n=int(n_ran or 0),
            floor=0.0,
            ceiling=100.0,
            note="an LLM grader judges whether the caller's goal was met",
        )
        secondary = [
            Metric(
                key="clean_close",
                label="Reached a clean close",
                value=round(100.0 * n_closed / n_ran, 1) if n_ran else None,
                unit="pct",
                ci=wilson_ci(int(n_closed or 0), int(n_ran or 0)),
                n=int(n_ran or 0),
                floor=0.0,
                ceiling=100.0,
                note="taken from the authoritative `flow_end` trace event",
            )
        ]
        cov = s.get("coverage") or {}
        if cov:
            secondary += [
                Metric(
                    key="state_coverage",
                    label="Flow states visited",
                    value=cov.get("states_visited"),
                    unit="count",
                    n=cov.get("states_total"),
                    note=f"of {cov.get('states_total')} declared",
                ),
                Metric(
                    key="transition_coverage",
                    label="Flow transitions fired",
                    value=cov.get("transitions_fired"),
                    unit="count",
                    n=cov.get("transitions_total"),
                    note=f"of {cov.get('transitions_total')} declared",
                ),
            ]

        tables: list[Table] = []
        if detail:
            tables.append(
                Table(
                    key="sessions",
                    title="Per-scenario outcomes",
                    columns=[
                        "Scenario",
                        "Turns",
                        "Closed",
                        "Goal met",
                        "Final state",
                        "High-severity findings",
                    ],
                    rows=[
                        [
                            f"`{d.get('task_id')}` ({d.get('scenario', '—')})",
                            str(d.get("num_turns", "—")),
                            "yes" if d.get("ended") else "**no**",
                            "yes" if d.get("task_success") else "**no**",
                            f"`{d.get('final_state', '—')}`",
                            str(d.get("high_severity", 0)),
                        ]
                        for d in detail
                    ],
                    note=(
                        "Each row is one scripted caller persona driven over real audio. "
                        "'Closed' and 'goal met' are independent: an agent can satisfy "
                        "the caller and never hang up, or hang up having satisfied "
                        "nobody."
                    ),
                    allow_context=True,
                )
            )
        if cov:
            unfired = cov.get("transitions_unfired") or []
            tables.append(
                Table(
                    key="coverage",
                    title="Flow coverage",
                    columns=["Measure", "Covered", "Declared", "Uncovered"],
                    rows=[
                        [
                            "States",
                            str(cov.get("states_visited", "—")),
                            str(cov.get("states_total", "—")),
                            ", ".join(f"`{x}`" for x in (cov.get("states_unvisited") or []))
                            or "none",
                        ],
                        [
                            "Transitions",
                            str(cov.get("transitions_fired", "—")),
                            str(cov.get("transitions_total", "—")),
                            ", ".join(f"`{x}`" for x in unfired) or "none",
                        ],
                    ],
                    note=(
                        "An unfired transition is an untested branch. It is not a "
                        "failure — it is the part of the flow this scenario set never "
                        "reached, and therefore the part no result here speaks to."
                    ),
                    allow_context=True,
                )
            )

        first = latest[0] if latest else {}
        provenance = Provenance(
            agent_id=first.get("agent_id"),
            base_url=None,
            endpoint="LiveKit voice room (POST /api/bench/voice/start)",
            mode="voice",
            harness_commit=None,
            repo_commit=ctx.repo_commit(),
            run_dir=str(run_dir),
            captured_at=_ts_to_iso(s.get("ts") or first.get("ts")),
            dataset=f"scripted caller personas for `{s.get('agent_type') or run_dir.name}`",
            dataset_size=int(n_sessions or 0),
            upstream="internal — no published equivalent",
            extra={"agent_type": s.get("agent_type") or run_dir.name},
        )

        report = RunReport(
            run_id=ctx.run_id(run_dir),
            benchmark=cls.benchmark,
            benchmark_title=cls.benchmark_title,
            title=(
                f"Conversation-flow suite — `{s.get('agent_type') or run_dir.name}` "
                "(real-audio voice)"
            ),
            mode="voice",
            series_key=f"flow_sim:{s.get('agent_type') or run_dir.name}",
            date=provenance.captured_at or None,
            status=status,
            partial_reason=partial_reason,
            headline=headline,
            secondary_metrics=secondary,
            what_measured=(
                "Whether a deployed voice agent actually completes its job on a phone "
                "call: does it collect what the flow says it must collect, does it "
                "handle a caller who answers out of order or refuses to engage, and "
                "does it end the call cleanly rather than trailing off. Real audio, "
                "real speech recognition, real turn-taking — not a text transcript "
                "stand-in."
            ),
            why_measured=(
                "Every text benchmark in this repository removes the two things that "
                "break voice products: recognition error and turn-taking. This suite "
                "exists to measure what those two things cost, on the flows we actually "
                "ship."
            ),
            methodology=[
                (
                    "Agent under test",
                    f"the deployed `{s.get('agent_type') or run_dir.name}` agent, with "
                    "its real flow definition, prompts and tools",
                ),
                ("Mode", "real-audio voice over a LiveKit room"),
                ("Endpoint", "`POST /api/bench/voice/start` → LiveKit room"),
                (
                    "Prompt handling",
                    "none — this is the shipped configuration, unmodified; that is the "
                    "point of the suite and the reason its numbers are not comparable "
                    "to a paper's",
                ),
                (
                    "Caller",
                    "an LLM user-simulator driving a persona and a goal, speaking "
                    "through text-to-speech into the room",
                ),
                (
                    "Turn limit",
                    "a per-scenario turn budget plus a post-goal allowance; hitting the "
                    "cap is recorded as `turn_cap_exceeded`, not silently truncated",
                ),
                (
                    "Tools bound",
                    "the agent's own production tools, gated by its own flow state "
                    "machine",
                ),
                (
                    "Scoring rule",
                    "task success is judged per scenario against the caller's goal; "
                    "clean close is read from the engine's `flow_end` trace event, not "
                    "inferred from the transcript",
                ),
            ],
            scoring_rule=(
                "task success = graded goal-met / executed sessions; clean close = "
                "sessions emitting `flow_end` / executed sessions"
            ),
            tables=tables,
            exclusions=exclusions,
            judge=Judge(
                kind="rule_analyzer",
                provider=None,
                model=None,
                independent=None,
                note=(
                    "Two graders, deliberately different in kind: a rule analyzer reads "
                    "the engine's own flow trace (deterministic — it cannot be talked "
                    "into a verdict), and an LLM grader judges goal satisfaction from "
                    "the transcript. Where they disagree, the trace wins on questions "
                    "of what happened and the grader wins on questions of whether the "
                    "caller was served."
                ),
            ),
            provenance=provenance,
            sampling=Sampling(
                method="hand-authored scenario set, exhaustive (not sampled)",
                n_population=int(n_sessions or 0),
                n_requested=int(n_sessions or 0),
                n_selected=int(n_sessions or 0),
                note=(
                    "Every scenario in the set was run. There is no sampling error here "
                    "— but there is selection: the set is what we thought to write down, "
                    "and the transition-coverage table is the honest measure of what it "
                    "misses."
                ),
            ),
            baselines=BaselineSet(
                comparability_note=(
                    "There is no external comparator and there cannot be one: this suite "
                    "tests our own flow definitions on our own agents. Its value is "
                    "longitudinal — the same scenario set re-run after a change — and "
                    "the regression view in the cross-run index is where that comparison "
                    "lives, not a leaderboard."
                )
            ),
            failures=_failures(s, latest),
            limitations=[
                Limitation(
                    "tiny_n",
                    (
                        f"N = {n_ran}. At this size a single scenario flipping moves the "
                        "headline by ten points. Every figure here is directional and is "
                        "labelled PRELIMINARY for that reason."
                    ),
                    "high",
                ),
                Limitation(
                    "not_comparable",
                    (
                        "The scenario set is ours, the flows are ours, and the grader is "
                        "ours. Nothing here can be compared to any published number, and "
                        "it should never be presented alongside one as if it could."
                    ),
                    "high",
                ),
                Limitation(
                    "simulated_caller",
                    (
                        "The caller is a language model speaking through text-to-speech. "
                        "It has cleaner prosody, no background noise and more patience "
                        "than a person on a mobile in a car — so recognition error here "
                        "is a floor, not an estimate."
                    ),
                    "high",
                ),
                Limitation(
                    "coverage",
                    (
                        f"{len((s.get('coverage') or {}).get('transitions_unfired') or [])} "
                        "declared transitions never fired across the whole set. Those "
                        "branches are untested, and a green result says nothing about "
                        "them."
                    ),
                    "medium",
                ),
                Limitation(
                    "run_to_run",
                    (
                        "Speech recognition, generation and turn-taking are all "
                        "stochastic. Two runs of the same scenario set differ; a "
                        "one-scenario change between runs is noise until it repeats."
                    ),
                    "medium",
                ),
            ],
            reproduction=Reproduction(
                commands=[
                    "uv sync --extra dev --extra voice",
                    f"./run_flow_sim.sh {s.get('agent_type') or run_dir.name}",
                    (
                        "python -m tau2.reporting.cli build "
                        f"results/whissle/flow_sim/{run_dir.name}"
                    ),
                ],
                environment={
                    "repo commit at report time": provenance.repo_commit or "unknown",
                    "extras required": "voice (LiveKit, audio codecs)",
                },
                notes=[
                    "Audio is captured per session (`*.caller.wav`, `*.bot.wav`, "
                    "`*.mix.wav`) — a disputed grader verdict can be settled by "
                    "listening.",
                    "The directory accumulates every historical run of the same "
                    "scenario; this report covers the latest session per task id.",
                ],
            ),
            artifacts=artifacts_for(
                run_dir,
                [
                    ("SUMMARY.json", "run-level aggregation and coverage"),
                    ("SUMMARY.md", "the harness's own short summary"),
                    ("*.session.json", "per-session sidecar: turns, flow trace, findings"),
                    ("*.mix.wav", "the recorded call"),
                    ("REPORT.md", "this report"),
                    ("report.json", "machine-readable form of this report"),
                ],
            ),
            licence_note="",
            warnings=warnings,
        )
        return report


def _ts_to_iso(ts: Any) -> str:
    s = str(ts or "")
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return ""


def _latest_per_task(sessions: list[Any], run_ts: Any) -> list[Any]:
    """The session directory is append-only across runs; keep one per task id."""
    best: dict[str, Any] = {}
    for x in sessions:
        if not isinstance(x, dict):
            continue
        tid = str(x.get("task_id") or "?")
        cur = best.get(tid)
        if cur is None or str(x.get("ts") or "") > str(cur.get("ts") or ""):
            best[tid] = x
    return [best[k] for k in sorted(best)]


def _failures(s: dict, sessions: list[Any]) -> list[FailureCategory]:
    out: list[FailureCategory] = []
    n = len(sessions) or 1
    by_type: dict[str, list[tuple[Any, dict]]] = defaultdict(list)
    for sess in sessions:
        for f in sess.get("analyzer_findings") or []:
            if isinstance(f, dict):
                by_type[str(f.get("type"))].append((sess, f))
    for f in s.get("coverage_findings") or []:
        if isinstance(f, dict):
            by_type[str(f.get("type"))].append(({}, f))

    counts = s.get("finding_counts_by_type") or {
        k: len(v) for k, v in by_type.items()
    }
    legend = {
        "premature_termination": (
            "the flow closed the call before the intake it declares was complete — the "
            "caller was served politely and the record is short"
        ),
        "agent_no_close": (
            "the caller's goal was met and the agent never hung up; the call ends "
            "because the harness stops driving it, which on a real line is a caller "
            "waiting in silence"
        ),
        "tool_leakage": (
            "tool syntax or internal scaffolding reached the spoken channel — the caller "
            "heard machinery"
        ),
        "turn_cap_exceeded": "the turn budget ran out before the goal; the flow is too long",
        "stuck_termination": "the session stalled without a classified cause",
        "dead_end": "a final non-end state with no outgoing transition",
        "coverage": "branches the scenario set never exercised",
    }
    severity_of = {
        "agent_no_close": "high",
        "tool_leakage": "high",
        "premature_termination": "medium",
        "coverage": "info",
    }
    for ftype, count in sorted(counts.items(), key=lambda kv: -int(kv[1] or 0)):
        hits = by_type.get(ftype, [])
        out.append(
            FailureCategory(
                key=f"finding_{ftype}",
                label=f"`{ftype}`",
                count=int(count or 0),
                severity=severity_of.get(ftype, "medium"),
                denominator=n,
                description=legend.get(ftype, ""),
                examples=[
                    FailureExample(
                        case_id=str(sess.get("task_id") or "run-level"),
                        summary=(
                            f"state `{f.get('state') or '—'}`"
                            + (
                                f" · {len(sess.get('turns') or [])} turns"
                                if sess.get("turns")
                                else ""
                            )
                        ),
                        evidence=str(f.get("detail") or "")[:320],
                        artifact=(
                            f"{sess.get('task_id')}_{sess.get('ts')}.session.json"
                            if sess.get("task_id")
                            else "SUMMARY.json"
                        ),
                    )
                    for sess, f in hits[:2]
                ],
            )
        )

    unmet = [x for x in sessions if not dig(x, "outcome", "task_success")]
    if unmet:
        out.append(
            FailureCategory(
                key="goal_not_met",
                label="Caller's goal not met",
                count=len(unmet),
                severity="high",
                denominator=n,
                description=(
                    "The grader judged the caller left without what they came for. This "
                    "is the headline's complement, and the reason quoted below is the "
                    "grader's own words."
                ),
                examples=[
                    FailureExample(
                        case_id=str(x.get("task_id")),
                        summary=(
                            f"{x.get('scenario', '—')} · "
                            f"{len(x.get('turns') or [])} turns · final state "
                            f"`{dig(x, 'outcome', 'final_state', default='—')}`"
                        ),
                        evidence=str(dig(x, "outcome", "task_success_reason", default=""))[:320],
                        artifact=f"{x.get('task_id')}_{x.get('ts')}.session.json",
                    )
                    for x in unmet[:3]
                ],
            )
        )
    return out
