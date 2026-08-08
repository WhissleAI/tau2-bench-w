#!/usr/bin/env python3
"""Pull every arm's numbers out of the three results trees into one table."""
import json
import pathlib
import statistics

RES = pathlib.Path("/Users/karan/Desktop/work/whissle/live_assist/tau2-bench-w/results/whissle")
ARMS = [
    ("haiku",   "claude-haiku-4-5"),
    ("sonnet5", "claude-sonnet-5"),
    ("opus5",   "claude-opus-5"),
    ("fable5",  "claude-fable-5"),
    ("g35f",    "gemini-3.5-flash"),
    ("g35fl",   "gemini-3.5-flash-lite"),
    ("g3fp",    "gemini-3-flash-preview"),
]


def _rate(node):
    """MedAgentBench reports each bucket as {n, of, rate_pct} (or a bare float)."""
    if isinstance(node, dict):
        return node.get("success_rate_pct") or node.get("rate_pct") or (
            100.0 * node["correct"] / node["n"] if node.get("n") else None)
    return node * 100 if isinstance(node, float) and node <= 1 else node


def pct(x):
    return "—" if x is None else f"{x:.1f}"


def medagent(arm):
    p = RES / "medagentbench" / f"brain-parity_sweep25_{arm}" / "SUMMARY.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    wi = d.get("write_integrity") or {}
    lat = []
    for t in (RES / "medagentbench" / f"brain-parity_sweep25_{arm}" / "tasks").glob("*.json"):
        j = json.loads(t.read_text())
        lat += [x["latency_ms"] for x in (j.get("turns") or []) if x.get("latency_ms")]
    lat.sort()
    q = lambda p_: lat[min(len(lat) - 1, int(len(lat) * p_))] if lat else None  # noqa: E731
    return {
        "overall": _rate(d.get("overall")),
        "query": _rate(d.get("query")),
        "action": _rate(d.get("action")),
        "infra_fail": d.get("n_infra_fail"),
        "n_scored": d.get("n_scored"),
        "said_not_wrote": (wi.get("said_but_did_not_write") or {}).get("rate_pct"),
        "said_n": (wi.get("said_but_did_not_write") or {}).get("n"),
        "action_eps": wi.get("n_action_episodes"),
        "ehr_rejected": wi.get("emitted_but_ehr_rejected"),
        "lat_p50": q(0.5), "lat_p95": q(0.95), "turns": len(lat),
        "raw": str(p.parent),
    }


def agentclinic(arm):
    hits = sorted(RES.glob(f"agentclinic/*sweep25_{arm}"))
    if not hits:
        return None
    p = hits[-1] / "SUMMARY.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    return {
        "accuracy": (d.get("accuracy") or 0) * 100,
        "declined": (d.get("declined_rate") or 0) * 100,
        "judge_cost_usd": d.get("judge_cost_usd"),
        "judge_model": d.get("judge_model"),
        "avg_inferences": d.get("avg_inferences"),
        "lat_p50": d.get("latency_p50_ms"), "lat_p95": d.get("latency_p90_ms"),
        "infra_fail": d.get("infra_fail"),
        "avg_inf": d.get("avg_inferences_per_case"),
        "raw": str(hits[-1]),
        "all": d,
    }


def pab(arm):
    p = RES / "patientagentbench" / f"sweep25_{arm}" / "summary.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text())
    return {"summary": d, "raw": str(p.parent)}


rows = {}
for arm, model in ARMS:
    rows[arm] = {"model": model, "medagent": medagent(arm),
                 "agentclinic": agentclinic(arm), "pab": pab(arm)}

print(f"{'arm':9s} {'model':24s} | {'MAB %':>6s} {'Q%':>6s} {'A%':>6s} {'said!w':>7s} "
      f"{'inf':>4s} {'p50ms':>7s} | {'AC %':>6s} {'decl%':>6s} {'p50ms':>7s} {'p90ms':>7s}")
print("-" * 118)
for arm, model in ARMS:
    r = rows[arm]
    m, a = r["medagent"], r["agentclinic"]
    said = f"{m['said_n']}/{m['action_eps']}" if m else "-"
    inf = str(m["infra_fail"]) if m else "-"
    mp50 = str(m["lat_p50"]) if m and m["lat_p50"] else "-"
    ap50 = str(a["lat_p50"]) if a and a["lat_p50"] else "-"
    ap95 = str(a["lat_p95"]) if a and a["lat_p95"] else "-"
    print(
        f"{arm:9s} {model:24s} | "
        f"{pct(m and m['overall']):>6s} {pct(m and m['query']):>6s} "
        f"{pct(m and m['action']):>6s} {said:>7s} {inf:>4s} {mp50:>7s} | "
        f"{pct(a and a['accuracy']):>6s} {pct(a and a['declined']):>6s} "
        f"{ap50:>7s} {ap95:>7s}"
    )

pathlib.Path("collected.json").write_text(json.dumps(rows, indent=1, default=str))
print("\n-> collected.json")
