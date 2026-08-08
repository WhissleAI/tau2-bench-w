# Copyright Sierra
"""The multimodal (NEJM) variant — getting the case image to the agent.

AgentClinic-NEJM cases carry an ``image_url`` (an NEJM content-server URL). Upstream
hands it to a vision model as an OpenAI ``image_url`` content block. Whissle's
agent-turn path is Anthropic-shaped, and the image-input work exposes two surfaces:

  * base64 image content BLOCKS on ``/api/bench/agent-turn`` — an Anthropic
    ``{"type":"image","source":{"type":"base64","media_type":…,"data":…}}`` block
    inside a user message. **Verified live against prod on 2026-08-07**: the endpoint
    accepts the block and the agent answers about the image's content, so the vision
    variant runs today rather than waiting on a merge.
  * an ``analyze_image`` TOOL the agent can call for a described reading. Declaring
    it is accepted today; in bench mode the call is delegated back to the harness
    (like every other tool), and the runner answers it by re-attaching the case image
    — see ``runner.run_case``'s ``look`` branch.

This module implements both against that contract and nothing else — no fallback
that fakes vision by pasting an image caption into the prompt, which would produce a
number that looks like multimodal performance without being one.

Everything here is behind ``--vision`` (default ``off``): the text variants
(MedQA / MedQA_Ext) run today regardless of whether the image PR has landed. When
vision is off on an image dataset, the run is still valid — it is exactly upstream's
``--doctor_image_request`` **without** the image, i.e. the dialogue-only lower bound
— and every artifact records ``vision: "off"`` so that lower bound is never mistaken
for a multimodal result.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any, Optional

# Vision modes.
OFF = "off"          # no image reaches the agent (text lower bound)
BLOCK = "block"      # base64 image content block on the agent-turn message
TOOL = "tool"        # advertise analyze_image and let the agent call it
BOTH = "both"        # block + tool
VISION_MODES = (OFF, BLOCK, TOOL, BOTH)

# The backend decides the format from MAGIC BYTES, not the declared type, and accepts
# jpeg/png/webp only (backend `docs/image-input.md`, PR #651). We mirror both rules
# here so a case fails locally with a clear reason instead of as a 415 mid-dialogue.
_MEDIA_BY_MAGIC = [
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"RIFF", "image/webp"),
]

# Per-image ceiling is 5 MB on DECODED bytes (413 above that). We cap slightly under.
MAX_IMAGE_BYTES = int(os.getenv("AGENTCLINIC_MAX_IMAGE_BYTES", str(5 * 1024 * 1024)))
# At most 4 images per request across the whole message list (400 above that). With a
# native multi-turn history the case image can accumulate, so the adapter prunes to
# this cap before sending — see ``doctor.prune_images``.
MAX_IMAGES_PER_REQUEST = 4


class VisionError(RuntimeError):
    pass


@dataclass
class CaseImage:
    url: str
    media_type: str
    data_b64: str
    n_bytes: int

    def content_block(self) -> dict[str, Any]:
        """The Anthropic-shaped base64 image block the agent-turn path accepts."""
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": self.media_type,
                "data": self.data_b64,
            },
        }


def _media_type(blob: bytes, header: Optional[str]) -> str:
    """Media type from magic bytes only — the declared ``Content-Type`` is advisory
    and a mismatch is a 415 server-side, so we never trust it over the bytes."""
    for magic, mt in _MEDIA_BY_MAGIC:
        if blob.startswith(magic):
            if mt == "image/webp" and blob[8:12] != b"WEBP":
                break
            return mt
    raise VisionError(
        "not a supported image (jpeg/png/webp decided by magic bytes; declared "
        f"{(header or 'none').split(';')[0]})")


def fetch_image(url: str, *, timeout: float = 60.0) -> CaseImage:
    """Download a case image and base64 it. Raises :class:`VisionError` on anything
    that is not a usable image — the caller turns that into a per-case finding, never
    a silent text-only run relabeled as multimodal."""
    import requests

    if not url:
        raise VisionError("case has no image_url")
    r = requests.get(url, timeout=timeout)
    if r.status_code >= 300:
        raise VisionError(f"GET image -> HTTP {r.status_code}")
    blob = r.content
    if not blob:
        raise VisionError("empty image body")
    if len(blob) > MAX_IMAGE_BYTES:
        raise VisionError(
            f"image is {len(blob)} bytes, over the {MAX_IMAGE_BYTES}-byte cap")
    mt = _media_type(blob, r.headers.get("Content-Type"))
    return CaseImage(url=url, media_type=mt,
                     data_b64=base64.b64encode(blob).decode("ascii"),
                     n_bytes=len(blob))


ANALYZE_IMAGE_TOOL: dict[str, Any] = {
    "name": "analyze_image",
    "description": (
        "Look at the medical image attached to this case and describe what it shows. "
        "Ask a specific question about the image to get a focused reading."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "What you want to know about the image, e.g. "
                               "'describe the lesion morphology and distribution'.",
            }
        },
        "required": ["question"],
    },
}


def image_user_content(text: str, image: Optional[CaseImage],
                       mode: str) -> Any:
    """Build the user message content for one doctor turn.

    Returns a plain string when no image is attached (identical to the text path, so
    the text variants are byte-identical whether or not this module is involved), and
    an Anthropic content-block list when it is."""
    if image is None or mode in (OFF, TOOL):
        return text
    return [image.content_block(), {"type": "text", "text": text}]


def vision_tools(mode: str) -> list[dict[str, Any]]:
    return [ANALYZE_IMAGE_TOOL] if mode in (TOOL, BOTH) else []
