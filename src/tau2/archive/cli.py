# Copyright Sierra
"""``python -m tau2.archive`` — archive runs, backfill history, rebuild the index.

    python -m tau2.archive backfill --dry-run     # what would be archived
    python -m tau2.archive backfill               # do it
    python -m tau2.archive index                  # rebuild INDEX.md from disk
    python -m tau2.archive where                  # print the archive root
    python -m tau2.archive verify                 # check every archived run

Nothing here runs a benchmark. Every command is a pure function of artifacts already
on disk, so it is safe to re-run at any time.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from . import backfill as backfill_mod
from . import index as index_mod
from .schema import MODALITY_NOT_RECORDED, NOT_RECORDED, SCHEMA
from .writer import ARCHIVE_DIR_ENV, archive_root


def _repo(start: Optional[Path] = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / "pyproject.toml").is_file() and (cand / "src" / "tau2").is_dir():
            return cand
    return p


def cmd_where(args) -> int:
    root = archive_root(args.root)
    print(root)
    print(f"  (override with ${ARCHIVE_DIR_ENV})")
    print(f"  exists: {root.exists()}")
    return 0


def cmd_backfill(args) -> int:
    repo = _repo()
    results = Path(args.results) if args.results else repo / "results" / "whissle"
    root = archive_root(args.root)
    print(f"backfilling {results}  ->  {root}")
    if args.dry_run:
        print("(dry run — nothing will be written)\n")

    rows = backfill_mod.run(
        results, root=args.root, repo=repo, dry_run=args.dry_run,
        include_modelsweep=not args.no_modelsweep,
    )

    by_suite: dict[str, list[dict]] = {}
    for row in rows:
        by_suite.setdefault(row["suite"], []).append(row)

    for suite in sorted(by_suite):
        print(f"\n{suite}")
        for row in sorted(by_suite[suite], key=lambda r: str(r.get("arm"))):
            status = "✗" if row["error"] else ("·" if args.dry_run else "✓")
            gaps = (
                "  [unrecoverable: " + ", ".join(row["unrecoverable"]) + "]"
                if row["unrecoverable"] else ""
            )
            print(
                f"  {status} {row['arm']:<34} {row['modality']:<12} "
                f"{row['date']:<12} ({row['date_source']}) "
                f"N={row['n_cases']}{gaps}"
            )
            if row["error"]:
                print(f"      ERROR {row['error']}")

    ok = sum(1 for r in rows if r["archived"])
    failed = [r for r in rows if r["error"]]
    undated = [r for r in rows if "date" in r["unrecoverable"]]
    unmodal = [r for r in rows if r["modality"] == MODALITY_NOT_RECORDED]
    unserved = [r for r in rows if "served model" in r["unrecoverable"]]
    uncosted = [r for r in rows if "token usage / cost" in r["unrecoverable"]]

    print("\n" + "─" * 72)
    print(f"{len(rows)} run(s) recognised, {ok} archived, {len(failed)} failed")
    print(
        f"unrecoverable: {len(undated)} without a date, {len(unmodal)} without a "
        f"modality, {len(unserved)} without a served model, {len(uncosted)} without "
        "token usage or cost"
    )
    if undated:
        print("  no recoverable date: " + ", ".join(
            f"{r['suite']}/{r['arm']}" for r in undated))
    if unmodal:
        print("  no recoverable modality: " + ", ".join(
            f"{r['suite']}/{r['arm']}" for r in unmodal))
    print(
        "  (served model, token usage and cost are unrecoverable for every run that "
        "predates the backend returning a `usage` block — the field is written as "
        f"{NOT_RECORDED!r}, never estimated)"
    )
    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
        print(f"\nwrote {args.json}")
    return 1 if failed else 0


def cmd_index(args) -> int:
    root = archive_root(args.root)
    idx = index_mod.rebuild(root)
    print(f"{idx['n_runs']} run(s) -> {root / 'INDEX.md'}")
    return 0


def cmd_rerender(args) -> int:
    """Rebuild every ``MANIFEST.md`` from its stored ``manifest.json``.

    This is the whole point of keeping the machine-readable record beside the human
    one: ``MANIFEST.md`` is *derived*, so improving the renderer does not mean
    re-running a benchmark or re-copying gigabytes of raw output. ``raw/``,
    ``cases/`` and ``manifest.json`` are never touched.
    """
    from . import manifest as manifest_mod

    root = archive_root(args.root)
    done = failed = 0
    for manifest_path in sorted(root.glob("*/*/manifest.json")):
        try:
            record = json.loads(manifest_path.read_text(encoding="utf-8"))
            if record.get("schema") != SCHEMA:
                continue
            (manifest_path.parent / "MANIFEST.md").write_text(
                manifest_mod.render(record), encoding="utf-8")
            done += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"✗ {manifest_path.parent}: {exc}", file=sys.stderr)
    print(f"re-rendered {done} MANIFEST.md file(s), {failed} failed")
    index_mod.rebuild(root)
    return 1 if failed else 0


def cmd_verify(args) -> int:
    """Check every archived run has the files and the required fields it claims.

    The point is that the archive can be audited without trusting the writer: a run
    missing ``raw/`` or carrying no modality is a real defect, and the only way to
    find one months later is to look.
    """
    root = archive_root(args.root)
    required = ("MANIFEST.md", "manifest.json", "config.json", "summary.json",
                "REPORT.md", "raw", "cases", "logs")
    problems = 0
    checked = 0
    for manifest_path in sorted(root.glob("*/*/manifest.json")):
        run_dir = manifest_path.parent
        checked += 1
        issues: list[str] = []
        try:
            record = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"✗ {run_dir.relative_to(root)}: unreadable manifest ({exc})")
            problems += 1
            continue
        if record.get("schema") != SCHEMA:
            issues.append(f"schema is {record.get('schema')!r}, expected {SCHEMA!r}")
        for name in required:
            if not (run_dir / name).exists():
                issues.append(f"missing {name}")
        if not record.get("modality"):
            issues.append("no modality recorded — this field is mandatory")
        head = (record.get("environment") or {}).get("metadata_head")
        if not head:
            issues.append("no metadata-head block")
        if issues:
            problems += 1
            print(f"✗ {run_dir.relative_to(root)}")
            for issue in issues:
                print(f"    {issue}")
    print(f"\n{checked} run(s) checked, {problems} with problems")
    return 1 if problems else 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="tau2.archive", description=__doc__)
    ap.add_argument(
        "--root", type=Path, default=None,
        help=f"archive root (default: ${ARCHIVE_DIR_ENV}, then "
             "~/Downloads/whissle_benchmarks)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("where", help="print the archive root")
    w.set_defaults(func=cmd_where)

    b = sub.add_parser("backfill", help="archive every run already under results/")
    b.add_argument("--results", type=Path, default=None, help="default: results/whissle")
    b.add_argument("--dry-run", action="store_true")
    b.add_argument("--no-modelsweep", action="store_true")
    b.add_argument("--json", type=Path, default=None, help="write the inventory as JSON")
    b.set_defaults(func=cmd_backfill)

    i = sub.add_parser("index", help="rebuild INDEX.md from the archive tree")
    i.set_defaults(func=cmd_index)

    r = sub.add_parser(
        "rerender",
        help="rebuild every MANIFEST.md from its stored manifest.json "
             "(raw/ and cases/ are never touched)",
    )
    r.set_defaults(func=cmd_rerender)

    v = sub.add_parser("verify", help="check every archived run is complete")
    v.set_defaults(func=cmd_verify)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
