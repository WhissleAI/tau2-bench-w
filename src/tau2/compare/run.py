# Copyright Sierra
"""CLI: ``python -m tau2.compare.run {list|run|report}``.

    python -m tau2.compare.run list
    python -m tau2.compare.run run --vendor whissle,elevenlabs --out results/compare
    python -m tau2.compare.run run --scenario misheard_proper_noun --out /tmp/x
    python -m tau2.compare.run report --out results/compare/<run-id>

``run`` with no ElevenLabs credentials is a SUCCESS, not an error: it produces a
Whissle-only report whose title, first section and JSON all say it is not a
comparison. Exiting non-zero there would push an operator toward the one thing
this package must never do — filling the gap with a number.

The only non-zero exits are for things that are actually broken: an unknown
vendor or scenario id, a malformed scenario file, or the home vendor being
unrunnable (which leaves nothing to measure at all).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

from dotenv import load_dotenv

from tau2.compare import compare as cmp
from tau2.compare import honesty, report, scenarios
from tau2.compare.vendors import HOME_VENDOR, KNOWN, build

# Same convention as run_flow.sh / tau2.flow.client: credentials live in .env at
# the repo root. Loaded here at the CLI edge rather than at import time, so the
# library never reaches into the filesystem behind a test's back.
load_dotenv()

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NOTHING_TO_MEASURE = 3

DEFAULT_VENDORS = f"{HOME_VENDOR},elevenlabs"


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _split(value: Optional[str]) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def _adapters(vendors: list[str], args: argparse.Namespace) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for vendor in vendors:
        kwargs: dict[str, Any] = {}
        if vendor == HOME_VENDOR:
            if args.whissle_agent_id:
                kwargs["agent_id"] = args.whissle_agent_id
            if args.transport:
                kwargs["transport"] = args.transport
        out[vendor] = build(vendor, **kwargs)
    return out


# ── commands ────────────────────────────────────────────────────────────────────


def cmd_list(args: argparse.Namespace) -> int:
    try:
        defs = scenarios.select(_split(args.scenario) or None, args.scenarios_file)
    except scenarios.ScenarioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    banner = honesty.banner_markdown()
    if banner:
        print(banner)
    print(f"Scenarios ({len(defs)}) — {args.scenarios_file or scenarios.default_path()}")
    for s in defs:
        print(f"\n  {s.id}")
        print(f"    {s.title}")
        print(f"    agent_type      : {s.agent_type}")
        print(f"    transports      : {', '.join(s.transports)}")
        print(f"    expectation     : {s.hypothesis.expectation}")
        print(f"    claim           : {s.hypothesis.claim}")
        print(f"    mechanism       : {s.hypothesis.mechanism}")
        print(f"    falsifier       : {s.hypothesis.falsifier}")
        print(f"    trace evidence  : {s.trace_evidence.description}")
        if s.trace_evidence.requires_metadata_head:
            print("    NOTE            : mechanism needs the acoustic metadata "
                  "head, which is disabled — unprovable today")
        print(f"    criteria        : {len(s.pass_criteria)} "
              f"({sum(1 for c in s.pass_criteria if c.critical)} critical)")

    print("\nVendors:")
    for vendor in _split(args.vendor) or list(KNOWN):
        try:
            pre = build(vendor).preflight()
        except ValueError as exc:
            print(f"  {vendor}: error — {exc}")
            continue
        state = "RUNNABLE" if pre.runnable else "NOT RUNNABLE"
        print(f"  {vendor}: {state}")
        if pre.missing_env:
            print(f"    missing env: {', '.join(pre.missing_env)}")
        if pre.reason:
            print(f"    {pre.reason}")
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    vendors = _split(args.vendor) or _split(DEFAULT_VENDORS)
    unknown = [v for v in vendors if v not in KNOWN]
    if unknown:
        print(f"error: unknown vendor(s) {unknown}; known: {list(KNOWN)}",
              file=sys.stderr)
        return EXIT_USAGE
    try:
        defs = scenarios.select(_split(args.scenario) or None, args.scenarios_file)
    except scenarios.ScenarioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    adapters = _adapters(vendors, args)
    preflights = {v: a.preflight().to_dict() for v, a in adapters.items()}

    banner = honesty.banner_markdown()
    if banner:
        print(banner)
    for vendor, pre in preflights.items():
        if not pre["runnable"]:
            print(f"[preflight] {vendor}: NOT RUNNABLE — {pre['reason']}")
        else:
            print(f"[preflight] {vendor}: runnable")

    if HOME_VENDOR in vendors and not preflights[HOME_VENDOR]["runnable"]:
        print(
            f"error: {HOME_VENDOR} is not runnable, so there is nothing to measure. "
            f"{preflights[HOME_VENDOR]['reason']}",
            file=sys.stderr,
        )
        return EXIT_NOTHING_TO_MEASURE

    run_id = args.run_id or _run_id()
    results: list[cmp.ScenarioComparison] = []
    for s in defs:
        print(f"\n[scenario] {s.id} — {s.title}")
        runs = {}
        for vendor, adapter in adapters.items():
            if not s.supports("text") and vendor == HOME_VENDOR:
                print(f"  {vendor}: scenario declares transports {s.transports}")
            run = adapter.run_scenario(s)
            runs[vendor] = run
            if not run.runnable:
                print(f"  {vendor}: not runnable")
            elif run.error:
                print(f"  {vendor}: ERROR {run.error}")
            else:
                print(f"  {vendor}: {len(run.turns)} turn(s), "
                      f"flow trace {'present' if run.flow_available else 'ABSENT'}")
        comparison = cmp.compare_scenario(s, runs)
        print(f"  verdict: {comparison.verdict} — {comparison.verdict_reason[:160]}")
        results.append(comparison)

    data = cmp.build_report_data(run_id, vendors, results, preflights)
    out_dir = os.path.join(args.out, run_id) if args.out else os.path.join(
        "results", "compare", run_id)
    paths = report.write(data, out_dir)
    report.write_runs(data, out_dir)
    print("")
    print(report.summary_line(data))
    print(f"markdown: {paths['markdown']}")
    print(f"json    : {paths['json']}")
    return EXIT_OK


def cmd_report(args: argparse.Namespace) -> int:
    path = args.json or (
        os.path.join(args.out, "report.json") if args.out else None
    )
    if not path or not os.path.exists(path):
        print(
            "error: point --json at a report.json (or --out at the run directory "
            "containing one)",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if args.rerender:
        print(report.rerender(path, args.out))
        return EXIT_OK
    payload = report.load_json(path)
    banner = honesty.banner_markdown()
    if banner:
        print(banner)
    print(f"run_id                     : {payload.get('run_id')}")
    print(f"is_comparison              : {payload.get('is_comparison')}")
    print(f"differentiator_status (run): {payload.get('differentiator_status')}")
    print(f"differentiator_status (now): {honesty.differentiator_status()}")
    if payload.get("not_a_comparison_reason"):
        print(f"not a comparison           : {payload['not_a_comparison_reason']}")
    print("rollup:")
    print(json.dumps(payload.get("rollup"), indent=2))
    return EXIT_OK


# ── parser ──────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tau2.compare.run",
        description=(
            "Scenario comparison between Whissle and external voice-agent vendors. "
            "Produces mechanism evidence, not just pass/fail — and refuses to emit "
            "a head-to-head verdict without a setup-matched pair."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--vendor", default=DEFAULT_VENDORS,
                       help=f"comma-separated vendors ({', '.join(KNOWN)})")
        p.add_argument("--scenario", default=None,
                       help="comma-separated scenario ids (default: all)")
        p.add_argument("--out", default=None, help="output directory")
        p.add_argument("--scenarios-file", default=None,
                       help="override data/compare/scenarios.json")

    p_list = sub.add_parser("list", help="list scenarios and vendor availability")
    common(p_list)
    p_list.set_defaults(func=cmd_list)

    p_run = sub.add_parser("run", help="run scenarios and write a report")
    common(p_run)
    p_run.add_argument("--run-id", default=None)
    p_run.add_argument("--whissle-agent-id", default=None,
                       help="drive an existing agent instead of a throwaway one")
    p_run.add_argument(
        "--transport", default=None, choices=["chat_turn", "bench_agent_turn"],
        help=(
            "Whissle transport. Default chat_turn — the only one that yields a flow "
            "trace; bench_agent_turn is a stateless smoke path with no mechanism "
            "evidence."
        ),
    )
    p_run.set_defaults(func=cmd_run)

    p_report = sub.add_parser("report", help="inspect or re-render a written report")
    common(p_report)
    p_report.add_argument("--json", default=None, help="path to a report.json")
    p_report.add_argument("--rerender", action="store_true",
                          help="re-render to Markdown with a current-status banner")
    p_report.set_defaults(func=cmd_report)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
