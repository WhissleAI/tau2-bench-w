#!/usr/bin/env python3
"""Aggregate tau2-bench-w voice-flow session JSONs into a report Markdown.

Consumes ``results/whissle/flow_sim/<agent_type>/*.session.json`` and emits a
Markdown doc (title, run summary, per-agent table, termination/length analysis,
signal+metadata coverage, and the analyzer-findings ledger) suitable for
``marketing_post_agent/render_latex_report.py`` → tectonic PDF.

Usage:
  build_bench_report_md.py --results <dir> --out report.md [--title "..."] [--label before|after]
"""
from __future__ import annotations
import argparse, json, glob, os, statistics as st
from collections import defaultdict, Counter


def _load(path):
    try:
        return json.load(open(path))
    except Exception:
        return None


def _reached_end(s):
    # Prefer the authoritative flow trace (flow_end kind); fall back to outcome.ended.
    trace = s.get("flow_trace") or []
    if any(t.get("kind") == "flow_end" for t in trace):
        return True
    return bool((s.get("outcome") or {}).get("ended"))


_INFRA_MARKERS = ("failed to run", "models/chat failed", "http 502", "http 503",
                  "http 504", "modelerror")


def _infra_failed(s):
    """A session ABORTED by a harness/infra error — the LLM user-sim driver couldn't
    fetch its next utterance (worker-starvation 502 / chat-driver failure), so the
    session ended inconclusively. This is independent of turn count: the abort can
    land mid-call (a few turns already recorded) or before the first turn. Such
    sessions are NOT a flow-logic outcome and must be excluded from the flow-quality
    denominator, or they masquerade as flow failures. Keyed on the harness's own
    abort marker in an analyzer finding — the authoritative signal."""
    for f in s.get("analyzer_findings") or []:
        d = (f.get("detail") or "").lower()
        if any(m in d for m in _INFRA_MARKERS):
            return True
    return False


def _sessions(results_dir):
    by = defaultdict(list)
    for p in sorted(glob.glob(os.path.join(results_dir, "*", "*.session.json"))):
        s = _load(p)
        if s:
            by[s.get("agent_type", os.path.basename(os.path.dirname(p)))].append(s)
    return by


def _pct(n, d):
    return f"{100.0*n/d:.0f}%" if d else "—"


def build(results_dir, title, label):
    by = _sessions(results_dir)
    agents = sorted(by)
    L = []
    W = L.append
    tag = f" ({label})" if label else ""
    W(f"# {title}{tag}\n")

    tot = sum(len(v) for v in by.values())
    all_raw = [s for v in by.values() for s in v]
    infra = [s for s in all_raw if _infra_failed(s)]
    all_s = [s for s in all_raw if not _infra_failed(s)]   # flow-quality denominator
    ran = len(all_s)
    succ = sum(1 for s in all_s if (s.get("outcome") or {}).get("task_success"))
    ended = sum(1 for s in all_s if _reached_end(s))
    turns_all = [len(s.get("turns") or []) for s in all_s if s.get("turns")]
    findings = [f for s in all_s for f in (s.get("analyzer_findings") or [])]

    W("## Run summary\n")
    W(f"- **Sessions attempted:** {tot} across {len(agents)} agent types")
    W(f"- **Infra/harness failures (never executed — 502 / chat-driver):** "
      f"{len(infra)}/{tot} ({_pct(len(infra), tot)}) — excluded from flow metrics below")
    W(f"- **Sessions that executed:** {ran}")
    W(f"- **Task success (of executed):** {succ}/{ran} ({_pct(succ, ran)})")
    W(f"- **Reached a clean close (of executed):** {ended}/{ran} ({_pct(ended, ran)})")
    if turns_all:
        W(f"- **Caller turns:** median {int(st.median(turns_all))}, "
          f"mean {sum(turns_all)/len(turns_all):.1f}, max {max(turns_all)}")
    W(f"- **Analyzer findings (executed sessions):** {len(findings)} "
      f"({', '.join(f'{k}:{c}' for k, c in Counter(f.get('severity') for f in findings).most_common())})\n")

    # per-agent table
    W("## Per-agent results\n")
    W("_Success / close are over **executed** sessions (infra-failed excluded)._\n")
    W("| Agent type | Exec | Infra-fail | Success | Reached close | Median turns | Signals cov. | Metadata cov. | Findings |")
    W("|---|--:|--:|--:|--:|--:|--:|--:|--:|")
    for a in agents:
        allv = by[a]
        inf = sum(1 for s in allv if _infra_failed(s))
        v = [s for s in allv if not _infra_failed(s)]
        n = len(v)
        sc = sum(1 for s in v if (s.get("outcome") or {}).get("task_success"))
        en = sum(1 for s in v if _reached_end(s))
        tn = [len(s.get("turns") or []) for s in v if s.get("turns")]
        # coverage: fraction of turns carrying signals / user_metadata, averaged over sessions
        def cov(field):
            fr = []
            for s in v:
                ts = s.get("turns") or []
                if ts:
                    fr.append(sum(1 for t in ts if t.get(field)) / len(ts))
            return _pct(sum(fr), len(fr)) if fr else "—"
        fnd = sum(len(s.get("analyzer_findings") or []) for s in v)
        med = int(st.median(tn)) if tn else 0
        W(f"| {a} | {n} | {inf} | {sc}/{n} ({_pct(sc,n)}) | {en}/{n} ({_pct(en,n)}) | {med} | "
          f"{cov('signals')} | {cov('user_metadata')} | {fnd} |")
    W("")

    # termination / length detail
    W("## Termination & length\n")
    stuck = [s for s in all_s if not _reached_end(s)]
    W(f"- **{len(stuck)}/{ran}** executed sessions did not reach a clean close "
      f"(the termination target: closing-terminal + guard-net fallback + shorter intake).")
    # length pressure manifests as sticking in a collector state before the turn cap,
    # not always as >=20 turns; report the states where sessions stall.
    stall = Counter(f.get("state") for s in stuck for f in (s.get("analyzer_findings") or [])
                    if f.get("type") == "stuck_termination" and f.get("state"))
    if stall:
        W("- **Stall states** (where executed sessions stuck without closing): "
          + ", ".join(f"`{s}`×{c}" for s, c in stall.most_common()))
    over = [s for s in all_s if len(s.get("turns") or []) >= 20]
    W(f"- **{len(over)}/{ran}** executed sessions ran ≥20 caller turns.")
    W("")

    # findings ledger
    W("## Analyzer findings ledger\n")
    if not findings:
        W("_No analyzer findings._\n")
    else:
        by_type = defaultdict(list)
        for f in findings:
            by_type[f.get("type", "?")].append(f)
        W("| Type | Count | Severity | Example state | Detail (first) |")
        W("|---|--:|---|---|---|")
        for t, fs in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
            sev = Counter(f.get("severity") for f in fs).most_common(1)[0][0]
            ex = fs[0]
            detail = (ex.get("detail") or "").replace("|", "/")[:90]
            W(f"| {t} | {len(fs)} | {sev} | {ex.get('state','—')} | {detail} |")
    W("")

    W("## Method\n")
    W("Real-audio voice sessions over LiveKit against the live backend; an LLM "
      "user-simulator (persona+goal) drives each call, a rule analyzer inspects the "
      "flow trace, and per-turn hesitation **signals** + ASR **metadata** are captured "
      "from the whissle-large gRPC sidecar. Success is judged by an independent LLM "
      "grader; close is taken from the authoritative `flow_end` trace event.\n")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="Whissle Voice-Flow Benchmark")
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    md = build(a.results, a.title, a.label)
    with open(a.out, "w") as f:
        f.write(md)
    print(f"wrote {a.out} ({len(md)} chars)")


if __name__ == "__main__":
    main()
