# Copyright Sierra
"""The one writer every benchmark suite calls.

There is exactly one implementation of "archive a run", and every suite calls it.
That is the whole design. Per-suite archiving would drift the way the per-suite
result shapes already drifted — seven directories that each need their own reader —
which is the problem this exists to end. The precedent is
``tau2.health.diagnostics``: one shape, one module, versioned, and adapters that feed
it rather than reimplement it.

WHAT LANDS ON DISK
------------------
::

    $TAU2_ARCHIVE_DIR/<suite>/<YYYYMMDD_HHMMSS>_<arm>/
      MANIFEST.md     what ran, when, why, how to reproduce, which SHAs
      manifest.json   the same facts, machine-readable, schema-stamped
      config.json     model / provider / effort / N / seeds / judge / task set
      summary.json    headline metrics + CIs + N + exclusions
      REPORT.md       the research-paper writeup
      cases/          one file per case
      logs/           runner stdout/stderr, infra failures, retries, drop notices
      raw/            the harness's own output, byte-for-byte

THREE RULES THE WRITER ENFORCES RATHER THAN REQUESTS
----------------------------------------------------
**``raw/`` is never edited.** It is a verbatim copy of what the harness wrote, and
nothing in this module ever writes into it after the copy. Everything derived —
``cases/``, ``summary.json``, the report — is computed *from* it and stored beside
it, so a wrong analysis is always redoable from the original. A derived tree that
overwrote its source would make one bad post-processing step permanent.

**Nothing is silently overwritten.** A second run landing on an existing directory
gets ``__2``, ``__3``, … and both the new manifest and the archive log say what
happened. Timestamps collide more often than you would think (two arms of a sweep
started in the same second), and the failure mode of overwriting — a run that
silently becomes a different run — is unrecoverable.

**Nothing is silently dropped.** Every file that could not be copied, every section
that could not be built, every fact that could not be recovered lands in
``logs/archive.log`` and in the manifest's ``notes``. A quiet archive is not a clean
one; it is one whose failures you will discover later, from the gap.
"""
from __future__ import annotations

import json
import os
import shutil
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from . import cost as cost_mod
from . import env as env_mod
from . import manifest as manifest_mod
from .schema import (
    DATE_FROM_ARTIFACT,
    DATE_FROM_NOW,
    DIR_TIMESTAMP_FORMAT,
    NOT_RECORDED,
    SCHEMA,
    ArchiveError,
    DateProvenance,
    RequestedRecord,
    ServedRecord,
    require_modality,
    safe_arm,
)
from .serving import ServingLedger

#: Override the archive root. Set it in CI, on a shared box, or anywhere a user's
#: home directory is the wrong answer — the default must not be load-bearing.
ARCHIVE_DIR_ENV = "TAU2_ARCHIVE_DIR"

DEFAULT_ARCHIVE_DIR = Path.home() / "Downloads" / "whissle_benchmarks"

#: Files never copied into ``raw/`` — noise that would bloat every archive without
#: telling a reader anything.
RAW_SKIP = {".DS_Store", "__pycache__", ".pytest_cache", ".ipynb_checkpoints"}

#: A single raw file above this size is copied but flagged in the log, so an archive
#: that quietly became a gigabyte is visible rather than merely slow.
LARGE_FILE_BYTES = 64 * 1024 * 1024

#: Optional per-file ceiling, in megabytes. Unset by default: the archive's job is
#: that nothing lives only inside the repo, so the default is to copy everything.
#:
#: But flow-sim alone is 7.4 GB of session audio, and a machine where that is not
#: viable needs an answer other than "don't archive". Setting this skips individual
#: files above the ceiling — never a JSON artifact in practice, only media — and
#: every skipped file is listed by path and size in ``logs/archive.log`` and counted
#: in the manifest, with the source directory recorded so the bytes remain findable.
#: Skipping is therefore explicit and auditable; it is never silent, and it is never
#: the default.
MAX_RAW_FILE_MB_ENV = "TAU2_ARCHIVE_MAX_RAW_FILE_MB"


def _max_raw_file_bytes() -> Optional[int]:
    raw = os.getenv(MAX_RAW_FILE_MB_ENV)
    if not raw:
        return None
    try:
        mb = float(raw)
    except ValueError:
        return None
    return int(mb * 1024 * 1024) if mb > 0 else None


def archive_root(explicit: Optional[Path] = None) -> Path:
    """Where archives go: an explicit argument, then ``$TAU2_ARCHIVE_DIR``, then
    ``~/Downloads/whissle_benchmarks``."""
    if explicit:
        return Path(explicit).expanduser()
    from_env = os.getenv(ARCHIVE_DIR_ENV)
    return Path(from_env).expanduser() if from_env else DEFAULT_ARCHIVE_DIR


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, default=str, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


@dataclass
class ArchiveResult:
    """What an export produced — returned so a caller can print the path and a CI job
    can assert on it."""

    path: Path
    archive_id: str
    suite: str
    arm: str
    modality: str
    n_cases: int
    notes: list[str] = field(default_factory=list)
    collided: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "archive_id": self.archive_id,
            "suite": self.suite,
            "arm": self.arm,
            "modality": self.modality,
            "n_cases": self.n_cases,
            "collided": self.collided,
            "notes": self.notes,
        }


class _Log:
    """The archive's own account of itself. Everything the writer skipped, degraded
    or worked around is written here — this file is why ``logs/`` exists."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.notes: list[str] = []

    def write(self, message: str, *, note: bool = False) -> None:
        self.lines.append(f"{_now().isoformat(timespec='seconds')}  {message}")
        if note:
            self.notes.append(message)

    def exception(self, what: str, exc: BaseException) -> None:
        self.write(f"WARN  {what}: {type(exc).__name__}: {exc}", note=True)
        self.lines.append(traceback.format_exc())

    def render(self) -> str:
        header = (
            "# archive.log\n"
            "#\n"
            "# Everything this archive skipped, degraded or worked around. An empty\n"
            "# WARN section means nothing was dropped; it does not mean nothing was\n"
            "# checked.\n\n"
        )
        return header + "\n".join(self.lines) + "\n"


def _copy_raw(src: Path, dest: Path, log: _Log) -> dict[str, Any]:
    """Copy the harness's output verbatim. Per-file failures are logged and the copy
    continues — losing one unreadable file is better than losing the archive."""
    dest.mkdir(parents=True, exist_ok=True)
    copied = skipped = failed = 0
    total_bytes = 0
    ceiling = _max_raw_file_bytes()
    oversized: list[dict[str, Any]] = []

    if not src.exists():
        log.write(f"WARN  raw source {src} does not exist — raw/ is empty", note=True)
        return {"copied": 0, "skipped": 0, "failed": 0, "bytes": 0,
                "source": str(src), "source_existed": False}

    if src.is_file():
        try:
            shutil.copy2(src, dest / src.name)
            return {"copied": 1, "skipped": 0, "failed": 0,
                    "bytes": src.stat().st_size, "source": str(src),
                    "source_existed": True}
        except Exception as exc:  # noqa: BLE001
            log.exception(f"copying {src}", exc)
            return {"copied": 0, "skipped": 0, "failed": 1, "bytes": 0,
                    "source": str(src), "source_existed": True}

    for item in sorted(src.rglob("*")):
        if any(part in RAW_SKIP for part in item.parts):
            skipped += 1
            continue
        rel = item.relative_to(src)
        target = dest / rel
        try:
            if item.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            size = item.stat().st_size
            if ceiling is not None and size > ceiling:
                oversized.append({"path": str(rel), "bytes": size,
                                  "source": str(item)})
                skipped += 1
                continue
            if size > LARGE_FILE_BYTES:
                log.write(f"NOTE  large raw file {rel} ({size/1e6:.1f} MB) — copied")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            copied += 1
            total_bytes += size
        except Exception as exc:  # noqa: BLE001
            failed += 1
            log.exception(f"copying {rel}", exc)

    log.write(
        f"raw/ <- {src}: {copied} file(s), {total_bytes/1e6:.2f} MB, "
        f"{skipped} skipped, {failed} failed"
    )
    if failed:
        log.write(
            f"WARN  {failed} raw file(s) could not be copied — raw/ is INCOMPLETE",
            note=True,
        )
    if oversized:
        dropped_bytes = sum(f["bytes"] for f in oversized)
        log.write(
            f"WARN  {len(oversized)} raw file(s) totalling {dropped_bytes/1e6:.1f} MB "
            f"were NOT copied: each exceeds the ${MAX_RAW_FILE_MB_ENV} ceiling of "
            f"{(ceiling or 0)/1e6:.0f} MB. `raw/` is INCOMPLETE for this run. The "
            f"files remain at {src}. Every one is listed below.",
            note=True,
        )
        for entry in oversized:
            log.write(f"      skipped {entry['path']} ({entry['bytes']/1e6:.1f} MB)")
    return {"copied": copied, "skipped": skipped, "failed": failed,
            "bytes": total_bytes, "source": str(src), "source_existed": True,
            "oversized_skipped": oversized or None,
            "oversized_skipped_bytes": sum(f["bytes"] for f in oversized) or None,
            "size_ceiling_bytes": ceiling}


def _write_cases(cases: Any, dest: Path, log: _Log) -> int:
    """One file per case. Accepts a list of records (keyed by any of the usual id
    fields) or a pre-built ``{case_id: record}`` mapping."""
    dest.mkdir(parents=True, exist_ok=True)
    if isinstance(cases, dict):
        items = list(cases.items())
    else:
        items = []
        for i, record in enumerate(cases or []):
            cid = None
            if isinstance(record, dict):
                for key in ("case_id", "id", "task_id", "case", "sample_id", "scenario"):
                    if record.get(key):
                        cid = str(record[key])
                        break
            items.append((cid or f"case_{i:04d}", record))

    written = 0
    seen: set[str] = set()
    for cid, record in items:
        safe = str(cid).replace(os.sep, "_").replace("/", "_").replace(":", "_")
        # Two cases with the same id would otherwise silently become one file.
        if safe in seen:
            n = 2
            while f"{safe}__{n}" in seen:
                n += 1
            log.write(
                f"NOTE  duplicate case id {cid!r} -> written as {safe}__{n}.json",
                note=True,
            )
            safe = f"{safe}__{n}"
        seen.add(safe)
        try:
            _json(dest / f"{safe}.json", record)
            written += 1
        except Exception as exc:  # noqa: BLE001
            log.exception(f"writing case {cid}", exc)
    log.write(f"cases/: {written} case file(s)")
    return written


def _write_logs(logs: Optional[dict[str, str]], dest: Path, log: _Log) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, content in (logs or {}).items():
        safe = str(name).replace(os.sep, "_").replace("/", "_")
        if not safe.endswith((".log", ".txt", ".jsonl")):
            safe += ".log"
        try:
            (dest / safe).write_text(str(content), encoding="utf-8")
            written += 1
        except Exception as exc:  # noqa: BLE001
            log.exception(f"writing log {name}", exc)
    return written


def _report(run_dir: Path, archive_dir: Path, log: _Log) -> Optional[str]:
    """Render REPORT.md with the existing research-grade generator.

    ``tau2.reporting`` already builds a report that answers the six questions a
    number needs (N, exclusions, judge independence, comparability, failure modes,
    what a reviewer would refuse to conclude) and enforces them with honesty rules.
    Re-implementing a weaker version here would be a second, worse report — so the
    archive calls it and records honestly when no adapter recognises the run.
    """
    try:
        from tau2.reporting import honesty, render_md
        from tau2.reporting.adapters import BuildContext, adapter_for

        adapter = adapter_for(run_dir)
        if adapter is None:
            log.write(
                "NOTE  no tau2.reporting adapter recognises this run directory — "
                "REPORT.md is a structured stub rather than a full report",
                note=True,
            )
            return None
        ctx = BuildContext(repo_root=env_mod.harness_repo(), results_root=run_dir.parent)
        report = adapter.build(run_dir, ctx)
        markdown = render_md.render(report)
        violations = honesty.audit(report, markdown)
        if violations:
            log.write(
                "NOTE  report generated with "
                f"{len(violations)} honesty-rule violation(s): "
                + "; ".join(str(v) for v in violations),
                note=True,
            )
            markdown += (
                "\n\n---\n\n## Honesty-rule violations recorded at archive time\n\n"
                "These were raised by `tau2.reporting.honesty` when this archive was "
                "written. They are reproduced here rather than suppressed, because a "
                "report that hides its own failed checks is the exact failure the "
                "rules exist to prevent.\n\n"
                + "\n".join(f"- {v}" for v in violations)
                + "\n"
            )
        _json(archive_dir / "report.json", report.to_dict())
        return markdown
    except Exception as exc:  # noqa: BLE001 — a report failure never loses the archive
        log.exception("rendering REPORT.md via tau2.reporting", exc)
        return None


def _stub_report(ctx: dict[str, Any]) -> str:
    """The report for a run no adapter recognises.

    Deliberately shaped like the real one — an empty section that names what is
    missing is a to-do; a section quietly omitted is a claim that it did not apply.
    """
    L = [
        f"# {ctx['suite']} — {ctx['arm']}",
        "",
        "> **This is a structured stub, not a generated report.** No "
        "`tau2.reporting` adapter recognises this run's directory shape, so the "
        "sections below are the questions a reader must answer from `raw/` and "
        "`summary.json` by hand. Writing an adapter for this suite turns this page "
        "into the full research-grade report the other suites get.",
        "",
        "## 1. What was measured",
        "",
        f"- **Suite** — {ctx['suite']}",
        f"- **Arm** — {ctx['arm']}",
        f"- **Modality** — `{ctx['modality']}`. {ctx['modality_meaning']}",
        f"- **Date** — {ctx['date']} (source: {ctx['date_source']})",
        f"- **Cases archived** — {ctx['n_cases']}",
        "",
        "## 2. How it was measured",
        "",
        "See `config.json` for the full configuration and `MANIFEST.md` for the "
        "environment, the exact reproduce command, and the harness/backend "
        "revisions.",
        "",
        "## 3. Result",
        "",
        "```json",
        json.dumps(ctx.get("summary") or {}, indent=2, default=str)[:4000],
        "```",
        "",
        "## 4. What this run does not support",
        "",
        f"- {ctx['modality_caveat']}",
        f"- {ctx['metadata_caveat']}",
        "- No adapter-generated exclusion accounting, confidence interval, or "
        "failure taxonomy exists for this run. A headline quoted from "
        "`summary.json` alone carries none of those, and should be qualified "
        "accordingly.",
        "",
        "## 5. Artifacts",
        "",
        "- `raw/` — the harness's own output, unmodified. Every derived number here "
        "is recomputable from it.",
        "- `cases/` — one file per case.",
        "- `logs/archive.log` — what this archive skipped or could not recover.",
        "",
    ]
    return "\n".join(L)


def export_run(
    *,
    suite: str,
    modality: str,
    arm: str = "",
    run_dir: Optional[Path] = None,
    config: Optional[dict[str, Any]] = None,
    summary: Optional[dict[str, Any]] = None,
    cases: Any = None,
    logs: Optional[dict[str, str]] = None,
    ledger: Optional[ServingLedger] = None,
    requested: Optional[RequestedRecord] = None,
    reproduce_command: str = "",
    reproduce_notes: Optional[list[str]] = None,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
    date: Optional[DateProvenance] = None,
    headline: Optional[dict[str, Any]] = None,
    agent_id: Optional[str] = None,
    base_url: str = "",
    notes: Optional[list[str]] = None,
    root: Optional[Path] = None,
    backfill: bool = False,
    probe_backend: Optional[bool] = None,
    environment: Optional[dict[str, Any]] = None,
    extra: Optional[dict[str, Any]] = None,
    update_index: bool = True,
) -> ArchiveResult:
    """Archive one benchmark run. The single entry point for every suite.

    ``modality`` has no default on purpose — see
    :func:`tau2.archive.schema.require_modality`. Everything else degrades to an
    explicit ``not recorded``.
    """
    log = _Log()
    modality = require_modality(modality, backfill=backfill)
    suite = safe_arm(suite).lower()
    arm_label = safe_arm(arm)

    if probe_backend is None:
        # A backfill describes a run that already happened; probing the backend now
        # would record TODAY's environment against a run from weeks ago, which is a
        # more convincing lie than recording nothing.
        probe_backend = not backfill

    # ---- date -------------------------------------------------------------
    if date is None:
        moment = started_at or finished_at
        if moment is not None:
            date = DateProvenance(
                date=moment.strftime("%Y-%m-%d"),
                timestamp=moment.isoformat(timespec="seconds"),
                source=DATE_FROM_ARTIFACT,
                source_detail="run start time supplied by the runner",
            )
        elif backfill:
            date = DateProvenance()  # unrecoverable — never guessed from mtime
        else:
            now = _now()
            date = DateProvenance(
                date=now.strftime("%Y-%m-%d"),
                timestamp=now.isoformat(timespec="seconds"),
                source=DATE_FROM_NOW,
                source_detail="the archive writer's clock at export time",
            )

    # ---- destination ------------------------------------------------------
    stamp_source = started_at or finished_at
    if stamp_source is None and date.timestamp != NOT_RECORDED:
        try:
            stamp_source = datetime.fromisoformat(date.timestamp.replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            stamp_source = None
    stamp = (stamp_source or _now()).strftime(DIR_TIMESTAMP_FORMAT)

    base = archive_root(root)
    archive_id = f"{stamp}_{arm_label}"
    dest = base / suite / archive_id
    collided = False
    if dest.exists():
        n = 2
        while (base / suite / f"{archive_id}__{n}").exists():
            n += 1
        collided = True
        original, archive_id = archive_id, f"{archive_id}__{n}"
        dest = base / suite / archive_id
        log.write(
            f"WARN  {base / suite / original} already exists — this run was written "
            f"to {archive_id} instead. Nothing was overwritten; both runs are intact.",
            note=True,
        )
    dest.mkdir(parents=True, exist_ok=True)
    log.write(f"archive {archive_id} -> {dest}")

    # ---- raw (verbatim, first, before anything derived) --------------------
    raw_info = (
        _copy_raw(Path(run_dir), dest / "raw", log)
        if run_dir else
        {"copied": 0, "skipped": 0, "failed": 0, "bytes": 0,
         "source": NOT_RECORDED, "source_existed": False}
    )
    if not run_dir:
        (dest / "raw").mkdir(exist_ok=True)
        log.write(
            "WARN  no run_dir was supplied, so raw/ is EMPTY — every number in this "
            "archive is unverifiable against original harness output",
            note=True,
        )

    # ---- cases + logs -----------------------------------------------------
    n_cases = _write_cases(cases, dest / "cases", log) if cases else 0
    if not cases:
        (dest / "cases").mkdir(exist_ok=True)
        log.write("NOTE  no per-case records supplied — cases/ is empty", note=True)
    _write_logs(logs, dest / "logs", log)

    # ---- served + cost ----------------------------------------------------
    served = ledger.served() if ledger else ServedRecord()
    per_model = ledger.usage_by_model() if ledger else {}
    extra_usd = ledger.extra_usd() if ledger else {}
    cost_block = cost_mod.compute(per_model, n_cases=n_cases or None, extra_usd=extra_usd)
    if ledger:
        _json(dest / "serving.json", ledger.to_dict())
        if served.failover_observed:
            log.write(
                "WARN  more than one model served this run "
                f"({served.models}) — provider failover occurred and the arm label "
                "does not describe every turn",
                note=True,
            )
    else:
        log.write(
            "NOTE  no serving ledger supplied — the model that actually served this "
            "run is not recorded, and the requested model is the only evidence",
            note=True,
        )

    # ---- environment ------------------------------------------------------
    if environment is not None:
        env_block = environment
        log.write("environment supplied by the caller (not re-probed)")
    else:
        env_block = env_mod.capture(
            modality=modality, base_url=base_url, agent_id=agent_id,
            probe_backend=probe_backend,
        ).to_dict()
        if not probe_backend:
            log.write(
                "NOTE  backend was not probed (backfill or probing disabled), so the "
                "metadata-head block reflects the recorded environment only",
                note=True,
            )
    if not env_block.get("metadata_head", {}).get("in_path", False):
        log.write(
            "NOTE  whissle metadata head NOT in path for this run: "
            + str(env_block.get("metadata_head", {}).get("reason", NOT_RECORDED))
        )

    # ---- the manifest -----------------------------------------------------
    record: dict[str, Any] = {
        "schema": SCHEMA,
        "archive_id": archive_id,
        "suite": suite,
        "arm": arm_label,
        "arm_raw": arm or NOT_RECORDED,
        "modality": modality,
        "modality_meaning": manifest_mod.modality_meaning(modality),
        "backfilled": bool(backfill),
        "archived_at": _now().isoformat(timespec="seconds"),
        "date": date.to_dict(),
        "run": {
            "started_at": started_at.isoformat(timespec="seconds") if started_at else NOT_RECORDED,
            "finished_at": finished_at.isoformat(timespec="seconds") if finished_at else NOT_RECORDED,
            "duration_seconds": (
                round((finished_at - started_at).total_seconds(), 1)
                if started_at and finished_at else None
            ),
        },
        "requested": (requested or RequestedRecord()).to_dict(),
        "served": served.to_dict(),
        "cost": cost_block,
        "environment": env_block,
        "headline": headline or None,
        "counts": {
            "cases": n_cases,
            "raw_files": raw_info["copied"],
            "raw_bytes": raw_info["bytes"],
            "raw_failed": raw_info["failed"],
            "raw_source": raw_info["source"],
            "raw_oversized_skipped": raw_info.get("oversized_skipped"),
            "raw_oversized_skipped_bytes": raw_info.get("oversized_skipped_bytes"),
            "raw_size_ceiling_bytes": raw_info.get("size_ceiling_bytes"),
            "raw_complete": not (
                raw_info["failed"] or raw_info.get("oversized_skipped")),
        },
        "reproduce": {
            "command": reproduce_command or NOT_RECORDED,
            "notes": reproduce_notes or [],
            "harness_sha": env_block.get("harness_git", {}).get("sha", NOT_RECORDED),
            "backend_sha": env_block.get("backend", {}).get("sha", NOT_RECORDED),
        },
        "notes": list(notes or []),
        **(extra or {}),
    }

    # ---- files ------------------------------------------------------------
    _json(dest / "config.json", config or {
        "_note": "no configuration was recorded for this run", "recovered": False})
    _json(dest / "summary.json", summary or {
        "_note": "no summary was recorded for this run", "recovered": False})

    markdown = _report(Path(run_dir), dest, log) if run_dir else None
    if markdown is None:
        markdown = _stub_report({
            "suite": suite, "arm": arm_label, "modality": modality,
            "modality_meaning": manifest_mod.modality_meaning(modality),
            "date": date.date, "date_source": date.source, "n_cases": n_cases,
            "summary": summary,
            "modality_caveat": manifest_mod.modality_caveat(modality),
            "metadata_caveat": env_block.get("metadata_head", {}).get(
                "reason", NOT_RECORDED),
        })
    (dest / "REPORT.md").write_text(markdown, encoding="utf-8")

    # The archive log is written last so it carries every note raised above; its
    # notes are folded back into the manifest so a reader who opens only
    # MANIFEST.md still sees what was dropped.
    record["notes"] = list(record["notes"]) + log.notes
    record["archive_log"] = "logs/archive.log"
    _json(dest / "manifest.json", record)
    (dest / "MANIFEST.md").write_text(manifest_mod.render(record), encoding="utf-8")
    (dest / "logs").mkdir(exist_ok=True)
    (dest / "logs" / "archive.log").write_text(log.render(), encoding="utf-8")

    if update_index:
        try:
            from .index import rebuild

            rebuild(base)
        except Exception as exc:  # noqa: BLE001 — index failure never loses a run
            log.exception("rebuilding INDEX.md", exc)
            (dest / "logs" / "archive.log").write_text(log.render(), encoding="utf-8")

    return ArchiveResult(
        path=dest, archive_id=archive_id, suite=suite, arm=arm_label,
        modality=modality, n_cases=n_cases, notes=log.notes, collided=collided,
    )


def export_hook(
    suite: str,
    modality: str,
    **defaults: Any,
) -> Callable[..., Optional[ArchiveResult]]:
    """A pre-bound, never-raising :func:`export_run` for a suite's end-of-run path.

    Suites call this at the moment they finish writing their results. It swallows
    every failure by design: an archive is a downstream convenience, and a benchmark
    that has already produced its numbers must never lose them because a copy to
    ``~/Downloads`` failed. Failures print to stderr rather than vanishing.
    """

    def hook(**kwargs: Any) -> Optional[ArchiveResult]:
        merged = {**defaults, **kwargs}
        merged.setdefault("modality", modality)
        try:
            return export_run(suite=suite, **merged)
        except Exception as exc:  # noqa: BLE001
            import sys

            print(
                f"[archive] {suite}: export failed ({type(exc).__name__}: {exc}) — "
                "the run's own results under results/ are untouched",
                file=sys.stderr,
            )
            return None

    return hook
