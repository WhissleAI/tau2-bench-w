# Copyright Sierra
"""AgentClinic adapter — Whissle plugs in as the DOCTOR agent.

AgentClinic (Schmidgall et al., arXiv:2405.07960; npj Digital Medicine 2026;
github.com/SamuelSchmidgall/AgentClinic) is a *dialogue* benchmark: a doctor agent
interviews a patient agent under incomplete information, may order tests from a
measurement agent, and must commit to a diagnosis, which a moderator agent grades
against the case's ground truth.

This package keeps the benchmark's patient / measurement / moderator agents and
their prompts VERBATIM (so numbers stay comparable to published baselines) and
swaps only the doctor for the real Whissle platform agent, over either

  * TEXT  — ``POST {WHISSLE_BASE}/api/bench/agent-turn`` (same contract, auth and
    retry policy as ``tau2.agent.whissle_agent``), or
  * VOICE — the real spoken pipeline (STT → agent → TTS over LiveKit) via
    ``tau2.flow.voice_transport``: the patient literally speaks and the doctor
    listens, and scoring runs over the resulting transcript unchanged.

See ``AGENTCLINIC.md`` at the repo root for the runbook.
"""

from tau2.health.agentclinic.protocol import (  # noqa: F401
    DoctorAction,
    doctor_system_prompt,
    parse_doctor_output,
)

__all__ = ["DoctorAction", "doctor_system_prompt", "parse_doctor_output"]
