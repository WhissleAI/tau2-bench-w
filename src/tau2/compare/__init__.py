# Copyright Sierra
"""Scenario comparison between Whissle and external voice-agent platforms.

Six scenarios chosen where a cascade (separable ASR → LLM → flow engine → TTS)
should structurally win or lose against an opaque speech-to-speech system, each
carrying a hypothesis, deterministic pass criteria, and the flow-trace evidence
that would prove the mechanism actually fired.

The package's opinions, in one place:

* **A pass/fail table is not the deliverable.** The trace explaining WHY is. See
  :mod:`tau2.compare.evidence`.
* **Two kinds of number, never mixed quietly.** ``setup_matched`` (we ran both)
  vs ``published_external`` (quoted, with a mandatory citation). See
  :mod:`tau2.compare.baselines`.
* **No setup-matched pair, no verdict.** See :mod:`tau2.compare.compare`.
* **No fabricated competitor numbers, ever.** A vendor without credentials is
  reported as not-runnable and has no score. See
  :mod:`tau2.compare.vendors.elevenlabs_convai`.
* **One honesty banner, one constant.** See :mod:`tau2.compare.honesty`.

Read ``COMPARE.md`` at the repo root for the method.
"""
from __future__ import annotations

from tau2.compare.honesty import differentiator_status

__all__ = ["differentiator_status"]
