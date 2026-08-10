"""The structural audit — which cascade channels reach the brain at all.

Some of what the ablation was commissioned to find out is not an experimental
question. If a channel is produced and then never read, its contribution is zero
by construction and no number of samples will show otherwise; running an
experiment to discover that would be a slow way of reading the source.

So this module records the code-truth separately from the measurement, with
file:line evidence, and the runner emits both. Each finding is checked against a
backend checkout when one is available (``--backend-root``) so the claims are
verified rather than remembered — and marked ``verified: false`` when it is not,
rather than being quietly asserted.

The audit is what makes the eventual report able to say *why* a delta is zero:
"the channel does not reach the model" and "the channel reaches the model and
does not help" are the same zero and completely different findings.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

CHANNELS = ("emotion", "intent", "entity", "age", "gender", "hesitation",
            "shadow", "speculation")


@dataclass
class Finding:
    channel: str
    produced: bool
    reaches_prompt: bool
    reaches_flow: bool
    #: Whether the producing head is actually serving on the deployed backend.
    serving_live_path: Optional[bool] = None
    evidence: list[str] = field(default_factory=list)
    verified: bool = False
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


#: (channel, produced, reaches_prompt, reaches_flow, summary, [(relpath, needle)])
_CLAIMS: list[tuple] = [
    ("emotion", True, True, False,
     "Produced by the whissle-large metadata head; injected into the prompt inside "
     "the [User speech analysis: …] developer message. Reaches the model — but only "
     "when the head is serving, which on the live voice path it is not.",
     [("pipecat-bot/bot/services_build.py", "_METADATA_FIELDS"),
      ("pipecat-bot/services/metadata_processor.py", "extract_and_store_metadata")]),
    ("intent", True, True, False,
     "Produced and injected alongside emotion. NOT read by the flow engine: "
     "transitions are decided by expression evaluation and an LLM condition judge, "
     "never by the ASR intent head.",
     [("pipecat-bot/bot/services_build.py", "_METADATA_FIELDS"),
      ("pipecat-bot/services/flow/expr.py", "def evaluate")]),
    ("entity", True, False, False,
     "Requested from the ASR as a metadata tag and then dropped: `entity` is not in "
     "_METADATA_FIELDS, so it never reaches the prompt, and nothing in services/flow/ "
     "reads it, so it never reaches a slot. Slots are filled by a separate LLM "
     "extraction pass over the transcript (flow/collector.py), which is text-only. "
     "The entity channel currently contributes nothing to anything.",
     [("pipecat-bot/services/whissle_stt.py", "entity"),
      ("pipecat-bot/services/flow/collector.py", "def extract_missing")]),
    ("age", True, False, False,
     "Produced by the same head and never injected — not in _METADATA_FIELDS.",
     [("pipecat-bot/bot/services_build.py", "_METADATA_FIELDS")]),
    ("gender", True, False, False,
     "Produced by the same head and never injected — not in _METADATA_FIELDS.",
     [("pipecat-bot/bot/services_build.py", "_METADATA_FIELDS")]),
    ("hesitation", True, False, False,
     "Computed from the metadata probability timeline and emitted to the client "
     "only. Phase 1 is produce-and-record: it is never placed in the prompt, so it "
     "cannot change a reply. It also depends on the same head, so with the head off "
     "it computes over an empty timeline.",
     [("pipecat-bot/services/hesitation.py", "def compute")]),
    ("shadow", True, False, False,
     "Shadow draft / predicted tools sit behind SHADOW_LLM and SHADOW_LLM_DRAFT, "
     "both defaulting off.",
     [("pipecat-bot/services/shadow_llm.py", "SHADOW_LLM")]),
    ("speculation", True, False, False,
     "Speculative tool pre-warm sits behind SPECULATIVE_TOOLS, defaulting off.",
     [("pipecat-bot/services/speculative_tools.py", "def speculation_enabled")]),
]

#: Why the live voice path produces no metadata at all today.
LIVE_PATH_CLAIM = {
    "claim": (
        "On the live voice path the metadata head is not running. Production STT is "
        "routed to AssemblyAI (English/Hinglish), Sarvam (Indian languages) or "
        "Deepgram; none of them emit a metadata head. The whissle-large sidecar that "
        "would supply one is gated behind WHISSLE_STT_TRANSPORT=grpc plus "
        "WHISSLE_GRPC_TARGET. Consequence: for live calls, arm B and arm A are the "
        "same prompt, and the metadata layer's contribution to production today is "
        "structurally zero — independent of any experiment."
    ),
    "evidence": [
        ("pipecat-bot/bot/services_build.py", "STT_ASSEMBLYAI"),
        ("pipecat-bot/services/whissle_metadata_sidecar.py", "WHISSLE_STT_TRANSPORT"),
    ],
    "batch_path_note": (
        "The BATCH path is different and is why this ablation can run at all: "
        "/api/models/transcribe calls whissle_batch_metadata in parallel with the "
        "external transcription, and that head IS serving in production. It is "
        "fail-open, so a timeout simply omits the `metadata` key."
    ),
}


def _locate(root: Optional[Path], rel: str, needle: str) -> Optional[str]:
    if not root:
        return None
    p = Path(root) / rel
    try:
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if needle in line:
                return f"{rel}:{i}"
    except Exception:
        return None
    return None


def run_audit(backend_root: Optional[Path] = None) -> dict[str, Any]:
    """Build the structural audit, verifying against a backend checkout if given."""
    findings: list[Finding] = []
    for channel, produced, prompt, flow, summary, ev in _CLAIMS:
        located, verified = [], True
        for rel, needle in ev:
            hit = _locate(backend_root, rel, needle)
            if hit:
                located.append(hit)
            else:
                located.append(f"{rel} (not verified: needle {needle!r} not found)")
                verified = False
        findings.append(Finding(
            channel=channel, produced=produced, reaches_prompt=prompt,
            reaches_flow=flow, evidence=located,
            verified=verified and backend_root is not None, summary=summary,
        ))

    live_ev, live_verified = [], True
    for rel, needle in LIVE_PATH_CLAIM["evidence"]:
        hit = _locate(backend_root, rel, needle)
        if hit:
            live_ev.append(hit)
        else:
            live_ev.append(f"{rel} (not verified)")
            live_verified = False

    injected = [f.channel for f in findings if f.reaches_prompt]
    return {
        "backend_root": str(backend_root) if backend_root else None,
        "channels": [f.to_dict() for f in findings],
        "channels_reaching_the_prompt": injected,
        "channels_produced_and_discarded": [f.channel for f in findings
                                            if f.produced and not f.reaches_prompt
                                            and not f.reaches_flow],
        "live_voice_path": {
            **LIVE_PATH_CLAIM,
            "evidence": live_ev,
            "verified": live_verified and backend_root is not None,
        },
        "headline": (
            f"Of {len(CHANNELS)} cascade channels, {len(injected)} "
            f"({', '.join(injected)}) reach the model at all; the rest are produced "
            "and discarded. On the live voice path none of them reach it, because "
            "the head that produces them is not running there."
        ),
    }


def find_backend_root(start: Optional[Path] = None) -> Optional[Path]:
    """Best-effort locate the gateway backend checkout.

    ``WHISSLE_BACKEND_ROOT`` wins, because this repo is usually run from a git
    worktree that is nowhere near the backend checkout — and an audit that
    silently reports ``verified: false`` because it could not find the source is
    the exact kind of quiet degradation this suite exists to refuse.
    """
    import os

    env = os.getenv("WHISSLE_BACKEND_ROOT")
    if env and (Path(env) / "pipecat-bot" / "bot" / "services_build.py").exists():
        return Path(env)
    here = Path(start or Path.cwd()).resolve()
    for base in [here, *here.parents]:
        cand = base.parent / "whissle_gateway_backend"
        if (cand / "pipecat-bot" / "bot" / "services_build.py").exists():
            return cand
        cand2 = base / "whissle_gateway_backend"
        if (cand2 / "pipecat-bot" / "bot" / "services_build.py").exists():
            return cand2
    return None
