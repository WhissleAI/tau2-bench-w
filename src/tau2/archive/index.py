# Copyright Sierra
"""INDEX.md — every run ever archived, newest first.

Rebuilt by scanning the archive tree, not accumulated in a sidecar. That is the
opposite choice from ``tau2.reporting.index``, and deliberately so: that index
accumulates because its run directories get deleted from the working tree and the
history must outlive them. This one is the durable store — if a folder is gone, the
run is gone, and an index that still listed it would be describing something a reader
cannot open. Rebuilding from disk means the index can never disagree with the tree.

Four columns carry the caveats rather than hiding them in each manifest: modality
(because two runs were published as voice having been driven over text), the metadata
head (off in production, so every number is our cascade minus its differentiator),
the served model (failover means the arm label is a request), and cost.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .schema import MODALITY_NOT_RECORDED, NOT_RECORDED, SCHEMA, is_voice

INDEX_SCHEMA = "whissle.benchmark.archive.index/v1"


def _read(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a half-written manifest must not sink the index
        return None


#: A sibling manifest shape written by a suite that has its own writer. The index
#: reads it so those runs are LISTED rather than silently absent — a run that exists
#: on disk but not in the index is the exact failure this archive exists to prevent,
#: and a folder nobody can find is not archived. Their directories are never touched.
FOREIGN_SCHEMAS = ("whissle.benchmark.archive.manifest/v1",)


def _foreign_row(record: dict[str, Any], run_dir: Path, root: Path) -> dict[str, Any]:
    """Map a sibling writer's manifest onto an index row, best-effort.

    Fields that shape differently, or that the sibling does not record, land as
    ``not recorded`` rather than being guessed — the same rule the rest of the
    archive follows.
    """
    started = str(record.get("started_at") or "")
    return {
        "archive_id": run_dir.name,
        "suite": str(record.get("suite") or run_dir.parent.name),
        "arm": str(record.get("arm") or NOT_RECORDED),
        "modality": str(record.get("modality") or MODALITY_NOT_RECORDED),
        "date": started[:10] if len(started) >= 10 else NOT_RECORDED,
        "date_source": "artifact" if started else NOT_RECORDED,
        "archived_at": str(record.get("written_at") or NOT_RECORDED),
        "backfilled": False,
        "n_cases": record.get("n_scored") or record.get("n_total") or 0,
        "headline": None,
        "requested_model": str(record.get("model") or NOT_RECORDED),
        # A sibling writer records the model it REQUESTED; it has no served-model
        # ledger, so the served column stays honest rather than borrowing the request.
        "served_model": NOT_RECORDED,
        "failover": False,
        "metadata_head_in_path": bool(record.get("metadata_head_in_path")),
        "total_usd": None,
        "path": str(run_dir.relative_to(root)),
        "notes": 0,
        "foreign_schema": str(record.get("schema")),
        # A sibling writer may not produce a MANIFEST.md, so the row links to
        # whatever it actually wrote. A link to a file that does not exist is worse
        # than no link: it reads as a missing artifact rather than a different layout.
        "entrypoint": next(
            (name for name in ("MANIFEST.md", "MANIFEST.json", "REPORT.md",
                               "summary.json")
             if (run_dir / name).exists()),
            "",
        ),
    }


def scan(root: Path) -> list[dict[str, Any]]:
    """Every archived run under ``root``, as flat index rows."""
    rows: list[dict[str, Any]] = []
    if not root.is_dir():
        return rows

    # Sibling writers first, so a run this package did not write still appears.
    for manifest_path in sorted(root.glob("*/*/MANIFEST.json")):
        record = _read(manifest_path)
        if isinstance(record, dict) and record.get("schema") in FOREIGN_SCHEMAS:
            rows.append(_foreign_row(record, manifest_path.parent, root))

    for manifest_path in sorted(root.glob("*/*/manifest.json")):
        record = _read(manifest_path)
        if not record or record.get("schema") != SCHEMA:
            continue
        run_dir = manifest_path.parent
        served = record.get("served") or {}
        cost = record.get("cost") or {}
        head = (record.get("environment") or {}).get("metadata_head") or {}
        rows.append({
            "archive_id": record.get("archive_id", run_dir.name),
            "suite": record.get("suite", run_dir.parent.name),
            "arm": record.get("arm_raw") or record.get("arm") or NOT_RECORDED,
            "modality": record.get("modality", MODALITY_NOT_RECORDED),
            "date": (record.get("date") or {}).get("date", NOT_RECORDED),
            "date_source": (record.get("date") or {}).get("source", NOT_RECORDED),
            "archived_at": record.get("archived_at", NOT_RECORDED),
            "backfilled": bool(record.get("backfilled")),
            "n_cases": (record.get("counts") or {}).get("cases", 0),
            "headline": record.get("headline"),
            "requested_model": (record.get("requested") or {}).get("model", NOT_RECORDED),
            "served_model": served.get("dominant", NOT_RECORDED),
            "failover": bool(served.get("failover_observed")),
            "metadata_head_in_path": bool(head.get("in_path")),
            "total_usd": cost.get("total_usd"),
            "path": str(run_dir.relative_to(root)),
            "notes": len(record.get("notes") or []),
        })
    # Newest first. A run with no recoverable date sorts last rather than first — an
    # undated run is not "the oldest", it is unplaced, and putting it at the top of a
    # reverse-chronological list would misrepresent it as the most recent.
    rows.sort(
        key=lambda r: (
            r["date"] != NOT_RECORDED,
            r["date"] if r["date"] != NOT_RECORDED else "",
            r["archive_id"],
        ),
        reverse=True,
    )
    return rows


def _headline(row: dict[str, Any]) -> str:
    head = row.get("headline")
    if not isinstance(head, dict):
        return "—"
    value = head.get("formatted") or head.get("value")
    if value is None:
        return "—"
    label = head.get("label") or head.get("key") or ""
    n = row.get("n_cases") or head.get("n")
    return f"{value}{f' ({label})' if label else ''}{f' · N={n}' if n else ''}"


def render(rows: list[dict[str, Any]], root: Path) -> str:
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    suites = sorted({r["suite"] for r in rows})
    voice = sum(1 for r in rows if is_voice(r["modality"]))
    undated = sum(1 for r in rows if r["date"] == NOT_RECORDED)
    head_on = sum(1 for r in rows if r["metadata_head_in_path"])

    L: list[str] = []
    W = L.append
    W("# Whissle benchmark archive")
    W("")
    W(
        f"Every benchmark run this machine has archived — **{len(rows)} run(s)** "
        f"across **{len(suites)} suite(s)**, newest first. Each row links to a "
        "self-contained folder holding the run's own raw output, its per-case "
        "artifacts, its configuration, and a manifest that says what ran, when, "
        "against which revisions, and what the number does not support."
    )
    W("")
    W(f"_Rebuilt from disk {generated}. Root: `{root}`._")
    W("")

    W("## Read the columns")
    W("")
    W(
        f"- **Modality** — how the run was actually driven. {len(rows) - voice} of "
        f"{len(rows)} run(s) here were driven over **text**, not voice. A text run "
        "measures the language model and the tool loop; it says nothing about speech "
        "recognition, turn-taking, or latency under audio. This column exists because "
        "two runs were once published as \"Voice\" having been driven entirely over "
        "text.\n"
        f"- **Head** — was the whissle-large metadata head in the path? `off` on "
        f"{len(rows) - head_on} of {len(rows)} run(s): production STT routes to a "
        "third party, so these numbers measure our cascade **without** its "
        "distinguishing layer.\n"
        "- **Served** — the model that actually answered, from the response body, not "
        "the model the arm asked for. **⚠** marks a run where more than one model "
        "served turns, so the arm label does not describe all of them.\n"
        "- **Date** — from the artifact's own recorded timestamp. File mtime is never "
        f"used. {undated} run(s) have no recoverable date and are listed last."
    )
    W("")

    W("## All runs")
    W("")
    W("| Date | Suite | Arm | Modality | Head | Served | Headline | N | Cost | Path |")
    W("|---|---|---|---|:---:|---|---|---:|---:|---|")
    for r in rows:
        modality = r["modality"]
        mod_cell = (
            f"**{modality}**" if is_voice(modality)
            else f"_{modality}_" if modality == MODALITY_NOT_RECORDED
            else modality
        )
        served = r["served_model"]
        served_cell = (
            f"`{served}`" if served != NOT_RECORDED else "—"
        ) + (" ⚠" if r["failover"] else "")
        cost = f"${r['total_usd']:.2f}" if isinstance(r.get("total_usd"), (int, float)) else "—"
        date = r["date"] + ("*" if r["date_source"] == "dirname" else "")
        W(
            f"| {date} | {r['suite']} | `{r['arm']}`{' †' if r.get('foreign_schema') else ''} | {mod_cell} "
            f"| {'on' if r['metadata_head_in_path'] else '**off**'} "
            f"| {served_cell} | {_headline(r)} | {r['n_cases']} | {cost} "
            f"| [`{r['path']}`]({r['path']}/{r.get('entrypoint') or 'MANIFEST.md'}) |"
        )
    W("")
    W("_`*` on a date means it came from the run directory's name rather than from "
      "inside the artifact. Both are recorded; neither is an mtime._")
    W("")
    foreign = [r for r in rows if r.get("foreign_schema")]
    if foreign:
        W(
            f"_`†` marks {len(foreign)} run(s) written by a different writer "
            f"(`{foreign[0]['foreign_schema']}`). They are listed here so nothing on "
            "disk is missing from the index, but their folders keep their own layout "
            "and carry no served-model ledger or cost — those columns read "
            f"`{NOT_RECORDED}` rather than borrowing the requested model._"
        )
        W("")

    W("## By suite")
    W("")
    for suite in suites:
        srows = [r for r in rows if r["suite"] == suite]
        dated = [r["date"] for r in srows if r["date"] != NOT_RECORDED]
        W(
            f"- **{suite}** — {len(srows)} run(s)"
            + (f", {min(dated)} → {max(dated)}" if dated else ", no recoverable dates")
            + f", {sum(r['n_cases'] for r in srows)} archived case(s)"
        )
    W("")
    return "\n".join(L) + "\n"


def rebuild(root: Path) -> dict[str, Any]:
    """Rescan ``root`` and rewrite ``INDEX.md`` + ``index.json``."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    rows = scan(root)
    index = {
        "schema": INDEX_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(root),
        "n_runs": len(rows),
        "runs": rows,
    }
    (root / "index.json").write_text(
        json.dumps(index, indent=2, default=str) + "\n", encoding="utf-8")
    (root / "INDEX.md").write_text(render(rows, root), encoding="utf-8")
    return index
