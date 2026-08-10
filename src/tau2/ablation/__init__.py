"""The metadata ablation — what Whissle's cascade layer actually contributes.

The product claim is a cascaded architecture: our own ASR emits per-utterance
emotion / intent / entity metadata, that metadata reaches the brain and the flow
engine, and the result is better than text alone. Every benchmark number we have
conflates that layer with the LLM. This package separates them.

Design
------
The ablation is **single-variable and paired**. One perception pass per case
(``perception.py``: TTS → ``/api/models/transcribe``) produces one ASR transcript
and one real metadata sidecar, and *both* arms consume that same transcript. The
only thing that differs between arms is whether the metadata block is present in
the prompt, so ASR quality, task, model, system prompt, tools and decoding
settings are held constant by construction rather than by hope.

    arm A   user turn = ASR text
    arm B   user turn = "[User speech analysis: …]\\n" + ASR text

Arm B's block is built by :func:`arms.speech_analysis_block`, which reimplements
``bot/services_build.py::_MetadataContextMixin._format_field`` character for
character, from the *real* sidecar this run measured. It is not a stand-in for
the metadata layer; it is the metadata layer's own output.

Why the prompt and not a backend switch
---------------------------------------
There is no switch. ``/api/bench/agent-turn`` never injects cascade metadata at
all — the text path has no metadata seam — and no env var or request field
suppresses the block on the voice path either. So the ablation is by
*construction* (add the real block) rather than by *suppression* (remove it).
:mod:`tau2.ablation.audit` records that, and the gaps it implies, as findings.

Modules
-------
``corpus``      the frozen, pre-declared case list and its schema
``perception``  TTS → ASR → metadata sidecar, cached on disk, shared by all arms
``arms``        arm definitions, prompt construction, the production block format
``grade``       deterministic graders — slots, routing, write integrity, ASR errors
``stats``       paired tests: exact McNemar, Wilcoxon, bootstrap CI, MDE
``audit``       code-truth audit of which channels reach the brain at all
``run``         the CLI: run, verify arm matching, report
"""

from __future__ import annotations

SCHEMA = "tau2.ablation.metadata/v1"

__all__ = ["SCHEMA"]
