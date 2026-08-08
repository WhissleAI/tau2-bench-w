#!/usr/bin/env python3
"""Cost per benchmark case, per arm — estimated, and honest about it.

The deployed /api/bench/agent-turn does not return a `usage` block (PR #664 adds
it), so no harness can record what a case actually cost. AgentClinic is the only
one that even looks: it writes `doctor_turns[].usage`, and every entry across a
100-case run is null.

So this composes two things we DID measure:

  turns/case   counted from the harness's own per-case records
  tokens/turn  measured directly against each vendor on a matched workload
               (ttft_probe.py, `tool_turn` — the multi-turn tool-selection shape
               these benchmarks are made of)

It is an estimate, and the error bar is real: the probe's prompt is shorter than
a benchmark case's accumulated history, so absolute dollars run LOW. What it is
good for is the RATIO between arms, which is what the decision turns on — every
arm is estimated the same way from the same measured shape.
"""
import collections
import json
import pathlib
import statistics

RES = pathlib.Path("/Users/karan/Desktop/work/whissle/live_assist/tau2-bench-w/results/whissle")
PRICING = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (2.00, 10.00),          # intro through 2026-08-31
    "claude-opus-5": (5.00, 25.00),
    "claude-fable-5": (10.00, 50.00),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3-flash-preview": (1.50, 9.00),
}
ARMS = [("haiku", "claude-haiku-4-5"), ("sonnet5", "claude-sonnet-5"),
        ("opus5", "claude-opus-5"), ("fable5", "claude-fable-5"),
        ("g35f", "gemini-3.5-flash"), ("g35fl", "gemini-3.5-flash-lite"),
        ("g3fp", "gemini-3-flash-preview")]

# tokens/turn from the probe, default config per model (what the benchmarks ran)
probe = json.load(open("ttft_results.json"))
DEFAULT_ARM = {
    "claude-haiku-4-5": "haiku-4-5 (prod)",
    "claude-sonnet-5": "sonnet-5 default",
    "claude-opus-5": "opus-5 default",
    "claude-fable-5": "fable-5 default",
    "gemini-3.5-flash": "g3.5-flash default(med)",
    "gemini-3.5-flash-lite": "g3.5-flash-lite minimal",   # lite's own default
    "gemini-3-flash-preview": "g3-flash-preview (prod fo)",
}
tok = {}
for model, arm in DEFAULT_ARM.items():
    rs = [r for r in probe if r["arm"] == arm and r["case"] == "tool_turn"
          and not r.get("error")]
    if rs:
        tok[model] = (statistics.mean(r["in_tokens"] for r in rs),
                      statistics.mean(r["out_tokens"] for r in rs))


def turns_per_case(arm):
    out = {}
    d = RES / "medagentbench" / f"brain-parity_sweep25_{arm}" / "tasks"
    if d.exists():
        n = [len(json.loads(p.read_text()).get("turns") or []) for p in d.glob("*.json")]
        out["medagent"] = statistics.mean(n) if n else None
    hits = sorted(RES.glob(f"agentclinic/*sweep25_{arm}/cases"))
    if hits:
        n = [len(json.loads(p.read_text()).get("doctor_turns") or [])
             for p in hits[-1].glob("*.json")]
        out["agentclinic"] = statistics.mean(n) if n else None
    return out


print(f"{'arm':9s} {'model':23s} {'in/turn':>8s} {'out/turn':>9s} "
      f"{'MAB t/c':>8s} {'MAB $/c':>9s} {'AC t/c':>7s} {'AC $/c':>9s} {'$/1k cases':>11s}")
print("-" * 104)
for arm, model in ARMS:
    if model not in tok:
        continue
    ti, to = tok[model]
    pi, po = PRICING[model]
    per_turn = ti / 1e6 * pi + to / 1e6 * po
    t = turns_per_case(arm)
    mab = t.get("medagent")
    ac = t.get("agentclinic")
    mab_c = per_turn * mab if mab else None
    ac_c = per_turn * ac if ac else None
    ref = mab_c if mab_c else ac_c
    print(f"{arm:9s} {model:23s} {ti:8.0f} {to:9.0f} "
          f"{(f'{mab:.1f}' if mab else '-'):>8s} {(f'${mab_c:.4f}' if mab_c else '-'):>9s} "
          f"{(f'{ac:.1f}' if ac else '-'):>7s} {(f'${ac_c:.4f}' if ac_c else '-'):>9s} "
          f"{(f'${ref*1000:.2f}' if ref else '-'):>11s}")
print("\nEstimated: turns/case measured, tokens/turn measured on a matched shape.")
print("Absolute dollars run low (probe history is shorter); ratios are the signal.")
