# Copyright Sierra
"""The one place a comparison report's honesty banner is defined.

WHY THIS MODULE IS A SINGLE CONSTANT
------------------------------------
Whissle's pitch against an opaque speech-to-speech vendor rests on the cascade
being *inspectable*: an ASR that emits acoustic metadata (emotion / intent /
hesitation), a flow engine whose transitions carry a stated reason, and a trace
you can read afterwards. As of the verification date below, one of those three
is not live in production: the whissle-large metadata head is unreachable from
the AWS prod hosts, so no emotion/intent/hesitation frame reaches the agent.

A comparison run today therefore measures **Whissle's LLM + flow engine against
the vendor's full stack, with Whissle's stated differentiator disabled**. That
sentence has to be at the top of every artifact this package emits, or the
artifact is a misleading document — most dangerously when Whissle *wins*, since
a reader will attribute the win to a component that was switched off.

Removing it must be ONE edit. When the metadata head is restored, set
:data:`DIFFERENTIATOR_OUTAGE` to ``None``. Everything downstream — the Markdown
banner, the machine-readable ``differentiator_status`` field, the per-case
metadata-section unavailability reason — derives from that single binding, so no
report can keep claiming an outage that has ended, and none can quietly drop the
claim while the outage continues.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

STATUS_DISABLED = "disabled"
STATUS_OPERATIONAL = "operational"


@dataclass(frozen=True)
class Outage:
    """A known-disabled Whissle differentiator, stated with its evidence.

    Every field is here so the banner is *checkable* rather than a vibe: a reader
    can re-run the probe against ``target`` and either confirm or retire it."""

    component: str
    target: str
    symptom: str
    verified_on: str
    consequence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "target": self.target,
            "symptom": self.symptom,
            "verified_on": self.verified_on,
            "consequence": self.consequence,
        }


# ── THE SINGLE BINDING ──────────────────────────────────────────────────────────
# Set to None when the metadata head is producing in production again. That one
# edit removes the banner from every report and flips `differentiator_status`.
DIFFERENTIATOR_OUTAGE: Optional[Outage] = Outage(
    component=(
        "Whissle acoustic metadata head (emotion / intent / hesitation off the "
        "Whissle ASR)"
    ),
    target="136.115.121.123:50051",
    symptom=(
        'gRPC metadata target unreachable from the AWS prod hosts — '
        'StatusCode.UNAVAILABLE, "tcp handshaker shutdown"'
    ),
    verified_on="2026-08-08",
    consequence=(
        "any Whissle-vs-vendor comparison run today measures Whissle's LLM + flow "
        "engine against the vendor's full stack, with Whissle's stated "
        "differentiator disabled"
    ),
)


def differentiator_status() -> str:
    """``"disabled"`` while :data:`DIFFERENTIATOR_OUTAGE` is set, else
    ``"operational"``. This is the value reports carry as the machine-readable
    ``differentiator_status`` field."""
    return STATUS_DISABLED if DIFFERENTIATOR_OUTAGE else STATUS_OPERATIONAL


def metadata_unavailable_reason() -> Optional[str]:
    """The canonical reason to stamp on a diagnostics ``metadata_sidecar`` section
    when the head is down, so a per-case artifact carries the same explanation the
    banner gives. ``None`` when there is no outage — in which case the caller falls
    back to the transport-derived reason from ``tau2.health.diagnostics``."""
    o = DIFFERENTIATOR_OUTAGE
    if o is None:
        return None
    return (
        f"{o.component} is NOT producing in production: {o.symptom} "
        f"(verified {o.verified_on}). Absence here is an infrastructure outage, "
        f"not a measurement that the head found nothing."
    )


def banner_markdown() -> str:
    """The banner, as a Markdown blockquote for the top of a report.

    Empty string when there is no outage — a report with nothing to disclose must
    not print an empty scare-box."""
    o = DIFFERENTIATOR_OUTAGE
    if o is None:
        return ""
    return "\n".join([
        "> ## ⚠ READ THIS FIRST — Whissle's differentiator was DISABLED for this run",
        ">",
        f"> **{o.component} is NOT producing in production.**",
        f"> {o.symptom}. Verified {o.verified_on} against `{o.target}`.",
        ">",
        f"> Therefore {o.consequence}.",
        ">",
        "> Read every result below with that in mind. A Whissle **win** here was not "
        "won by the acoustic metadata head — it was switched off. A Whissle **loss** "
        "here is a loss taken with one hand tied behind its back; it is not evidence "
        "that the metadata head would not have changed the outcome, and it must not "
        "be quoted as one.",
        "",
    ])


def banner_block() -> dict[str, Any]:
    """The machine-readable twin of :func:`banner_markdown`, for report JSON."""
    o = DIFFERENTIATOR_OUTAGE
    return {
        "differentiator_status": differentiator_status(),
        "outage": o.to_dict() if o else None,
        "banner_text": banner_markdown() or None,
        "metadata_unavailable_reason": metadata_unavailable_reason(),
    }
