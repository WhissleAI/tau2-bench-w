"""Register the Whissle agents into PatientAgentBench's assistant registry.

PatientAgentBench is CC-BY-NC-4.0 and explicitly does not accept pull requests
("to maintain the code exactly as it was used in the paper"), so this adapter must
not fork or patch it. It does not need to: their registry exposes a public
``register_assistant_agent(name, cls)``, and their config selects an agent by
``agent_class``. Importing this module before their runner resolves the config is
the entire integration.

Registered names:
  ``whissle``         harness-tools mode  (default; comparable to their baselines)
  ``whissle-native``  agent-tools mode    (product measurement; NOT comparable)
  ``whissle-voice``   agent-tools over the real speech pipeline
"""

from __future__ import annotations

from typing import Optional

_REGISTERED: Optional[dict[str, type]] = None


def register(include_voice: bool = True) -> dict[str, type]:
    """Idempotently register the Whissle agent classes. Returns name -> class.

    ``include_voice`` is skippable so a text-only run never imports the voice
    transport (and therefore never needs LiveKit installed).
    """
    global _REGISTERED
    if _REGISTERED is not None:
        return _REGISTERED

    from patient_agent_bench.assistant_agent.registry import register_assistant_agent

    from tau2.health.patientagent.agents import build_agent_classes

    harness_cls, native_cls = build_agent_classes()
    registered: dict[str, type] = {
        harness_cls.NAME: harness_cls,
        native_cls.NAME: native_cls,
    }

    if include_voice:
        try:
            from tau2.health.patientagent.voice_agent import build_voice_agent_class

            voice_cls = build_voice_agent_class()
            registered[voice_cls.NAME] = voice_cls
        except ImportError:
            # Voice extras absent — text modes still work. Fail only if a run
            # actually asks for agent_class "whissle-voice".
            pass

    for name, cls in registered.items():
        register_assistant_agent(name, cls)

    _REGISTERED = registered
    return registered


def mode_of(agent_class: str) -> str:
    """The mode label for an ``agent_class``, for report provenance."""
    from tau2.health.patientagent.agents import AGENT_TOOLS_MODE, HARNESS_TOOLS_MODE

    return {
        "whissle": HARNESS_TOOLS_MODE,
        "whissle-native": AGENT_TOOLS_MODE,
        "whissle-voice": AGENT_TOOLS_MODE,
    }.get(agent_class, "unknown")
