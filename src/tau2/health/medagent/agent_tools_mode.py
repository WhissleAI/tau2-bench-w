"""Mode B — the shipped product: Whissle's own FHIR tools against the virtual EHR.

Mode A binds the benchmark's tools to the Whissle brain and answers "how good is
our reasoning". Mode B answers the question a healthcare SI actually asks: **does
the product we ship do this?** It runs the `ehr_assistant` agent with its own
registered `fhir_*` tools, executing server-side against the benchmark's virtual
EHR, and grades by reading the resulting chart state.

The two modes grade differently, on purpose:

  mode A  grades the POST payload recovered from the transcript (upstream parity)
  mode B  grades the FHIR resources that exist afterwards (ground truth)

Mode B is therefore immune to the said-vs-wrote blind spot by construction: an
agent that narrates an order it never placed scores zero.

Preconditions (all checked by `preflight`, none of which block mode A):

1. `ehr_assistant` exists as an agent type in the backend, and an agent of that
   type exists in the org. A colleague is seeding this; until it lands
   `preflight` reports `available: false` and the CLI skips the mode cleanly.
2. The org has a `fhir` credential whose `base_url` points at the benchmark's
   virtual EHR. Because tools execute inside the backend, `localhost:8080` is
   not reachable — the EHR must be exposed at a URL the backend can resolve, and
   `validate_fhir_config` in the backend enforces https for non-loopback hosts.
   Set `MEDAGENTBENCH_FHIR_PUBLIC_BASE` to that tunnel URL.
3. A text-turn endpoint that executes the agent's real tools.
   `/api/bench/agent-turn` is deliberately NOT it: that endpoint executes
   nothing and per-request `tools` fully replace the agent's own, which is
   exactly right for mode A and exactly wrong here.

Set `WHISSLE_EHR_AGENT_ID` and `MEDAGENTBENCH_FHIR_PUBLIC_BASE` to enable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

import requests


@dataclass
class ModeBPreflight:
    """Whether mode B can run, and precisely what is missing if not."""

    available: bool
    agent_id: Optional[str] = None
    agent_type: Optional[str] = None
    public_fhir_base: Optional[str] = None
    blockers: list[str] = None  # type: ignore[assignment]

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "public_fhir_base": self.public_fhir_base,
            "blockers": self.blockers or [],
        }


def preflight(
    *,
    base: Optional[str] = None,
    api_key: Optional[str] = None,
    agent_id: Optional[str] = None,
    timeout: float = 30.0,
) -> ModeBPreflight:
    """Check mode B's preconditions without running anything.

    Never raises: an unavailable mode B must not break a mode A run.
    """
    blockers: list[str] = []
    base = (base or os.getenv("WHISSLE_BASE") or "").rstrip("/")
    api_key = api_key or os.getenv("WHISSLE_API_KEY")
    agent_id = agent_id or os.getenv("WHISSLE_EHR_AGENT_ID")
    public_base = os.getenv("MEDAGENTBENCH_FHIR_PUBLIC_BASE")

    if not public_base:
        blockers.append(
            "MEDAGENTBENCH_FHIR_PUBLIC_BASE is unset — the backend executes the "
            "FHIR tools itself and cannot reach a localhost EHR. Expose the "
            "benchmark container over https and set this."
        )
    elif public_base.startswith("http://") and "localhost" not in public_base:
        blockers.append(
            "MEDAGENTBENCH_FHIR_PUBLIC_BASE must be https — the backend's "
            "validate_fhir_config rejects plaintext for non-loopback hosts."
        )

    if not agent_id:
        blockers.append(
            "WHISSLE_EHR_AGENT_ID is unset — create an `ehr_assistant` agent "
            "once that type is seeded in the backend."
        )
    agent_type = None
    if agent_id and base and api_key:
        try:
            r = requests.get(
                f"{base}/api/agents/{agent_id}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=timeout,
            )
            if r.status_code == 404:
                blockers.append(f"agent {agent_id} not found in this org")
            elif r.status_code >= 400:
                blockers.append(f"agent lookup failed: HTTP {r.status_code}")
            else:
                agent = r.json()
                agent_type = agent.get("agent_type")
                if agent_type != "ehr_assistant":
                    blockers.append(
                        f"agent {agent_id} is type {agent_type!r}, expected "
                        "'ehr_assistant'"
                    )
                tools = {
                    t.get("name")
                    for t in (agent.get("tools") or [])
                    if t.get("enabled")
                }
                if not any(t and t.startswith("fhir_") for t in tools):
                    blockers.append(
                        "the agent has no enabled fhir_* tools — mode B measures "
                        "the agent's own registered FHIR tools"
                    )
        except requests.RequestException as e:
            blockers.append(f"agent lookup transport failure: {e}")

    return ModeBPreflight(
        available=not blockers,
        agent_id=agent_id,
        agent_type=agent_type,
        public_fhir_base=public_base,
        blockers=blockers,
    )
