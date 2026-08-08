# Copyright Sierra
"""Vendor adapter registry.

One place that knows every vendor name, so the CLI's ``--vendor`` list, the
preflight table and the report legend cannot drift apart."""
from __future__ import annotations

from typing import Any, Callable

from tau2.compare.vendors.base import (
    Preflight,
    ScenarioRun,
    TurnRecord,
    VendorAdapter,
    not_runnable,
)

WHISSLE = "whissle"
ELEVENLABS = "elevenlabs"

#: The vendor whose mechanism evidence this package is built to surface. Named
#: rather than assumed so the comparison layer can say "no home vendor in this
#: run" instead of silently picking the first name it sees.
HOME_VENDOR = WHISSLE


def _whissle(**kwargs: Any) -> VendorAdapter:
    from tau2.compare.vendors.whissle import WhissleAdapter

    return WhissleAdapter(**kwargs)


def _elevenlabs(**kwargs: Any) -> VendorAdapter:
    from tau2.compare.vendors.elevenlabs_convai import ElevenLabsConvAIAdapter

    return ElevenLabsConvAIAdapter(**kwargs)


BUILDERS: dict[str, Callable[..., VendorAdapter]] = {
    WHISSLE: _whissle,
    ELEVENLABS: _elevenlabs,
}

KNOWN = tuple(BUILDERS)


def build(vendor: str, **kwargs: Any) -> VendorAdapter:
    """Instantiate an adapter by name. Unknown names raise — a typo'd vendor must
    not become a silently absent one."""
    try:
        builder = BUILDERS[vendor]
    except KeyError:
        raise ValueError(
            f"unknown vendor {vendor!r}; known vendors: {', '.join(KNOWN)}"
        ) from None
    return builder(**kwargs)


__all__ = [
    "BUILDERS",
    "ELEVENLABS",
    "HOME_VENDOR",
    "KNOWN",
    "Preflight",
    "ScenarioRun",
    "TurnRecord",
    "VendorAdapter",
    "WHISSLE",
    "build",
    "not_runnable",
]
