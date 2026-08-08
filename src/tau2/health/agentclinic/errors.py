# Copyright Sierra
"""Infra-vs-measurement classification, reusing the flow suite's taxonomy.

``tau2.flow.analyze`` defines the finding vocabulary this repo already reports in,
including ``infra_fail``: "the session could not be measured" — a transport, provider
or credit failure, as opposed to anything the agent under test did. The flow runner
buckets those sessions OUT of its metrics (``simulate.aggregate_agent_type``), and
this adapter does the same: an AgentClinic case whose dialogue died on a 502 is not a
diagnostic miss, and letting it count as one would understate the score in exactly the
direction that flatters nobody and informs no one.

We deliberately import ``Finding`` / ``DEFAULT_SEVERITY`` from the flow analyzer
rather than defining a parallel vocabulary, so there is one taxonomy in the repo.
"""
from __future__ import annotations

from tau2.flow.analyze import DEFAULT_SEVERITY, Finding  # noqa: F401  (re-export)

assert "infra_fail" in DEFAULT_SEVERITY, "flow taxonomy lost infra_fail"


class AgentClinicError(RuntimeError):
    """Any failure inside the adapter."""


class DoctorInfraError(AgentClinicError):
    """The doctor could not be reached / driven — transport, provider, credit or
    timeout. Classified ``infra_fail`` and EXCLUDED from the scored denominator."""


def is_infra_error(exc: BaseException) -> bool:
    """Mirror of ``tau2.flow.simulate._is_infra_error``, without importing that
    module (it pulls in LiveKit for the voice transport, which must not be a
    requirement of a text run). Kept in lockstep with it: request/transport
    exceptions, model-provider outages, timeouts, and the voice transport's own
    typed infra error."""
    import requests as _requests

    from tau2.flow.usersim import ModelError

    if isinstance(exc, (DoctorInfraError, ModelError, _requests.RequestException,
                        TimeoutError)):
        return True
    # VoiceInfraError only exists once the voice transport imports cleanly (LiveKit
    # present); look it up lazily so text runs never pay for that import.
    try:
        from tau2.flow.voice_transport import VoiceInfraError
    except Exception:  # noqa: BLE001 — optional dependency
        return False
    return isinstance(exc, VoiceInfraError)


def infra_finding(detail: str, **evidence) -> Finding:
    return Finding("infra_fail", "high",
                   f"infrastructure failure — the case could not be measured: {detail}",
                   evidence=evidence)
