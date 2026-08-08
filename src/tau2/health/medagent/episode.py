"""One MedAgentBench episode: drive the brain through the benchmark loop.

Mirrors upstream's `MedAgentBench.start_sample` turn for turn, including the
terminal statuses, so a trajectory produced here is gradeable by upstream's own
`refsol.py` without translation.

Statuses (upstream `SampleStatus`):
  completed          — the agent called FINISH
  agent_invalid_action — the reply matched none of GET / POST / FINISH
  task_limit_reached — ran out of rounds
  infra_fail         — the brain or the EHR was unreachable; NOT scored
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from tau2.health.medagent.brain import BrainInfraError, WhissleBrain
from tau2.health.medagent.data import Case
from tau2.health.medagent.fhir import (
    FhirInfraError,
    FhirWriter,
    WriteAttempt,
    send_get_request,
)
from tau2.health.medagent.protocol import (
    DEFAULT_MAX_ROUND,
    GET_ERROR,
    GET_OBSERVATION,
    POST_ACCEPTED,
    POST_INVALID,
    Trajectory,
    Turn,
    build_prompt,
    parse_action,
)

STATUS_COMPLETED = "completed"
STATUS_INVALID = "agent_invalid_action"
STATUS_LIMIT = "task_limit_reached"
STATUS_INFRA = "infra_fail"


@dataclass
class Episode:
    """Everything one task produced, ready to be graded and archived."""

    case: Case
    trajectory: Trajectory
    turns: list[Turn] = field(default_factory=list)
    write_attempts: list[WriteAttempt] = field(default_factory=list)
    infra_fail: bool = False
    infra_reason: Optional[str] = None
    attempt: int = 1
    duration_ms: int = 0
    prompt: str = ""

    @property
    def status(self) -> str:
        return self.trajectory.status

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.case.id,
            "category": self.case.category,
            "eval_mrn": self.case.eval_mrn,
            "instruction": self.case.instruction,
            "context": self.case.context,
            "status": self.status,
            "result": self.trajectory.result,
            "infra_fail": self.infra_fail,
            "infra_reason": self.infra_reason,
            "attempt": self.attempt,
            "duration_ms": self.duration_ms,
            "rounds": len(self.turns),
            "prompt": self.prompt,
            "turns": [t.as_dict() for t in self.turns],
            "history": [
                {"role": i.role, "content": i.content} for i in self.trajectory.history
            ],
            "write_attempts": [a.as_dict() for a in self.write_attempts],
        }


def run_episode(
    case: Case,
    *,
    brain: WhissleBrain,
    funcs: list[dict[str, Any]],
    api_base: str,
    writer: Optional[FhirWriter] = None,
    max_round: int = DEFAULT_MAX_ROUND,
    attempt: int = 1,
) -> Episode:
    """Drive one task to a terminal state.

    Infra failures (brain unreachable, EHR unreachable) are captured on the
    episode rather than raised, so a single flaky task cannot abort a run — the
    caller excludes them from scoring.
    """
    prompt = build_prompt(api_base, funcs, case.context, case.instruction)
    traj = Trajectory()
    traj.add("user", prompt)
    ep = Episode(case=case, trajectory=traj, attempt=attempt, prompt=prompt)

    # Anthropic-shaped history for the bench endpoint. Upstream injects every
    # observation as a `user` message; we keep that mapping so the model sees
    # the same conversation.
    messages: list[dict] = [{"role": "user", "content": prompt}]
    system = brain.system_for(prompt)

    started = time.time()
    try:
        for rnd in range(max_round):
            t0 = time.time()
            reply = brain.turn(messages, system)
            latency_ms = int((time.time() - t0) * 1000)

            traj.add("agent", reply)
            messages.append({"role": "assistant", "content": reply})
            action = parse_action(reply)

            turn = Turn(
                round=rnd,
                agent_reply=reply,
                action_kind=action.kind,
                url=action.url,
                payload=action.payload,
                latency_ms=latency_ms,
            )

            if action.kind == "get":
                res = send_get_request(action.url or "")
                if "data" in res:
                    obs = GET_OBSERVATION.format(data=res["data"])
                else:
                    obs = GET_ERROR.format(error=res["error"])

            elif action.kind == "post":
                if action.payload is None:
                    obs = POST_INVALID
                else:
                    # Upstream stops here and lies. We optionally ask the EHR
                    # what it really thinks — but the observation handed back to
                    # the agent stays byte-identical to upstream, so the agent's
                    # behaviour (and therefore the score) is unaffected.
                    if writer is not None and writer.mode != "none":
                        ep.write_attempts.append(
                            writer.check(action.url or "", action.payload)
                        )
                    obs = POST_ACCEPTED

            elif action.kind == "finish":
                traj.result = action.result
                traj.status = STATUS_COMPLETED
                ep.turns.append(turn)
                break

            else:
                traj.status = STATUS_INVALID
                ep.turns.append(turn)
                break

            turn.observation = obs
            ep.turns.append(turn)
            traj.add("user", obs)
            messages.append({"role": "user", "content": obs})
        else:
            traj.status = STATUS_LIMIT

    except (BrainInfraError, FhirInfraError) as e:
        ep.infra_fail = True
        ep.infra_reason = str(e)
        traj.status = STATUS_INFRA
    except Exception as e:  # noqa: BLE001 — never let one task kill the run
        ep.infra_fail = True
        ep.infra_reason = f"unexpected harness error: {type(e).__name__}: {e}"
        traj.status = STATUS_INFRA

    ep.duration_ms = int((time.time() - started) * 1000)
    return ep
