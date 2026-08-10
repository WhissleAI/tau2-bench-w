# Copyright Sierra
"""``python -m tau2.flow.adherence_cli`` — score flow adherence from session files.

Scores what is already on disk. It runs no sessions and needs no credentials,
which is the point: the analyzer's findings were already being written into
every session sidecar, so the metric is recoverable for every run the suite has
ever done, retroactively, without spending a call.

    # one domain
    python -m tau2.flow.adherence_cli results/whissle/flow_sim/headache_enrollment

    # the whole suite, per-domain plus a total
    python -m tau2.flow.adherence_cli results/whissle/flow_sim --per-domain

    # the same scenarios in both modalities
    python -m tau2.flow.adherence_cli --text results/.../text --voice results/.../voice

`--json` prints the machine-readable form for the reporting layer.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from tau2.flow.adherence import AdherenceReport, score_parity, score_sessions


def load_sessions(path: Path, latest_only: bool = True) -> list[dict]:
    """Every session sidecar under ``path``.

    The directory is append-only across runs, so by default only the newest
    session per task id is kept — the same rule the report adapter uses. Mixing
    a task's failed first attempt with its passing re-run would score the same
    scenario twice and let a flaky case dominate the density figure.
    """
    files = sorted(path.glob("**/*.session.json"))
    out: list[dict] = []
    for f in files:
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            print(f"  ! unreadable, skipped: {f} ({e})", file=sys.stderr)
    if not latest_only:
        return out

    best: dict[str, tuple[str, dict]] = {}
    for s in out:
        tid = str(s.get("task_id") or "?")
        ts = str(s.get("ts") or "")
        if tid not in best or ts > best[tid][0]:
            best[tid] = (ts, s)
    return [s for _, s in best.values()]


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.1f}%"


def _num(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.1f}"


def print_report(title: str, r: AdherenceReport) -> None:
    print(f"\n{title}")
    print("─" * max(24, len(title)))
    print(f"  Clean-session rate     {_pct(r.clean_session_rate)}   "
          f"({r.clean_sessions} of {r.scored_sessions} scored sessions had no high-severity finding)")
    print(f"  Spotless-session rate  {_pct(r.spotless_session_rate)}   "
          f"({r.spotless_sessions} had no finding at all)")
    print(f"  Findings / 100 turns   {_num(r.findings_per_100_turns)}   "
          f"({sum(r.findings_by_type.values())} findings over {r.turns} turns)")
    if r.excluded_sessions:
        print(f"  Excluded               {r.excluded_sessions} session(s), "
              f"{_pct(r.exclusion_rate)} — {dict(sorted(r.exclusion_breakdown.items()))}")
        print("                         (never ran; excluded rather than scored as failures)")
    if r.findings_by_type:
        print("\n  By type (count, per 100 turns):")
        for t, n in sorted(r.findings_by_type.items(), key=lambda x: -x[1]):
            print(f"    {t:<24} {n:>4}   {_num(r.density(t))}")
    else:
        print("\n  No adherence findings.")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="tau2.flow.adherence_cli", description=__doc__)
    ap.add_argument("run_dir", nargs="?", type=Path, help="a flow_sim run directory")
    ap.add_argument("--per-domain", action="store_true",
                    help="score each immediate subdirectory separately, then the total")
    ap.add_argument("--text", type=Path, help="text-arm directory, for a parity run")
    ap.add_argument("--voice", type=Path, help="voice-arm directory, for a parity run")
    ap.add_argument("--all-attempts", action="store_true",
                    help="score every session on disk, not just the latest per task")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    ap.add_argument("--out", type=Path, help="also write the JSON here")
    a = ap.parse_args(argv)

    latest = not a.all_attempts
    payload: dict[str, Any]

    if a.text or a.voice:
        if not (a.text and a.voice):
            ap.error("--text and --voice must be given together")
        p = score_parity(load_sessions(a.text, latest), load_sessions(a.voice, latest))
        payload = {"schema": "tau2.flow.adherence_parity/v1", **p.as_dict()}
        if not a.json:
            print_report("Text arm", p.text)
            print_report("Voice arm", p.voice)
            print(f"\nParity\n──────\n  {p.note}")
            if p.clean_rate_gap is not None:
                print(f"  Clean-session rate, text minus voice: {p.clean_rate_gap:+.1f} points")
    elif a.run_dir:
        if not a.run_dir.is_dir():
            ap.error(f"not a directory: {a.run_dir}")
        domains: dict[str, AdherenceReport] = {}
        if a.per_domain:
            for d in sorted(x for x in a.run_dir.iterdir() if x.is_dir()):
                s = load_sessions(d, latest)
                if s:
                    domains[d.name] = score_sessions(s)
        total = score_sessions(load_sessions(a.run_dir, latest))
        payload = {
            "schema": "tau2.flow.adherence/v1",
            "run_dir": str(a.run_dir),
            "total": total.as_dict(),
            "by_domain": {k: v.as_dict() for k, v in domains.items()},
        }
        if not a.json:
            for name, r in domains.items():
                print_report(name, r)
            print_report("TOTAL" if domains else a.run_dir.name, total)
    else:
        ap.error("give a run directory, or --text and --voice")

    if a.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    if a.out:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n→ {a.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
