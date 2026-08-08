"""``python -m tau2.reporting`` — build reports over run directories that already exist.

Nothing here runs a benchmark. Every command is a pure function of the artifacts on
disk, which means it is safe to re-run at any time, over any subset, and produces
byte-identical output for unchanged inputs apart from the generation timestamp.

    # one run
    python -m tau2.reporting build results/whissle/patientagentbench/pab_text_100

    # every run under results/whissle, plus the index and the website export
    python -m tau2.reporting all

    # what would change, without writing
    python -m tau2.reporting all --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, Optional

from . import honesty, render_md, web_export
from . import index as index_mod
from . import publish as publish_mod
from .adapters import ADAPTERS, BuildContext, adapter_for
from .model import RunReport

DEFAULT_RESULTS = Path("results/whissle")


def _repo_root(start: Optional[Path] = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / "pyproject.toml").is_file() and (cand / "src" / "tau2").is_dir():
            return cand
    return p


def discover(results_root: Path) -> list[Path]:
    """Every directory under ``results_root`` an adapter recognises.

    Depth-limited and detector-driven: a new benchmark's runs are discovered the
    moment its adapter lands, with no path patterns to update here.
    """
    found: list[Path] = []
    if not results_root.is_dir():
        return found
    for bench_dir in sorted(p for p in results_root.iterdir() if p.is_dir()):
        for run_dir in sorted(p for p in bench_dir.iterdir() if p.is_dir()):
            if adapter_for(run_dir):
                found.append(run_dir)
        # some suites (flow_sim) put the run one level up, keyed by agent type
        if not any(f.parent == bench_dir for f in found) and adapter_for(bench_dir):
            found.append(bench_dir)
    return found


def build_one(
    run_dir: Path, ctx: BuildContext, *, write: bool = True, allow_violations: bool = False
) -> tuple[Optional[RunReport], list[honesty.Violation], str]:
    adapter = adapter_for(run_dir)
    if adapter is None:
        return None, [
            honesty.Violation(
                "no_adapter", f"no adapter recognises {run_dir}", str(run_dir)
            )
        ], ""
    try:
        report = adapter.build(run_dir, ctx)
    except Exception as exc:  # an adapter that throws is a bug, not a reason to stop
        return None, [
            honesty.Violation(
                "adapter_error",
                f"{type(exc).__name__}: {exc}",
                str(run_dir),
            )
        ], ""
    md = render_md.render(report)
    violations = honesty.audit(report, md)
    if violations and not allow_violations:
        return report, violations, md
    if write:
        (run_dir / "REPORT.md").write_text(md, encoding="utf-8")
        (run_dir / "report.json").write_text(
            json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8"
        )
        # The wire envelope, written beside the report so the exact bytes the store
        # receives are reviewable in a diff rather than only observable over HTTP.
        (run_dir / "publish.json").write_text(
            json.dumps(publish_mod.run_envelope(report, md), indent=2) + "\n",
            encoding="utf-8",
        )
    return report, violations, md


def _print_violations(run_dir: Path, violations: Iterable[honesty.Violation]) -> None:
    print(f"  ✗ {run_dir}", file=sys.stderr)
    for v in violations:
        print(f"      {v}", file=sys.stderr)


def cmd_build(args) -> int:
    repo = _repo_root()
    results_root = (repo / DEFAULT_RESULTS).resolve()
    ctx = BuildContext(repo_root=repo, results_root=results_root)
    failed = 0
    for raw in args.run_dirs:
        run_dir = Path(raw).resolve()
        report, viols, _md = build_one(
            run_dir, ctx, write=not args.dry_run, allow_violations=args.allow_violations
        )
        if report is None or (viols and not args.allow_violations):
            _print_violations(run_dir, viols)
            failed += 1
            continue
        flag = " [PRELIMINARY]" if report.preliminary else ""
        print(
            f"  ✓ {report.run_id}: {report.headline.label} "
            f"{report.headline.formatted()} ({honesty.qualifier(report)}){flag}"
        )
        if viols:
            _print_violations(run_dir, viols)
    return 1 if failed else 0


def cmd_all(args) -> int:
    repo = _repo_root()
    results_root = (repo / DEFAULT_RESULTS).resolve()
    ctx = BuildContext(repo_root=repo, results_root=results_root)
    run_dirs = discover(results_root)
    if not run_dirs:
        print(f"no recognised run directories under {results_root}", file=sys.stderr)
        return 1

    reports: list[RunReport] = []
    rendered: dict[str, str] = {}
    entries: list[dict] = []
    failed = 0
    print(f"building {len(run_dirs)} run report(s) under {results_root}")
    for run_dir in run_dirs:
        report, viols, md = build_one(
            run_dir, ctx, write=not args.dry_run, allow_violations=args.allow_violations
        )
        if report is None or (viols and not args.allow_violations):
            _print_violations(run_dir, viols)
            failed += 1
            continue
        if viols:
            _print_violations(run_dir, viols)
        reports.append(report)
        rendered[report.run_id] = md
        entries.append(index_mod.entry_for(report))
        flag = " [PRELIMINARY]" if report.preliminary else ""
        print(
            f"  ✓ {report.run_id}: {report.headline.formatted()} "
            f"({honesty.qualifier(report)}){flag}"
        )

    if args.dry_run:
        print("dry run — nothing written")
        return 1 if failed else 0

    idx = index_mod.write(results_root, entries)
    print(f"  ✓ index: {idx['n_runs']} runs → {results_root / 'INDEX.md'}")

    export = web_export.build(publishable(reports), idx)
    export_violations = web_export.validate(export)
    if export_violations and not args.allow_violations:
        print("  ✗ website export failed validation:", file=sys.stderr)
        for v in export_violations:
            print(f"      {v}", file=sys.stderr)
        return 1
    out = repo / args.web_out
    web_export.write(out, export)
    print(f"  ✓ website export: {len(export['rows'])} row(s) → {out}")

    if getattr(args, "publish", False):
        failed += _publish(
            [(r, rendered[r.run_id]) for r in publishable(reports)],
            idx,
            allow_violations=args.allow_violations,
        )
    return 1 if failed else 0


def _publish(pairs, index, *, allow_violations: bool, dry_run: bool = False) -> int:
    """Send runs to the results store. Returns the number of failures."""
    store = publish_mod.BenchmarkStore()
    if not dry_run and not store.configured:
        print(
            f"  ✗ publish: missing {store.missing()} — set them in .env or the "
            "environment",
            file=sys.stderr,
        )
        return 1
    results, viols = publish_mod.publish_reports(
        pairs, index, store=store, dry_run=dry_run, allow_violations=allow_violations
    )
    if viols and not allow_violations:
        print("  ✗ publish blocked — the store would reject these:", file=sys.stderr)
        for v in viols:
            print(f"      {v}", file=sys.stderr)
        return 1
    bad = 0
    for r in results:
        if r.ok:
            print(f"  ✓ published {r.run_id}" + (f" ({r.detail})" if r.detail else ""))
        else:
            print(f"  ✗ publish {r.run_id}: HTTP {r.status} {r.detail}", file=sys.stderr)
            bad += 1
    return bad


def cmd_publish(args) -> int:
    repo = _repo_root()
    results_root = (repo / DEFAULT_RESULTS).resolve()
    ctx = BuildContext(repo_root=repo, results_root=results_root)
    pairs = []
    for raw in args.run_dirs or [str(p) for p in discover(results_root)]:
        run_dir = Path(raw).resolve()
        report, viols, md = build_one(
            run_dir, ctx, write=False, allow_violations=args.allow_violations
        )
        if report is None or (viols and not args.allow_violations):
            _print_violations(run_dir, viols)
            return 1
        pairs.append((report, md))
    return _publish(pairs, None, allow_violations=args.allow_violations, dry_run=args.dry_run)


def publishable(reports: list[RunReport]) -> list[RunReport]:
    """Which runs the public page should show.

    Not "the most recent", which lets a two-case smoke run written five minutes ago
    displace the hundred-case result it was smoke-testing. For each paper benchmark
    the most *authoritative* run wins: largest scored N, latest date breaking ties.
    The flow suite is different in kind — each agent type is its own subject — so it
    contributes the latest run of every series rather than one row.
    """
    best: dict[str, RunReport] = {}
    flows: dict[str, RunReport] = {}
    for r in reports:
        if r.benchmark in web_export.FLOW_BENCHMARKS:
            cur = flows.get(r.series)
            if cur is None or (r.date or "", r.run_id) > (cur.date or "", cur.run_id):
                flows[r.series] = r
            continue
        cur = best.get(r.benchmark)
        if cur is None or (r.n_scored, r.date or "") > (cur.n_scored, cur.date or ""):
            best[r.benchmark] = r
    return sorted(best.values(), key=lambda r: r.benchmark) + sorted(
        flows.values(), key=lambda r: r.series
    )


def cmd_check(args) -> int:
    """Audit without writing — the shape CI wants."""
    args.dry_run = True
    return cmd_all(args)


def cmd_list(args) -> int:
    repo = _repo_root()
    results_root = (repo / DEFAULT_RESULTS).resolve()
    for run_dir in discover(results_root):
        a = adapter_for(run_dir)
        print(f"{a.benchmark:22s} {run_dir.relative_to(results_root)}")
    print(f"\nadapters registered: {', '.join(a.benchmark for a in ADAPTERS)}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="tau2.reporting", description=__doc__)
    ap.add_argument(
        "--allow-violations",
        action="store_true",
        help="write the report even if an honesty rule fails (prints them regardless)",
    )
    ap.add_argument("--dry-run", action="store_true", help="audit without writing")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build one or more run reports")
    b.add_argument("run_dirs", nargs="+")
    b.set_defaults(func=cmd_build)

    a = sub.add_parser("all", help="build every run, the index and the website export")
    a.add_argument("--web-out", default="web/benchmark_export.json")
    a.add_argument(
        "--publish",
        action="store_true",
        help="also POST the publishable runs and the history to the results store "
        "(needs WHISSLE_BASE and WHISSLE_API_KEY); idempotent on run id",
    )
    a.set_defaults(func=cmd_all)

    pub = sub.add_parser(
        "publish",
        help="POST one or more runs to the results store (idempotent on run id)",
    )
    pub.add_argument("run_dirs", nargs="*", help="default: every recognised run")
    pub.set_defaults(func=cmd_publish)

    c = sub.add_parser("check", help="audit every run without writing")
    c.add_argument("--web-out", default="web/benchmark_export.json")
    c.set_defaults(func=cmd_check)

    ls = sub.add_parser("list", help="list recognised run directories")
    ls.set_defaults(func=cmd_list)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
