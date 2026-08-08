# Copyright Sierra
"""One archive writer, called by every benchmark suite.

    from tau2 import archive

    ledger = archive.ServingLedger()          # 1. record what actually served
    ...                                        #    (one line at the turn-taker)
    archive.export_run(                        # 2. archive at end of run
        suite="medagentbench",
        arm="sweep25_opus5",
        modality=archive.MODALITY_TEXT,        #    required — no default
        run_dir=out_dir,
        config=cfg, summary=summary, cases=records,
        ledger=ledger,
        reproduce_command="python -m tau2.health.medagent.run --...",
    )

Lands a self-describing folder under ``$TAU2_ARCHIVE_DIR`` (default
``~/Downloads/whissle_benchmarks``) and rebuilds the cross-run index.

Design notes live in :mod:`tau2.archive.writer` (why one writer),
:mod:`tau2.archive.schema` (the four fields a run cannot omit),
:mod:`tau2.archive.env` (why the metadata head is captured, not asserted) and
:mod:`tau2.archive.cost` (why input dominates output ~75:1 here).
"""
from __future__ import annotations

from .cost import PRICES, Usage, price_for
from .env import Environment, capture, metadata_head
from .index import rebuild
from .schema import (
    MODALITIES,
    MODALITY_NOT_RECORDED,
    MODALITY_TEXT,
    MODALITY_TEXT_VISION,
    MODALITY_VOICE,
    MODALITY_VOICE_VISION,
    NOT_RECORDED,
    SCHEMA,
    ArchiveError,
    DateProvenance,
    RequestedRecord,
    ServedRecord,
    require_modality,
)
from .serving import ServingLedger, from_records
from .writer import (
    ARCHIVE_DIR_ENV,
    DEFAULT_ARCHIVE_DIR,
    ArchiveResult,
    archive_root,
    export_hook,
    export_run,
)

__all__ = [
    "ARCHIVE_DIR_ENV",
    "DEFAULT_ARCHIVE_DIR",
    "MODALITIES",
    "MODALITY_NOT_RECORDED",
    "MODALITY_TEXT",
    "MODALITY_TEXT_VISION",
    "MODALITY_VOICE",
    "MODALITY_VOICE_VISION",
    "NOT_RECORDED",
    "PRICES",
    "SCHEMA",
    "ArchiveError",
    "ArchiveResult",
    "DateProvenance",
    "Environment",
    "RequestedRecord",
    "ServedRecord",
    "ServingLedger",
    "Usage",
    "archive_root",
    "capture",
    "export_hook",
    "export_run",
    "from_records",
    "metadata_head",
    "price_for",
    "rebuild",
    "require_modality",
]
