"""Write a run into the shared benchmark archive.

Layout, which is not ours to invent:

    ~/Downloads/whissle_benchmarks/<suite>/<timestamp>_<arm>/
        MANIFEST.json      what this run is, unambiguously
        config.json        everything needed to reproduce it
        summary.json       the machine-readable result
        REPORT.md          the human-readable result
        cases/             per-case artifacts
        logs/              stdout/stderr of the run
        raw/               untransformed responses

Two fields in ``MANIFEST.json`` exist because getting them wrong has already
caused real damage: two earlier runs were labelled "Voice" while being driven
entirely over text, and the mislabelling came from artifacts that did not say.
So both are recorded explicitly, never inferred:

``modality``               ``text`` | ``voice`` — the transport actually used.
``metadata_head_in_path``  whether the whissle-large metadata head was producing
                           for this run, and by which path. ``false`` is the
                           correct value for anything driven over the live voice
                           pipeline today, where the head is not running.

One directory per **arm**, because an arm is the unit a reader compares. A paired
run writes one directory per arm plus a ``_paired`` directory holding the
comparison, so neither the arms nor the comparison has to be reconstructed.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ARCHIVE_ROOT = Path.home() / "Downloads" / "whissle_benchmarks"
SUITE = "metadata_ablation"


def _write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, str):
        path.write_text(obj, encoding="utf-8")
    else:
        path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str),
                        encoding="utf-8")


def manifest(summary: dict[str, Any], *, arm: str, modality: str,
             metadata_head_in_path: bool, head_path: str,
             notes: str = "") -> dict[str, Any]:
    dec = summary.get("decoding") or {}
    corpus = summary.get("corpus") or {}
    return {
        "schema": "whissle.benchmark.archive.manifest/v1",
        "suite": SUITE,
        "run_id": summary.get("run_id"),
        "arm": arm,
        "layer": summary.get("layer", "ablation"),
        "written_at": datetime.now(timezone.utc).isoformat(),
        "started_at": summary.get("started_at"),
        "finished_at": summary.get("finished_at"),
        # -- the two fields that must never be inferred ---------------------
        "modality": modality,
        "modality_evidence": (
            "driven over POST /api/bench/agent-turn, a stateless text brain call; "
            "no LiveKit room, no audio transport, no turn-taking"
            if modality == "text" else "live voice transport"),
        "metadata_head_in_path": metadata_head_in_path,
        "metadata_head_path": head_path,
        "metadata_head_note": (
            "The whissle-large metadata head is NOT running on the live voice path: "
            "production STT routes to AssemblyAI/Sarvam/Deepgram and the sidecar is "
            "gated behind WHISSLE_STT_TRANSPORT=grpc. This run obtained the head's "
            "output through the BATCH path (/api/models/transcribe → "
            "whissle_batch_metadata), which is serving."),
        # -------------------------------------------------------------------
        "model": dec.get("model"),
        "provider": dec.get("provider"),
        "thinking": dec.get("thinking"),
        "agent_id": dec.get("agent_id"),
        "n_total": summary.get("n_total"),
        "n_scored": summary.get("n_comparable", summary.get("n_with_substrate")),
        "n_excluded": summary.get("n_excluded"),
        "corpus_version": corpus.get("version"),
        "corpus_digest": corpus.get("digest_of_run") or corpus.get("digest"),
        "endpoint": "POST /api/bench/agent-turn",
        "base_url": os.getenv("WHISSLE_BASE"),
        "notes": notes,
    }


def write_run(run_dir: Path, summary: dict[str, Any], *,
              report_md: Optional[str] = None,
              modality: str = "text",
              metadata_head_in_path: bool = True,
              head_path: str = "batch (/api/models/transcribe → whissle_batch_metadata)",
              log_path: Optional[Path] = None,
              archive_root: Optional[Path] = None) -> list[Path]:
    """Write one archive directory per arm, plus a ``_paired`` comparison."""
    root = Path(archive_root or ARCHIVE_ROOT) / SUITE
    ts = (summary.get("started_at") or datetime.now(timezone.utc).isoformat())
    ts = ts.replace(":", "").replace("-", "").split(".")[0]
    run_dir = Path(run_dir)
    written: list[Path] = []

    records = []
    rp = run_dir / "records.json"
    if rp.exists():
        try:
            records = json.loads(rp.read_text())
        except Exception:
            records = []

    arm_keys = [a["key"] for a in summary.get("arms") or []] or ["substrate"]
    for arm in arm_keys:
        d = root / f"{ts}_{arm}"
        _write(d / "MANIFEST.json", manifest(
            summary, arm=arm, modality=modality,
            metadata_head_in_path=metadata_head_in_path, head_path=head_path))
        _write(d / "config.json", {
            "decoding": summary.get("decoding"),
            "arms": summary.get("arms"),
            "corpus": summary.get("corpus"),
            "metadata_head": summary.get("metadata_head"),
            "reproduce": [
                "uv run python -m tau2.ablation freeze",
                f"uv run python -m tau2.ablation run --arms {','.join(arm_keys)} "
                f"--run-name {summary.get('run_id')}",
            ],
        })
        per_arm = (summary.get("per_arm") or {}).get(arm, {})
        _write(d / "summary.json", {
            "arm": arm, "per_arm": per_arm,
            "run_id": summary.get("run_id"),
            "n_scored": per_arm.get("n"),
            "structural_audit": summary.get("structural_audit"),
        })
        for rec in records:
            arms = rec.get("arms") or {}
            if arm in arms or not arms:
                _write(d / "cases" / f"{rec['case_id']}.json",
                       {**{k: v for k, v in rec.items() if k != "arms"},
                        "arm": arms.get(arm)})
        raw = run_dir / "cases"
        if raw.is_dir():
            (d / "raw").mkdir(parents=True, exist_ok=True)
            for f in raw.glob("*.json"):
                shutil.copy2(f, d / "raw" / f.name)
        if log_path and Path(log_path).exists():
            (d / "logs").mkdir(parents=True, exist_ok=True)
            shutil.copy2(log_path, d / "logs" / "run.log")
        if report_md:
            _write(d / "REPORT.md", report_md)
        written.append(d)

    if len(arm_keys) > 1:
        d = root / f"{ts}_paired"
        _write(d / "MANIFEST.json", manifest(
            summary, arm="paired", modality=modality,
            metadata_head_in_path=metadata_head_in_path, head_path=head_path,
            notes="the paired comparison across arms — the unit the report reads"))
        _write(d / "summary.json", summary)
        if report_md:
            _write(d / "REPORT.md", report_md)
        if log_path and Path(log_path).exists():
            (d / "logs").mkdir(parents=True, exist_ok=True)
            shutil.copy2(log_path, d / "logs" / "run.log")
        written.append(d)

    return written
