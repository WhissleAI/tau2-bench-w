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
     … until the agent flow ``ended``, the user is done (the AGENT closed / refused —
     goal-met alone does NOT stop the sim: it keeps cooperating for a small post-goal
     allowance so the agent can deliver its closing), the post-goal allowance is
     exhausted, or the per-task turn budget (fixture-declared, default 24) is hit.
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
from tau2.flow.client import FlowClient, FlowClientError
from tau2.flow.usersim import (
    ModelError,
    Task,
    UserSimulator,
    WhissleModel,
    judge_goal_drift,
    judge_task_success,
)
from tau2.flow.voice_transport import VoiceInfraError, VoiceTransport

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
                # Turn budget resolution (most specific wins):
                #   task.max_turns  >  type block max_turns  >  fixture top-level (24).
                # One-size-fits-all was a measurement confound: a 10-state intake flow
                # (headache_enrollment) needs far more spoken turns than a 3-question
                # booking, so a global cap under-budgets long flows (false
                # "never ended") and over-budgets short ones. Declare the budget where
                # the flow length is known — per type / per task in sim_tasks.json.
                # (24 remains the top-level fallback; 14 was too low for VOICE, the
                # dominant `ended=False` finding in the 2026-08-04 5x5 voice bench.)
                # Override per-run with --max-turns.
                "max_turns": block.get("max_turns", d.get("max_turns", 24)),
                # Post-goal allowance: cooperative turns the sim grants the agent to
                # deliver its closing after the goal is met (same resolution order).
                "post_goal_turns": block.get("post_goal_turns",
                                             d.get("post_goal_turns", 4))}
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


def _signals_summary(turns: list[dict]) -> dict:
    """Aggregate the per-turn meta-signal capture across a session.

    ``total`` frames, ``by_kind`` counts per producer (hesitation / shadow /
    speculative), ``turns_with_signals`` coverage, and ``hesitation_turns`` — the
    turns where the hesitation predictor fired (the non-verbal "confidence of a yes"
    read). All zero on the text channel (no signals emitted), so a summary that reads
    total=0 over voice is the tell that SIGNAL_EMIT / the metadata GPU is not live."""
    total = 0
    by_kind: Counter = Counter()
    turns_with = 0
    hesitation_turns: list[int] = []
    # Raw whissle-large metadata coverage across the session.
    meta_frames = 0
    meta_turns = 0
    emotions: Counter = Counter()
    intents: Counter = Counter()
    for t in turns:
        sigs = t.get("signals") or []
        if sigs:
            turns_with += 1
        for s in sigs:
            total += 1
            k = s.get("signal")
            if k:
                by_kind[k] += 1
            if k == "hesitation":
                hesitation_turns.append(t.get("n"))
        md = t.get("user_metadata") or []
        if md:
            meta_turns += 1
            meta_frames += len(md)
        fin = t.get("metadata_final") or {}
        if fin.get("emotion"):
            emotions[fin["emotion"]] += 1
        if fin.get("intent"):
            intents[fin["intent"]] += 1
    return {
        "total": total,
        "by_kind": dict(by_kind),
        "turns_with_signals": turns_with,
        "hesitation_turns": sorted(set(x for x in hesitation_turns if x is not None)),
        # Raw acoustic-metadata coverage: per-interim frame count, turns with any
        # metadata, and the emotion/intent label mix the whissle-large head reported.
        "metadata_frames": meta_frames,
        "turns_with_metadata": meta_turns,
        "emotions_seen": dict(emotions),
        "intents_seen": dict(intents),
    }


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


def _pctl(vals: list, q: float) -> Optional[int]:
    """Nearest-rank percentile of a numeric list (None on empty)."""
    vs = sorted(v for v in vals if v is not None)
    if not vs:
        return None
    k = min(len(vs) - 1, max(0, round(q * (len(vs) - 1))))
    return vs[k]


def _is_infra_error(exc: BaseException) -> bool:
    """Infrastructure failure (transport / provider / credit / connectivity) —
    the session could not be measured; NOT a flow bug of the agent under test."""
    import requests as _requests

    return isinstance(exc, (VoiceInfraError, ModelError,
                            _requests.RequestException, TimeoutError))


# ── one simulated session ────────────────────────────────────────────────────────

def run_session(
    client: FlowClient, model: WhissleModel, task: Task, system_prompt: str, *,
    semantic: bool = True, keep_agent: bool = False, mode: str = "text",
    attempt: int = 1,
) -> dict[str, Any]:
    """Create the agent, drive a full simulated conversation, analyze, ALWAYS clean
    up. Returns a JSON-serializable session result (also written to disk).

    ``mode`` selects the TRANSPORT the conversation is driven over:
      * ``"text"`` — ``POST /chat/turn`` (deterministic; full retrievable step-trace).
      * ``"voice"`` — the real voice pipeline over LiveKit (STT→flow-brain→TTS), via
        :class:`VoiceTransport`. Same user-sim + judges; the agent's spoken transcript
        (RTVI ``bot-transcription``) is the scored text. Since PR #613 the voice flow
        step-trace IS persisted (``voice/start`` returns a real conversations id and the
        pipeline writes the trace), so ``GET /flow/trace`` returns the VOICE steps and
        the deterministic state-trace analyzer runs on them identically to text. Per-turn
        emotion/intent + hesitation signals are captured off the data channel, and duplex
        audio (caller/bot/mix WAVs) is captured as evidence. If a deploy hasn't rolled the
        trace persistence yet, it degrades honestly to ``voice_trace_unavailable``. See
        WHISSLE_VOICE_TESTING.md."""
    voice = mode == "voice"
    if voice:
        # No per-turn flow state is exposed over voice, so the goal-drift judge (which
        # grades against the active state's goal) has nothing to key on — disable it.
        semantic = False
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
    vt: Optional[VoiceTransport] = None
    greeting: str = ""
    audio_evidence: dict[str, Any] = {}
    # Termination bookkeeping for the analyzer's agent_no_close / turn_cap_exceeded
    # classification (see analyze._add_no_end_finding).
    goal_met_turn: Optional[int] = None    # turn whose agent reply satisfied the goal
    empty_reply_turns: list[int] = []      # turns where the agent replied EMPTY
    turn_cap_hit = False
    end_reason: Optional[str] = None
    sim: Optional[UserSimulator] = None
    infra_fail = False  # infrastructure failure — bucketed out of flow metrics

    _emit({"event": "session_start", "task_id": task.id, "agent_type": task.agent_type,
           "scenario": task.scenario, "persona": task.persona, "goal": task.goal, "ts": ts})

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

        # VOICE transport: open the real spoken session AFTER the flow is attached, so
        # the pipeline builds the FlowController. The agent greets first (a real
        # answered call); that greeting is the first thing the caller reacts to.
        if voice:
            vt = VoiceTransport(agent_id)
            greeting = vt.start()
            _emit({"event": "voice_session", "room": vt.room, "greeting": greeting})

        sim = UserSimulator(task=task, model=model)
        _t_llm = time.monotonic()
        user_msg = sim.first_utterance()
        if voice and vt is not None:
            vt.note_llm_ms(round((time.monotonic() - _t_llm) * 1000))

        for i in range(1, task.max_turns + 1):
            if voice:
                assert vt is not None
                res = vt.turn(user_msg, conversation_id=conv_id)
            else:
                res = client.turn(agent_id, user_msg, conversation_id=conv_id)
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

            # Per-turn meta-signals (hesitation / shadow / speculative predictions
            # from the whissle-large metadata head) emitted over the voice data channel
            # this turn. Text channel carries none → []. This is what makes the bench
            # exercise the meta-signal layer, not just flow + transcript.
            turn_signals = res.raw.get("signals") or []
            turn_metadata = res.raw.get("user_metadata") or []
            rec = {
                "n": i, "user_msg": user_msg, "agent_reply": res.reply,
                "ended": ended, "current_state": res.current_state,
                "tools_used": res.tools_used,
                "tool_calls_in_reply": _tool_calls_from_reply(res.reply),
                "engine_turn": eng_turn, "steps": res.steps,
                "drift": drift,
                "signals": turn_signals,
                "signal_kinds": sorted({s.get("signal") for s in turn_signals if s.get("signal")}),
                # Raw whissle-large acoustic metadata frames (emotion/intent/age/gender +
                # probs) pushed per interim+final this turn, and the last (settled) one.
                "user_metadata": turn_metadata,
                "metadata_final": turn_metadata[-1] if turn_metadata else None,
                "hesitant_input": bool(res.raw.get("hesitant_input")),
                # Sim-reply latency breakdown: bot-turn-final → the sim STARTS
                # publishing its reply audio (wait/LLM/TTS components). Voice only.
                "sim_reply": res.raw.get("sim_reply"),
            }
            turns.append(rec)
            _emit({"event": "turn", **rec})

            if not (res.reply or "").strip():
                empty_reply_turns.append(i)

            # Transport says the transcript surface has DIED (bot audio flows but no
            # transcript events, streak survived a handshake retry) — driving more
            # turns measures nothing. Stop and classify the session infra_fail.
            if voice and res.raw.get("transcript_dead"):
                end_reason = "transcript_dead"
                infra_fail = True
                _emit({"event": "conversation_end", "reason": end_reason, "turn": i})
                break

            if ended or sim.done:
                end_reason = "flow_ended" if ended else "user_done"
                _emit({"event": "conversation_end", "reason": end_reason, "turn": i})
                break
            # Drive-through-closing: once the sim's goal is met it keeps cooperating
            # (acknowledgements / "no, that's all" / returning the goodbye) so the
            # AGENT gets a fair chance to deliver its closing and reach flow_end —
            # but only up to task.post_goal_turns extra turns. Exhausting the
            # allowance without a close is the agent's failure (agent_no_close), not
            # the sim hanging up early.
            if (goal_met_turn is not None
                    and i - goal_met_turn >= task.post_goal_turns):
                end_reason = "post_goal_allowance_exhausted"
                _emit({"event": "conversation_end", "reason": end_reason, "turn": i,
                       "goal_met_turn": goal_met_turn,
                       "post_goal_turns": task.post_goal_turns})
                break
            _t_llm = time.monotonic()
            user_msg = sim.next_utterance(res.reply)
            if voice and vt is not None:
                vt.note_llm_ms(round((time.monotonic() - _t_llm) * 1000))
            if sim.goal_met and goal_met_turn is None:
                goal_met_turn = i  # the goal was satisfied as of turn i's reply
                _emit({"event": "goal_met", "turn": i})
        else:
            end_reason = "turn_cap"
            turn_cap_hit = True
            _emit({"event": "conversation_end", "reason": "turn_cap",
                   "turn": task.max_turns})

        # Full accumulated trace (authoritative for the analyzer). As of PR #613 the
        # voice pipeline persists its step-trace too: real-mode voice/start creates a
        # conversations row (its id threaded onto conv_id via the VoiceTransport), so
        # GET /flow/trace returns the VOICE flow steps. If the backend hasn't persisted
        # yet (deploy still rolling), full_steps stays [] and the voice branch below
        # degrades honestly to a voice_trace_unavailable finding.
        if agent_id and conv_id:
            # Voice persists at turn boundaries/call-end asynchronously, so the last
            # turn's steps can lag a beat — retry briefly before giving up (text is
            # synchronous and returns on the first try).
            attempts = 5 if voice else 1
            for att in range(1, attempts + 1):
                try:
                    tr = client.get_trace(agent_id, conv_id)
                    full_steps = list((tr or {}).get("steps") or [])
                except FlowClientError as e:
                    _emit({"event": "trace_fetch_failed", "detail": str(e)})
                    break
                if full_steps or not voice:
                    break
                time.sleep(2.0)
            if voice:
                _emit({"event": "voice_trace_fetch", "conversation_id": conv_id,
                       "num_steps": len(full_steps), "attempts": att})

        # Voice: capture the duplex audio (real spoken-session evidence) + re-ASR.
        if voice and vt is not None:
            try:
                prefix = str((out_dir / f"{task.id}_{ts}").resolve())
                audio_evidence = vt.finish(prefix, transcribe=True)
                _emit({"event": "voice_audio", **{k: v for k, v in audio_evidence.items()
                                                  if k in ("bot", "caller", "mix",
                                                           "latencies_ms", "bot_reasr")}})
            except Exception as e:  # noqa: BLE001
                _emit({"event": "voice_audio_failed", "detail": str(e)})

    except FlowClientError as e:
        setup_error = str(e)
        _emit({"event": "error", "phase": "setup/drive", "detail": setup_error})
    except Exception as e:  # noqa: BLE001
        setup_error = f"{type(e).__name__}: {e}"
        if _is_infra_error(e):
            infra_fail = True
        _emit({"event": "error", "phase": "setup/drive", "detail": setup_error})

    # A session that NEVER executed a turn (agent create / voice join / first LLM
    # call failed) is an infrastructure failure by definition — nothing about the
    # flow was measured, so it must not read as a flow finding.
    if setup_error and not turns:
        infra_fail = True

    # Fall back to per-turn steps if the trace GET was unavailable.
    if not full_steps:
        for t in turns:
            full_steps.extend(t["steps"])
    full_steps = sorted(full_steps, key=lambda s: s.get("seq", 0))

    # Derive termination from the TRACE, not just the per-turn flag. Over voice
    # `res.raw["ended"]` is hardcoded False (the transport can't see the flow's
    # terminal), so a flow that reached its `end`/handoff (a `flow_end` step —
    # incl. the deferred closing-terminal) would wrongly read ended=False. If the
    # persisted trace shows a flow_end, the flow DID terminate.
    if not ended and any(s.get("kind") == "flow_end" for s in full_steps):
        ended = True

    # ── judges + analyze ─────────────────────────────────────────────────────
    transcript = _render_transcript(turns, greeting=greeting)
    success = {"success": None, "reason": "not run"}
    if agent_id and turns and not setup_error:
        success = judge_task_success(model, task, transcript)
        _emit({"event": "task_success", **success})

    findings: list[Finding] = []
    if infra_fail:
        findings.append(Finding(
            "infra_fail", "high",
            "infrastructure failure — the session could not be measured "
            f"(attempt {attempt}): {setup_error or end_reason}",
            evidence={"attempt": attempt, "turns_driven": len(turns),
                      "end_reason": end_reason}))
    elif setup_error:
        findings.append(Finding("stuck_termination", "high",
                                f"session failed to run: {setup_error}"))
    elif voice and not full_steps:
        # Voice ran the flow over the real STT→brain→TTS pipeline, but the backend
        # does not persist a voice flow step-trace, so the deterministic state-trace
        # analyzer has nothing to audit. This is a KNOWN transport gap, not a product
        # bug — flagged as info, not a false stuck_termination. Task-success (from the
        # spoken transcript), per-turn latency, and duplex audio still apply. The
        # moment the backend persists the voice trace, full_steps is non-empty and the
        # unchanged analyzer runs on it via the branch below. See WHISSLE_VOICE_TESTING.md.
        findings.append(Finding(
            "voice_trace_unavailable", "info",
            "voice session ran the flow over the real voice pipeline, but no voice "
            "flow step-trace is persisted by the backend, so the deterministic "
            "state-trace analyzer was skipped (transcript task-success still ran).",
            evidence={"room": vt.room if vt else None, "turns": len(turns),
                      "latencies_ms": audio_evidence.get("latencies_ms", [])}))
    elif flow_spec:
        post_goal_driven = (len(turns) - goal_met_turn) if goal_met_turn else 0
        findings = analyze_session(
            flow_spec, full_steps,
            tools_used_by_turn=tools_used_by_turn,
            ended=ended, goal_met=success.get("success"),
            sim_goal_met=bool(sim and sim.goal_met),
            post_goal_turns_driven=max(0, post_goal_driven),
            turn_cap_hit=turn_cap_hit,
            empty_reply_turns=empty_reply_turns,
            compliance=task.compliance,
            transcript_lower="\n".join(
                (t["agent_reply"] or "") for t in turns).lower(),
        )
    else:
        findings.append(Finding("stuck_termination", "high",
                                "no declared flow spec available to audit against."))

    for f in findings:
        _emit({"event": "finding", **f.as_dict()})

    # Tear down the live voice room before deleting the agent.
    if vt is not None:
        try:
            vt.stop()
        except Exception as e:  # noqa: BLE001
            _emit({"event": "voice_stop_failed", "detail": str(e)})

    # ── teardown — NEVER leave a throwaway agent behind ──────────────────────
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
    # Sim-reply latency rollup (voice): per-turn breakdowns + p50/p95 of the total.
    # total/wait are anchored on the bot's AUDIO end (the audible turn boundary),
    # so an end-of-turn detector stall shows up here instead of hiding.
    _sim_replies = [t.get("sim_reply") for t in turns if t.get("sim_reply")]
    sim_reply_summary = {
        "turns_measured": len(_sim_replies),
        "p50_ms": _pctl([r["total_ms"] for r in _sim_replies], 0.50),
        "p95_ms": _pctl([r["total_ms"] for r in _sim_replies], 0.95),
        "p50_wait_ms": _pctl([r["wait_ms"] for r in _sim_replies], 0.50),
        "p50_wait_from_event_ms": _pctl(
            [r["wait_from_event_ms"] for r in _sim_replies
             if r.get("wait_from_event_ms") is not None], 0.50),
        "p50_llm_ms": _pctl([r.get("llm_ms") for r in _sim_replies], 0.50),
        "p50_tts_ms": _pctl([r["tts_ms"] for r in _sim_replies], 0.50),
        "bot_end_reasons": dict(Counter(
            r.get("bot_end_reason") for r in _sim_replies if r.get("bot_end_reason"))),
    } if _sim_replies else None
    result = {
        "task_id": task.id, "agent_type": task.agent_type, "scenario": task.scenario,
        "ts": ts, "agent_id": agent_id, "agent_deleted": deleted or keep_agent,
        "conversation_id": conv_id,
        "mode": mode,
        "infra_fail": infra_fail,
        "attempt": attempt,
        "sim_reply_latency": sim_reply_summary,
        "voice_room": (vt.room if vt else None) if voice else None,
        "greeting": greeting or None,
        "audio": audio_evidence or None,
        "num_turns": len(turns), "ended": ended,
        "end_reason": end_reason,
        "goal_met_turn": goal_met_turn,
        "post_goal_turns_driven": max(0, (len(turns) - goal_met_turn)
                                      if goal_met_turn else 0),
        "empty_reply_turns": empty_reply_turns,
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

    # Per-session, ts-stamped sidecar carrying the FULL persisted flow-trace (every
    # state_enter / transition_check / tools_gated / var_set / say_emitted / flow_end
    # step), the full transcript incl. greeting, the outcome+metadata, and the analyzer
    # findings — the complete structured record for each session (the <task>.json above
    # strips _full_steps and is overwritten when a task repeats). This is the deliverable.
    (out_dir / f"{task.id}_{ts}.session.json").write_text(
        json.dumps({
            "task_id": task.id, "agent_type": task.agent_type, "mode": mode, "ts": ts,
            "agent_id": agent_id, "conversation_id": conv_id,
            "voice_room": (vt.room if vt else None) if voice else None,
            "scenario": task.scenario, "persona": task.persona, "goal": task.goal,
            "compliance_spec": task.compliance,
            "greeting": greeting or None,
            "outcome": {"task_success": success.get("success"),
                        "task_success_reason": success.get("reason"),
                        "ended": ended,
                        "end_reason": end_reason,
                        "goal_met_turn": goal_met_turn,
                        "sim_goal_met": bool(sim and sim.goal_met),
                        "post_goal_turns_driven": result["post_goal_turns_driven"],
                        "empty_reply_turns": empty_reply_turns,
                        "final_state": turns[-1]["current_state"] if turns else None},
            "metadata": {"num_turns": len(turns),
                         "max_turns_budget": task.max_turns,
                         "post_goal_turns_allowance": task.post_goal_turns,
                         "turn_cap_hit": turn_cap_hit,
                         "start_state": flow_spec.get("start_state"),
                         "latencies_ms": (audio_evidence or {}).get("latencies_ms", []),
                         "sim_reply_latency_ms": sim_reply_summary,
                         "audio": audio_evidence or None,
                         "signals_summary": _signals_summary(turns),
                         "infra_fail": infra_fail,
                         "attempt": attempt,
                         "setup_error": setup_error},
            "transcript": transcript,
            "turns": turns,                    # user/agent per turn + per-turn steps
            "flow_trace": full_steps,          # the FULL persisted flow step-trace
            "states_visited": result["states_visited"],
            "analyzer_findings": [f.as_dict() for f in findings],
            "flow_spec": flow_spec,
        }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


def _render_transcript(turns: list[dict], greeting: str = "") -> str:
    lines = []
    if greeting:
        lines.append(f"AGENT: {greeting}")  # voice: the agent answers/greets first
    for t in turns:
        lines.append(f"USER: {t['user_msg']}")
        lines.append(f"AGENT: {t['agent_reply']}")
    return "\n".join(lines)


# ── aggregation / reporting ──────────────────────────────────────────────────────

def aggregate_agent_type(agent_type: str, flow_spec: dict,
                         results: list[dict]) -> dict[str, Any]:
    """Coverage + finding rollup for one agent type over its sessions.

    Infra-failed sessions (``infra_fail``: transport / provider / credit outages,
    already retried once by the runner) are counted in their OWN bucket and
    excluded from the flow metrics — a session that never measured the flow must
    not read as the flow failing."""
    ran = [r for r in results if not r.get("infra_fail")]
    infra = [r for r in results if r.get("infra_fail")]
    cov_findings, cov_table = coverage_findings(
        flow_spec, [r["_full_steps"] for r in ran])
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
        "sessions_ran": len(ran),
        "sessions_infra": len(infra),
        "sessions_ended_cleanly": sum(1 for r in ran if r["ended"]),
        "task_success": sum(1 for r in ran if r["task_success"] is True),
        "sessions_with_high_findings": sum(1 for r in ran if r["high_severity"]),
        "finding_counts_by_type": dict(type_counter),
        "finding_counts_by_severity": dict(sev_counter),
        "coverage": cov_table,
        "coverage_findings": [f.as_dict() for f in cov_findings],
        "sessions_detail": [
            {k: r[k] for k in ("task_id", "scenario", "num_turns", "ended",
                               "task_success", "final_state", "high_severity",
                               "finding_counts_by_type", "agent_deleted",
                               "infra_fail", "attempt")}
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
        f"- **sessions**: {s['sessions']}  •  **ended cleanly**: "
        f"{s['sessions_ended_cleanly']}/{s['sessions_ran']}  •  **task success**: "
        f"{s['task_success']}/{s['sessions_ran']}",
        f"- **sessions with HIGH-severity findings**: "
        f"{s['sessions_with_high_findings']}/{s['sessions_ran']}",
        f"- **infra failures (excluded from flow metrics)**: "
        f"{s['sessions_infra']}/{s['sessions']}",
        "",
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
        "| agent_type | sessions | infra | ended | task_success | high-sev sessions | "
        "states cov | trans cov |",
        "|------------|----------|-------|-------|--------------|-------------------|"
        "-----------|-----------|",
    ]
    for s in agent_summaries:
        cov = s["coverage"]
        lines.append(
            f"| `{s['agent_type']}` | {s['sessions']} | "
            f"{s.get('sessions_infra', 0)} | "
            f"{s['sessions_ended_cleanly']}/{s['sessions_ran']} | "
            f"{s['task_success']}/{s['sessions_ran']} | "
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


@app.command()
def run(
    agent_type: str = Option(..., help="seeded agent type (e.g. dental_receptionist)"),
    sessions: int = Option(2, help="how many sessions to run (cycles tasks if > count)"),
    task_id: Optional[str] = Option(
        None, help="run specific task id(s) instead — comma-separated for several"),
    semantic: bool = Option(True, help="run the per-turn goal-drift LLM judge"),
    keep_agent: bool = Option(False, help="do NOT delete the throwaway agents (debug)"),
    max_turns: Optional[int] = Option(None, help="override the per-session turn cap"),
    mode: str = Option(
        "text", help="transport: 'text' (deterministic /chat/turn) or 'voice' (the "
        "real STT→brain→TTS pipeline over LiveKit — see WHISSLE_VOICE_TESTING.md)"),
) -> None:
    """Run simulated-user sessions for one agent type against the LIVE backend."""
    if mode not in ("text", "voice"):
        console.print(f"[red]--mode must be 'text' or 'voice', got {mode!r}[/red]")
        raise SystemExit(2)
    client = FlowClient()
    model = WhissleModel()
    who = client.whoami()
    console.print(
        f"[bold]flow-sim[/bold]  org={who.get('organization', {}).get('name')!r}  "
        f"type={agent_type}  base={client.base}  [bold]mode={mode}[/bold]")

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
                          semantic=semantic, keep_agent=keep_agent, mode=mode)
        # Infra failure (session never ran / transport died): retry the WHOLE
        # session once before recording it; a second infra failure is recorded in
        # the infra bucket (excluded from flow metrics), never as a flow finding.
        if res.get("infra_fail"):
            console.print("    [yellow]infra failure — retrying session once[/yellow]")
            res = run_session(client, model, task, system_prompt,
                              semantic=semantic, keep_agent=keep_agent, mode=mode,
                              attempt=2)
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
    voice_note = ""
    if res.get("mode") == "voice":
        lats = ((res.get("audio") or {}).get("latencies_ms")) or []
        bot = ((res.get("audio") or {}).get("bot")) or {}
        p50 = sorted(lats)[len(lats) // 2] if lats else None
        sr = res.get("sim_reply_latency") or {}
        voice_note = (f"  [cyan]voice[/cyan] room={res.get('voice_room')!r} "
                      f"turn-latency-p50={p50}ms "
                      f"sim-reply-p50={sr.get('p50_ms')}ms "
                      f"bot-audio={bot.get('seconds')}s")
    if res.get("infra_fail"):
        voice_note += "  [yellow]INFRA-FAIL[/yellow]"
    console.print(
        f"    turns={res['num_turns']} ended={res['ended']} "
        f"success={res['task_success']} final={res['final_state']!r}  "
        f"findings: {ft}  {tag}  ({dt:.1f}s)  deleted={res['agent_deleted']}{voice_note}")


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
