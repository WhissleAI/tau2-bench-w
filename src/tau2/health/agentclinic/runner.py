# Copyright Sierra
"""The episode loop — upstream's ``main`` loop, with the doctor swapped for Whissle.

The control flow below is a faithful port of ``agentclinic.py``'s per-scenario loop
(order of turns, who hears what, when the image is attached, the final-question nudge,
and the break-on-diagnosis), because those details are the benchmark. What changed:

  * the doctor is a Whissle agent reached over a transport (text ``agent-turn`` or the
    real voice pipeline) instead of a raw provider call, and
  * every turn is RECORDED — dialogue, tests ordered, refusals, per-turn latency — so
    a case can be read afterwards instead of only counted.

The loop is transport-agnostic: anything implementing :class:`DoctorTransport`
(``act`` / ``deliver_tool_result``) can play the doctor, which is exactly how the
voice driver plugs in without a second copy of the loop.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol

from tau2.health.agentclinic.agents import (
    DOCTOR_BIASES,
    MeasurementAgent,
    PatientAgent,
    SupportLLM,
    bias_text,
    compare_results,  # noqa: F401 — kept exported for callers pinning upstream's raw call
    judge_declination,
    moderate,
    moderator_says_yes,
    moderator_says_yes_lenient,
)
from tau2.health.agentclinic.dataset import IMAGE_DATASETS, Scenario
from tau2.health.agentclinic.doctor import DoctorConfig, WhissleDoctor
from tau2.health.agentclinic.errors import infra_finding, is_infra_error
from tau2.health.agentclinic.protocol import DoctorAction, looks_like_refusal
from tau2.health.agentclinic.scoring import CaseScore
from tau2.health.agentclinic.vision import OFF, CaseImage, VisionError, fetch_image
from tau2.health.model_router import ConstrainedChoice

RESULTS_ROOT = Path("results/whissle/agentclinic")

FINAL_QUESTION_NUDGE = "This is the final question. Please provide a diagnosis.\n"


class DoctorTransport(Protocol):
    infs: int

    def act(self, incoming: Optional[str], *,
            attach_image: bool = False) -> DoctorAction: ...

    def deliver_tool_result(self, action: DoctorAction, result: str) -> None: ...


# ── one case ────────────────────────────────────────────────────────────────────

def run_case(
    scenario: Scenario,
    doctor: DoctorTransport,
    support: SupportLLM,
    *,
    total_inferences: int = 20,
    patient_bias: Optional[str] = None,
    vision: str = OFF,
    img_request: bool = False,
    mode: str = "text",
    decline_judge: bool = True,
    image: Optional[CaseImage] = None,
    image_error: Optional[str] = None,
) -> dict[str, Any]:
    """Drive one scenario to a diagnosis (or to the inference cap) and score it."""
    t0 = time.monotonic()
    patient = PatientAgent(scenario, support, bias_present=patient_bias)
    measurement = MeasurementAgent(scenario, support)

    dialogue: list[dict[str, Any]] = []
    tests_ordered: list[str] = []
    images_requested = 0
    refusals: list[dict[str, Any]] = []
    latencies_ms: list[int] = []
    infra_fail = False
    error: Optional[str] = None
    findings: list[dict[str, Any]] = []
    format_deviation = False
    action: Optional[DoctorAction] = None

    # Upstream attaches the image on EVERY doctor inference for image datasets unless
    # --doctor_image_request is set, in which case only after the doctor asks. With a
    # native multi-turn history the image stays visible once sent, so we send it once
    # and record when; the stateless (agentclinic) history re-sends each turn.
    vision_on = vision != OFF and scenario.dataset in IMAGE_DATASETS and image is not None
    image_pending = vision_on and not img_request

    # incoming is what the doctor hears next: "" on the first turn (upstream's empty
    # pi_dialogue — the doctor opens), then the patient's or the reader's line, or
    # None in tools mode where the reader's answer went back as a tool_result.
    incoming: Optional[str] = ""

    try:
        for inf_id in range(total_inferences):
            if inf_id == total_inferences - 1:
                incoming = (incoming or "") + FINAL_QUESTION_NUDGE

            t_turn = time.monotonic()
            attach = image_pending
            action = doctor.act(incoming, attach_image=attach)
            latency_ms = round((time.monotonic() - t_turn) * 1000)
            latencies_ms.append(latency_ms)
            if attach:
                image_pending = False
            format_deviation = format_deviation or action.format_deviation

            refusal = looks_like_refusal(action.text)
            if refusal:
                refusals.append({"inference": inf_id + 1, "evidence": refusal,
                                 "text": action.text[:400]})
            dialogue.append({
                "role": "doctor", "inference": inf_id + 1, "kind": action.kind,
                "text": action.text, "payload": action.payload,
                "latency_ms": latency_ms, "refusal": refusal,
                "via": action.raw.get("via"),
            })

            if action.is_terminal:
                break

            if action.kind == "test":
                tests_ordered.append(action.payload or action.text)
                result = measurement.inference_measurement(action.text)
                dialogue.append({"role": "measurement", "text": result})
                patient.add_hist(result)
                if action.tool_calls:
                    doctor.deliver_tool_result(action, result)
                    incoming = None
                else:
                    incoming = result
                continue

            if action.kind == "look":
                # The vision tool (image-input contract). Re-attach the image on the
                # next turn when we have one; otherwise say plainly that there is no
                # image, rather than letting the agent hallucinate a reading.
                if vision_on:
                    image_pending = True
                    result = ("The case image is attached to this message. "
                              "Describe what you see.")
                else:
                    result = (f"No image is available in this run (vision={vision})."
                              f" {image_error or ''}").strip()
                dialogue.append({"role": "system", "text": result,
                                 "question": action.payload})
                if action.tool_calls:
                    doctor.deliver_tool_result(action, result)
                    incoming = None
                else:
                    incoming = result
                continue

            if action.kind == "images":
                images_requested += 1
                if vision_on:
                    image_pending = True
                    result = ("The case images are attached." if image
                              else "No images are available for this case.")
                else:
                    result = (f"Images are unavailable in this run "
                              f"(vision={vision}). {image_error or ''}").strip()
                dialogue.append({"role": "system", "text": result})
                if action.tool_calls:
                    doctor.deliver_tool_result(action, result)
                    incoming = None
                else:
                    incoming = result
                continue

            # A question to the patient.
            reply = patient.inference_patient(action.text)
            dialogue.append({"role": "patient", "text": reply})
            measurement.add_hist(reply)
            incoming = reply

    except Exception as e:  # noqa: BLE001 — classified, not swallowed
        error = f"{type(e).__name__}: {e}"
        if is_infra_error(e):
            infra_fail = True
            findings.append(infra_finding(error, turns=len(dialogue)).as_dict())
        else:
            raise

    # ── score ───────────────────────────────────────────────────────────────────
    correct_dx = scenario.diagnosis_information()
    committed = action is not None and action.is_terminal and not infra_fail
    if committed:
        assert action is not None
        raw = ""
        verdict: Optional[ConstrainedChoice] = None
        try:
            # Constrained to exactly "yes"/"no": upstream's grading rule is a literal
            # string test, so a routed model answering "Yes." would mark a CORRECT
            # diagnosis wrong. The prompt is untouched; only the decode is pinned.
            verdict = moderate(action.text, correct_dx, support)
            raw = verdict.raw.strip().lower()
        except Exception as e:  # noqa: BLE001 — a dead moderator is infra, not a miss
            error = error or f"moderator: {type(e).__name__}: {e}"
            if is_infra_error(e):
                infra_fail = True
                findings.append(infra_finding(error, phase="moderator").as_dict())
        if not infra_fail:
            ok = moderator_says_yes(verdict.value if verdict else raw)
            score = CaseScore(
                outcome="correct" if ok else "incorrect",
                correctness=ok,
                doctor_diagnosis=action.payload,
                doctor_final_text=action.text,
                moderator_raw=raw,
                # The grader's reply VERBATIM, before any lowercasing or canonical-
                # ization, so an artifact can always be re-graded by hand.
                moderator_raw_text=verdict.raw if verdict else raw,
                moderator_lenient=moderator_says_yes_lenient(raw),
                moderator_attempts=verdict.attempts if verdict else 0,
                moderator_normalized=bool(verdict.normalized) if verdict else False,
                moderator_unconstrained=bool(verdict and not verdict.resolved),
                declined=False,
                refusal_evidence=[r["evidence"] for r in refusals],
                format_deviation=format_deviation,
                # A refusal that QUOTED the marker trips upstream's substring
                # detector. Scored as upstream scores it; flagged so the report can
                # say what it really was.
                spurious_commit=bool(looks_like_refusal(action.text)),
            )
        else:
            score = CaseScore("infra_fail", None, None, action.text if action else "")
    elif infra_fail:
        score = CaseScore("infra_fail", None, None,
                          action.text if action is not None else "")
    else:
        declined = bool(refusals)
        source = "pattern" if declined else None
        reason = ""
        if not declined and decline_judge and dialogue:
            # Role-scope deferrals ("that's for your doctor to determine") are
            # refusals with none of the standard phrasing. One classifier call keeps
            # them out of the "ran out of turns" bucket, where they would understate
            # the boundary this whole accounting exists to measure.
            closing = "\n\n".join(d["text"] for d in dialogue
                                  if d["role"] == "doctor")[-4000:]
            verdict = judge_declination(support, closing)
            if verdict.get("declined") is True:
                declined, source, reason = True, "judge", verdict.get("reason", "")
        score = CaseScore(
            outcome="declined" if declined else "no_commit",
            correctness=False,   # upstream scores a non-commitment as not-correct
            doctor_diagnosis=None,
            doctor_final_text=dialogue[-1]["text"] if dialogue else "",
            declined=declined,
            refusal_evidence=[r["evidence"] for r in refusals],
            format_deviation=format_deviation,
            decline_source=source,
            decline_reason=reason,
        )

    return {
        "scenario_id": scenario.id,
        "scenario_index": scenario.index,
        "dataset": scenario.dataset,
        "mode": mode,
        "correct_diagnosis": correct_dx,
        "answer_options": scenario.answer_options,
        "score": score.as_dict(),
        "infra_fail": infra_fail,
        "error": error,
        "findings": findings,
        "inferences_used": getattr(doctor, "infs", len(latencies_ms)),
        "max_inferences": total_inferences,
        "tests_ordered": tests_ordered,
        "images_requested": images_requested,
        "image": ({"url": image.url, "bytes": image.n_bytes,
                   "media_type": image.media_type} if image else None),
        "image_error": image_error,
        "vision": vision,
        "refusals": refusals,
        "dialogue": dialogue,
        "latency_ms": latencies_ms,
        "wall_s": round(time.monotonic() - t0, 2),
    }


def load_case_image(scenario: Scenario, vision: str
                    ) -> tuple[Optional[CaseImage], Optional[str]]:
    """Fetch the case image when vision is on. A failure is recorded on the case (so
    the artifact says the image never arrived) and the case still runs text-only."""
    if vision == OFF or scenario.dataset not in IMAGE_DATASETS:
        return None, None
    url = scenario.image_url
    try:
        return fetch_image(url or ""), None
    except VisionError as e:
        return None, str(e)
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


# ── text-mode factory ───────────────────────────────────────────────────────────

def make_text_doctor(scenario: Scenario, cfg: DoctorConfig,
                     image: Optional[CaseImage],
                     doctor_bias: Optional[str] = None) -> WhissleDoctor:
    cfg = DoctorConfig(**{**cfg.__dict__})
    cfg.bias_prompt = bias_text(doctor_bias, DOCTOR_BIASES)
    return WhissleDoctor(cfg, scenario.examiner_information(), image=image)


# ── artifacts ───────────────────────────────────────────────────────────────────

def run_dir(root: Optional[Path] = None, *, tag: str = "") -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = (root or RESULTS_ROOT) / (f"{ts}-{tag}" if tag else ts)
    (d / "cases").mkdir(parents=True, exist_ok=True)
    return d


def write_case(out: Path, case: dict[str, Any]) -> Path:
    p = out / "cases" / f"{case['scenario_id']}.json"
    p.write_text(json.dumps(case, ensure_ascii=False, indent=2, default=str),
                 encoding="utf-8")
    return p


def render_transcript(case: dict[str, Any]) -> str:
    """The dialogue as a human reads it — the artifact a clinician can review."""
    label = {"doctor": "DOCTOR", "patient": "PATIENT",
             "measurement": "MEASUREMENT", "system": "SYSTEM"}
    lines = [f"# {case['scenario_id']}  ({case['dataset']}, {case['mode']} mode)",
             f"correct diagnosis: {case['correct_diagnosis']}",
             f"outcome: {case['score']['outcome']}", ""]
    for d in case["dialogue"]:
        lines.append(f"{label.get(d['role'], d['role'].upper())}: {d['text']}")
    return "\n".join(lines) + "\n"
