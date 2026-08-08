# Copyright Sierra
"""CLI — run AgentClinic against a real Whissle agent.

    python -m tau2.health.agentclinic.run --dataset MedQA --limit 5

Every artifact records N and how N was chosen (limit / sample / seed), which agent
played the doctor, which model backed the benchmark's own agents, and which transport
carried the dialogue — a number without that provenance is not a result.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

from tau2.health.agentclinic.agents import make_support_llm
from tau2.health.agentclinic.dataset import (
    DATASETS,
    IMAGE_DATASETS,
    Scenario,
    load_scenarios,
    select,
)
from tau2.health.agentclinic.doctor import DoctorConfig, resolve_agent, teardown_agent
from tau2.health.agentclinic.runner import (
    RESULTS_ROOT,
    load_case_image,
    make_text_doctor,
    render_transcript,
    run_case,
    run_dir,
    write_case,
)
from tau2.health.agentclinic.scoring import aggregate, summary_markdown
from tau2.health.agentclinic.vision import OFF, VISION_MODES

load_dotenv()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agentclinic",
        description="AgentClinic with a real Whissle agent as the doctor.")
    p.add_argument("--dataset", default="MedQA", choices=sorted(DATASETS),
                   help="MedQA (107) | MedQA_Ext (214) | NEJM (15) | NEJM_Ext (120) "
                        "| MIMICIV (bring your own file)")
    p.add_argument("--limit", type=int, default=None,
                   help="run only N cases (default: all). N is reported everywhere.")
    p.add_argument("--sample", default="head", choices=("head", "random"),
                   help="head = upstream's first-N subset; random = seeded sample")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--total-inferences", type=int, default=20,
                   help="doctor turns per case (upstream default 20)")
    p.add_argument("--mode", default="text", choices=("text", "voice"))
    p.add_argument("--protocol", default=None, choices=("markers", "tools"),
                   help="doctor action surface; default markers for text (upstream "
                        "contract) and tools for voice")
    p.add_argument("--history", default="native", choices=("native", "agentclinic"),
                   help="native multi-turn messages (default) or upstream's rolling "
                        "single-message history")
    p.add_argument("--prompt-mode", default="override", choices=("override", "agent"),
                   help="override = send AgentClinic's doctor prompt as the system "
                        "prompt (upstream contract, comparable); agent = send no "
                        "system prompt so the agent's OWN shipped prompt and "
                        "guardrails run — the arm that measures the refusal boundary")
    p.add_argument("--vision", default=OFF, choices=list(VISION_MODES),
                   help="how the case image reaches the agent (image datasets only)")
    p.add_argument("--agent-id", default=None,
                   help="the Whissle agent that plays the doctor "
                        "(default $WHISSLE_AGENT_ID)")
    p.add_argument("--agent-type", default=None,
                   help="create a throwaway agent of this seeded type instead, e.g. "
                        "clinical_intake_triage")
    p.add_argument("--support-llm", default="whissle",
                   help="backend for the benchmark's own patient/measurement/"
                        "moderator agents: whissle | litellm:<model>")
    p.add_argument("--patient-bias", default=None)
    p.add_argument("--doctor-bias", default=None)
    p.add_argument("--doctor-image-request", action="store_true",
                   help="upstream's --doctor_image_request: the image is withheld "
                        "until the doctor asks for it")
    p.add_argument("--no-decline-judge", action="store_true",
                   help="skip the LLM classifier that catches role-scope refusals the "
                        "deterministic patterns miss (one call per non-committing case)")
    p.add_argument("--concurrency", type=int, default=4,
                   help="cases in parallel (text only; voice is forced to 1)")
    p.add_argument("--out", default=None, help=f"results root (default {RESULTS_ROOT})")
    p.add_argument("--tag", default="", help="suffix for the run directory")
    p.add_argument("--audio", action="store_true",
                   help="voice mode: write per-case duplex WAV evidence")
    return p


def _percentile(xs: list[int], q: float) -> Optional[int]:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def _run_one(scenario: Scenario, args: argparse.Namespace, cfg: DoctorConfig,
             out: Path) -> dict[str, Any]:
    """One case, end to end, never raising: an unexpected exception is recorded on
    the case (and shows up in the report) rather than killing the run."""
    support = make_support_llm(args.support_llm)
    image, image_error = (None, None)
    if args.mode == "text":
        image, image_error = load_case_image(scenario, args.vision)
    try:
        if args.mode == "voice":
            from tau2.health.agentclinic.voice import VoiceDoctor

            doctor = VoiceDoctor(cfg, scenario.examiner_information())
            try:
                case = run_case(
                    scenario, doctor, support,
                    total_inferences=args.total_inferences,
                    patient_bias=args.patient_bias, vision=OFF,
                    img_request=False, mode="voice",
                    decline_judge=not args.no_decline_judge)
                if args.audio:
                    audio_dir = out / "audio"
                    audio_dir.mkdir(parents=True, exist_ok=True)
                    case["audio"] = doctor.finish(
                        str((audio_dir / scenario.id).resolve()), transcribe=False)
                case["voice"] = {
                    "room": doctor.transport.room,
                    "conversation_id": doctor.transport.conversation_id,
                    "latencies_ms": list(doctor.transport.latencies_ms),
                    "turns": doctor.turns,
                }
            finally:
                doctor.stop()
        else:
            doctor = make_text_doctor(scenario, cfg, image, args.doctor_bias)
            case = run_case(
                scenario, doctor, support,
                total_inferences=args.total_inferences,
                patient_bias=args.patient_bias, vision=args.vision,
                img_request=args.doctor_image_request, mode="text",
                decline_judge=not args.no_decline_judge,
                image=image, image_error=image_error)
            case["doctor_turns"] = doctor.turns
    except Exception as e:  # noqa: BLE001 — a case must never sink the run
        case = {
            "scenario_id": scenario.id, "scenario_index": scenario.index,
            "dataset": scenario.dataset, "mode": args.mode,
            "correct_diagnosis": scenario.diagnosis_information(),
            "score": {"outcome": "infra_fail", "correctness": None,
                      "doctor_diagnosis": None, "doctor_final_text": "",
                      "declined": False, "refusal_evidence": [],
                      "format_deviation": False},
            "infra_fail": True,
            "error": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc()[-2000:],
            "dialogue": [], "tests_ordered": [], "findings": [],
        }
    case["support_llm"] = getattr(support, "name", args.support_llm)
    case["support_llm_cost_usd"] = round(float(getattr(support, "cost_usd", 0.0)), 5)
    write_case(out, case)
    (out / "transcripts").mkdir(parents=True, exist_ok=True)
    (out / "transcripts" / f"{scenario.id}.txt").write_text(
        render_transcript(case), encoding="utf-8")
    return case


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.protocol is None:
        args.protocol = "tools" if args.mode == "voice" else "markers"
    if args.mode == "voice":
        if args.vision != OFF:
            print("error: voice mode carries no image channel — use --vision off "
                  "(run the vision variant in text mode)", file=sys.stderr)
            return 2
        if args.protocol != "tools":
            print("warning: voice mode with --protocol markers expects the doctor to "
                  "SAY 'REQUEST TEST:' out loud; tools is the realistic surface.",
                  file=sys.stderr)
        args.concurrency = 1
    if args.vision != OFF and args.dataset not in IMAGE_DATASETS:
        print(f"warning: --vision {args.vision} on a text-only dataset "
              f"({args.dataset}); no images exist to send.", file=sys.stderr)

    scenarios = load_scenarios(args.dataset)
    chosen = select(scenarios, limit=args.limit, sample=args.sample, seed=args.seed)

    provisioned = resolve_agent(args.agent_id, args.agent_type)
    cfg = DoctorConfig(
        agent_id=provisioned.agent_id,
        protocol=args.protocol,
        history=args.history,
        prompt_mode=args.prompt_mode,
        vision=args.vision,
        max_infs=args.total_inferences,
        img_request=bool(args.doctor_image_request
                         and args.dataset in IMAGE_DATASETS),
    )
    cfg.require()

    out = run_dir(Path(args.out) if args.out else None, tag=args.tag or args.dataset)
    meta = {
        "dataset": args.dataset,
        "ts": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "mode": args.mode,
        "protocol": args.protocol,
        "history": args.history,
        "prompt_mode": args.prompt_mode,
        "vision": args.vision,
        "limit": args.limit,
        "sample": args.sample,
        "seed": args.seed,
        "total_inferences": args.total_inferences,
        "dataset_size": len(scenarios),
        "selected_ids": [s.index for s in chosen],
        "agent_id": provisioned.agent_id,
        "agent_type": provisioned.agent_type,
        "agent_created_for_run": provisioned.created,
        "support_llm": args.support_llm,
        "decline_judge": not args.no_decline_judge,
        "patient_bias": args.patient_bias,
        "doctor_bias": args.doctor_bias,
        "base": cfg.base,
        "upstream": "github.com/SamuelSchmidgall/AgentClinic (arXiv:2405.07960)",
    }
    (out / "RUN.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"agentclinic: {len(chosen)}/{len(scenarios)} {args.dataset} cases • "
          f"mode={args.mode} protocol={args.protocol} vision={args.vision} • "
          f"agent={provisioned.agent_id[:8]}… → {out}", file=sys.stderr)

    cases: list[dict[str, Any]] = []
    try:
        if args.concurrency > 1:
            with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
                futs = {ex.submit(_run_one, s, args, cfg, out): s for s in chosen}
                for f in as_completed(futs):
                    c = f.result()
                    cases.append(c)
                    print(f"  [{len(cases)}/{len(chosen)}] {c['scenario_id']}: "
                          f"{c['score']['outcome']}", file=sys.stderr)
        else:
            for s in chosen:
                c = _run_one(s, args, cfg, out)
                cases.append(c)
                print(f"  [{len(cases)}/{len(chosen)}] {c['scenario_id']}: "
                      f"{c['score']['outcome']}", file=sys.stderr)
    finally:
        if provisioned.created:
            ok = teardown_agent(provisioned)
            meta["agent_deleted"] = ok

    cases.sort(key=lambda c: c.get("scenario_index", 0))
    lat = [x for c in cases for x in (c.get("latency_ms") or []) if isinstance(x, int)]
    if args.mode == "voice":
        lat = [t.get("latency_ms") for c in cases
               for t in ((c.get("voice") or {}).get("turns") or [])
               if isinstance(t.get("latency_ms"), int)]
    summary = aggregate(cases, meta={**meta,
                                     "latency_p50_ms": _percentile(lat, 0.5),
                                     "latency_p90_ms": _percentile(lat, 0.9)})
    (out / "SUMMARY.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "SUMMARY.md").write_text(summary_markdown(summary), encoding="utf-8")
    print(summary_markdown(summary))
    print(f"artifacts: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
