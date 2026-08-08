# Copyright Sierra
"""AgentClinic case loading — the upstream ``.jsonl`` files, unmodified.

Upstream ships five case sets (``github.com/SamuelSchmidgall/AgentClinic``, MIT).
Verified record counts in the current upstream tree:

    MedQA       agentclinic_medqa.jsonl            107 dialogue cases (OSCE shape)
    MedQA_Ext   agentclinic_medqa_extended.jsonl   214 dialogue cases  <- the paper's
                                                                          "215 language
                                                                          agents"
    NEJM        agentclinic_nejm.jsonl              15 image+dialogue cases
    NEJM_Ext    agentclinic_nejm_extended.jsonl    120 image+dialogue cases <- the
                                                                              paper's
                                                                              "120
                                                                              multimodal"
    MIMICIV     agentclinic_mimiciv.jsonl          referenced by upstream's loader but
                                                   NOT distributed in the repo (it is
                                                   derived from credentialed MIMIC-IV);
                                                   supported here if you drop the file
                                                   in the data dir yourself.

Two record shapes, mirrored from upstream's ``Scenario*`` classes:

  OSCE (MedQA / MIMIC-IV)   {"OSCE_Examination": {Objective_for_Doctor, Patient_Actor,
                             Physical_Examination_Findings, Test_Results,
                             Correct_Diagnosis}}
  NEJM                      {question, image_url, patient_info, physical_exams,
                             answers:[{text, correct}]}

The cases are *not* vendored into this repo: :func:`ensure_dataset` fetches them on
first use into ``data/agentclinic/`` (override with ``AGENTCLINIC_DATA_DIR``), so we
redistribute nothing and always run the upstream file.
"""
from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

RAW_BASE = "https://raw.githubusercontent.com/SamuelSchmidgall/AgentClinic/main"

DATASETS: dict[str, str] = {
    "MedQA": "agentclinic_medqa.jsonl",
    "MedQA_Ext": "agentclinic_medqa_extended.jsonl",
    "NEJM": "agentclinic_nejm.jsonl",
    "NEJM_Ext": "agentclinic_nejm_extended.jsonl",
    "MIMICIV": "agentclinic_mimiciv.jsonl",
}

# The image datasets — a case here carries an ``image_url`` the doctor may need.
IMAGE_DATASETS = {"NEJM", "NEJM_Ext"}

# Not distributed upstream (credentialed source); never attempt to fetch it.
NO_FETCH = {"MIMICIV"}


def data_dir() -> Path:
    return Path(os.getenv("AGENTCLINIC_DATA_DIR") or "data/agentclinic").resolve()


class DatasetError(RuntimeError):
    pass


@dataclass
class Scenario:
    """One case, normalized across the OSCE and NEJM record shapes.

    The four accessors match upstream's ``Scenario*`` API exactly, because the
    patient / measurement / moderator prompts interpolate their raw values (a dict
    for OSCE cases, a string for NEJM ones) and changing the stringification would
    change the prompts the published baselines saw.
    """

    index: int
    dataset: str
    raw: dict[str, Any]

    # -- upstream API ------------------------------------------------------------

    def patient_information(self) -> Any:
        if self.dataset in IMAGE_DATASETS:
            return self.raw["patient_info"]
        return self.raw["OSCE_Examination"]["Patient_Actor"]

    def examiner_information(self) -> Any:
        if self.dataset in IMAGE_DATASETS:
            return "What is the most likely diagnosis?"
        return self.raw["OSCE_Examination"]["Objective_for_Doctor"]

    def exam_information(self) -> Any:
        if self.dataset in IMAGE_DATASETS:
            return self.raw["physical_exams"]
        osce = self.raw["OSCE_Examination"]
        # Upstream mutates the physical-exam dict to carry the tests under a "tests"
        # key and hands the merged dict to the measurement agent. Copy instead of
        # mutating the loaded record (upstream's in-place edit is a latent bug when a
        # scenario is reused across runs).
        exams = dict(osce["Physical_Examination_Findings"])
        exams["tests"] = osce["Test_Results"]
        return exams

    def diagnosis_information(self) -> str:
        if self.dataset in IMAGE_DATASETS:
            return [a["text"] for a in self.raw["answers"] if a.get("correct")][0]
        return self.raw["OSCE_Examination"]["Correct_Diagnosis"]

    # -- extras ------------------------------------------------------------------

    @property
    def id(self) -> str:
        return f"{self.dataset}-{self.index}"

    @property
    def image_url(self) -> Optional[str]:
        return self.raw.get("image_url") if self.dataset in IMAGE_DATASETS else None

    @property
    def question(self) -> Optional[str]:
        return self.raw.get("question") if self.dataset in IMAGE_DATASETS else None

    @property
    def answer_options(self) -> list[str]:
        """NEJM cases are multiple-choice upstream; the doctor is NEVER shown the
        options (it must produce a free-text diagnosis) — they are recorded only as
        artifact context for a human reading the case afterwards."""
        return [a["text"] for a in self.raw.get("answers") or []]


def ensure_dataset(name: str, *, allow_download: bool = True) -> Path:
    """Path to the case file, downloading it from upstream on first use."""
    if name not in DATASETS:
        raise DatasetError(
            f"unknown dataset {name!r} — one of {sorted(DATASETS)}")
    path = data_dir() / DATASETS[name]
    if path.exists() and path.stat().st_size > 0:
        return path
    if name in NO_FETCH:
        raise DatasetError(
            f"{name} is not distributed by upstream (derived from credentialed "
            f"MIMIC-IV). Place your own {DATASETS[name]} at {path} to run it.")
    if not allow_download:
        raise DatasetError(f"{path} missing and downloads disabled")
    import requests  # local import: keeps the module importable offline

    url = f"{RAW_BASE}/{DATASETS[name]}"
    path.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(url, timeout=120)
    if r.status_code >= 300:
        raise DatasetError(f"fetch {url} -> HTTP {r.status_code}")
    path.write_bytes(r.content)
    return path


def load_scenarios(name: str, *, path: Optional[Path] = None,
                   allow_download: bool = True) -> list[Scenario]:
    """All cases of ``name``, in file order (upstream's scenario ids are indices
    into this order, so the order is part of the contract)."""
    p = Path(path) if path else ensure_dataset(name, allow_download=allow_download)
    out: list[Scenario] = []
    with open(p, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            line = line.strip()
            if not line:
                continue
            out.append(Scenario(index=len(out), dataset=name, raw=json.loads(line)))
    if not out:
        raise DatasetError(f"{p} contained no cases")
    return out


def select(scenarios: list[Scenario], *, limit: Optional[int] = None,
           sample: str = "head", seed: int = 0) -> list[Scenario]:
    """Choose the cases to run.

    ``head`` (default) takes the first N — exactly what upstream's
    ``for _scenario_id in range(0, min(num_scenarios, ...))`` does, so a limited run
    here is the same subset a limited run upstream would grade. ``random`` draws a
    seeded sample when you want an unbiased slice; the seed and the selected ids are
    written into every artifact so N is always reported with its provenance.
    """
    if limit is None or limit >= len(scenarios):
        return list(scenarios)
    if sample == "random":
        rng = random.Random(seed)
        return sorted(rng.sample(scenarios, limit), key=lambda s: s.index)
    if sample != "head":
        raise DatasetError(f"unknown sample mode {sample!r} (head|random)")
    return list(scenarios[:limit])
