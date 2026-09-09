#!/usr/bin/env python3
"""Whissle native diagnostic — structured extractor for a tau2 results.json.

Reproduces (and extends) the teammate's "Whissle native vN diagnostic" report:
metric table, tool behavior, latency/token stats, provenance (result SHA-256 +
base commit), per-task pass rollup. Works for text (whissle) and voice
(whissle_voice) arms alike.

Usage:
  diagnostic_report.py <results.json> [--label "retail text"] [--json out.json]
"""
from __future__ import annotations
import argparse, hashlib, json, os, statistics as st
from collections import Counter, defaultdict


def _pct(n, d): return round(100.0 * n / d, 2) if d else 0.0


def _pctl(xs, p):
    if not xs: return 0.0
    xs = sorted(xs); k = (len(xs) - 1) * p
    lo = int(k); hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def load(path):
    # save-to is a directory containing results.json
    if os.path.isdir(path):
        path = os.path.join(path, "results.json")
    with open(path, "rb") as f:
        raw = f.read()
    return json.loads(raw), hashlib.sha256(raw).hexdigest(), path


def component_pass(ri, comp):
    """Did a reward component pass? Uses reward_breakdown when present."""
    rb = ri.get("reward_breakdown") or {}
    if comp in rb:
        return rb[comp] >= 1.0
    return None


def build(path, label):
    d, sha, resolved = load(path)
    sims = d.get("simulations") or []
    info = d.get("info") or {}
    tasks = d.get("tasks") or []
    n = len(sims)
    num_trials = info.get("num_trials", 1)
    n_tasks = len({s.get("task_id") for s in sims}) or len(tasks)

    exact = db = act = asrt = 0
    infra = 0
    durations, agent_latency, convo_msgs, spoken_turns = [], [], [], []
    in_tok = out_tok = 0
    usim_cost = 0.0
    convo_ids = set()
    agent_tool_calls = Counter()
    tool_results_ok = tool_results_total = 0
    per_task = defaultdict(lambda: {"trials": 0, "exact": 0})
    term = Counter()

    for s in sims:
        ri = s.get("reward_info") or {}
        reward = ri.get("reward")
        if s.get("termination_reason") in ("too_many_errors", "infra_error") or ri.get("info", {}).get("infra"):
            infra += 1
        is_exact = (reward == 1.0)
        exact += int(is_exact)
        if (ri.get("db_check") or {}).get("db_match"): db += 1
        acts = ri.get("action_checks") or []
        if acts and all(a.get("action_match") for a in acts): act += 1
        cp = component_pass(ri, "NL_ASSERTION")
        if cp: asrt += 1
        tid = s.get("task_id")
        per_task[tid]["trials"] += 1
        per_task[tid]["exact"] += int(is_exact)
        if s.get("duration"): durations.append(s["duration"])
        term[s.get("termination_reason", "?")] += 1
        psid = s.get("provider_session_id")
        if psid: convo_ids.add(psid)

        msgs = s.get("messages") or []
        n_agent = n_user = 0
        for m in msgs:
            role = m.get("role"); src = (m.get("source") or "")
            if role == "assistant":
                n_agent += 1
                g = m.get("generation_time_seconds")
                if g: agent_latency.append(g * 1000.0)  # ms
                for tc in (m.get("tool_calls") or []):
                    agent_tool_calls[tc.get("name")] += 1
            if role in ("user", "assistant"): pass
            if role == "tool":
                tool_results_total += 1
                if not m.get("error"): tool_results_ok += 1
            u = m.get("usage") or {}
            if u:
                in_tok += u.get("prompt_tokens", 0) or 0
                out_tok += u.get("completion_tokens", 0) or 0
            if m.get("cost"): usim_cost += m["cost"]
        convo_msgs.append(len([m for m in msgs if m.get("role") in ("user", "assistant")]))
        spoken_turns.append(len([m for m in msgs if m.get("role") in ("user", "assistant") and m.get("content")]))

    def stats(xs):
        return dict(mean=round(st.mean(xs), 2) if xs else 0,
                    median=round(st.median(xs), 2) if xs else 0,
                    p95=round(_pctl(xs, 0.95), 2), max=round(max(xs), 2) if xs else 0)

    out = {
        "label": label,
        "provenance": {
            "result_sha256": sha,
            "results_path": resolved,
            "base_commit": info.get("git_commit"),
            "num_trials": num_trials,
            "max_steps": info.get("max_steps"),
            "agent": (info.get("agent_info") or {}).get("implementation")
                     or (info.get("agent_info") or {}).get("llm") or None,
            "user_llm": (info.get("user_info") or {}).get("llm") or None,
        },
        "result": {
            "tasks": n_tasks,
            "trials_per_task": num_trials,
            "completed_trials": f"{n - infra}/{n}",
            "infra_failures": infra,
            "exact_passes": f"{exact}/{n}",
            "exact_pass_pct": _pct(exact, n),
            "db_component_passes": f"{db}/{n}",
            "action_component_passes": f"{act}/{n}",
            "assertion_component_passes": f"{asrt}/{n}",
            "unique_conversations": len(convo_ids),
        },
        "tooling": {
            "agent_tool_calls_total": sum(agent_tool_calls.values()),
            "agent_tool_calls_per_convo": round(sum(agent_tool_calls.values()) / n, 2) if n else 0,
            "agent_tool_calls": dict(agent_tool_calls.most_common()),
            "tool_results_ok": tool_results_ok,
            "tool_results_total": tool_results_total,
            "tool_success_pct": _pct(tool_results_ok, tool_results_total),
        },
        "conversation": {
            "end_to_end_s": stats(durations),
            "all_messages": stats(convo_msgs),
            "spoken_turns": stats(spoken_turns),
            "agent_response_latency_ms": stats(agent_latency),
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "user_sim_cost_usd": round(usim_cost, 4),
        },
        "termination": dict(term),
        "per_task": {str(k): v for k, v in sorted(per_task.items(), key=lambda kv: str(kv[0]))},
    }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--label", default="run")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    out = build(a.results, a.label)
    if a.json:
        json.dump(out, open(a.json, "w"), indent=2)
    r = out["result"]
    print(f"\n=== {a.label} ===")
    print(f"tasks={r['tasks']} trials/task={r['trials_per_task']} completed={r['completed_trials']} infra={r['infra_failures']}")
    print(f"EXACT PASS (Pass^1) = {r['exact_passes']}  ({r['exact_pass_pct']}%)")
    print(f"  DB={r['db_component_passes']}  action={r['action_component_passes']}  assertion={r['assertion_component_passes']}  convos={r['unique_conversations']}")
    c = out["conversation"]
    print(f"latency ms: mean={c['agent_response_latency_ms']['mean']} p95={c['agent_response_latency_ms']['p95']} max={c['agent_response_latency_ms']['max']}")
    print(f"tokens in/out = {c['input_tokens']}/{c['output_tokens']}  user-sim $={c['user_sim_cost_usd']}")
    t = out["tooling"]
    print(f"agent tool calls = {t['agent_tool_calls_total']} ({t['agent_tool_calls_per_convo']}/convo); success {t['tool_success_pct']}%")
    print(f"top tools: {dict(list(t['agent_tool_calls'].items())[:6])}")
    print(f"provenance: sha256={out['provenance']['result_sha256'][:16]}… base={out['provenance']['base_commit']}")


if __name__ == "__main__":
    main()
