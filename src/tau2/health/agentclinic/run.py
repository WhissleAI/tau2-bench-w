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
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

from tau2.health import diagnostics
from tau2.health.agentclinic import diagnostics as case_diag
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
from tau2.health.diagnostics import attach as diag_attach
from tau2.health.agentclinic.vision import OFF, VISION_MODES
from tau2.health.model_router import (
    DEFAULT_JUDGE_MODELS,
    JUDGE_PROVIDERS,
    WHISSLE,
    judge_provenance,
    make_judge_llm,
    require_provider_key,
)

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
    p.add_argument("--judge-provider", default=WHISSLE, choices=list(JUDGE_PROVIDERS),
                   help="who runs the benchmark's own patient / measurement reader / "
                        "moderator / decline classifier. Default `whissle` routes them "
                        "through our own model API and needs ONLY WHISSLE_API_KEY; "
                        "`openai` / `anthropic` give an INDEPENDENT judge (needs that "
                        "provider's key) and are the stronger footing for a number "
                        "published against the paper")
    p.add_argument("--judge-model", default=None,
                   help="model for an external --judge-provider "
                        f"(defaults: {DEFAULT_JUDGE_MODELS})")
    p.add_argument("--support-llm", default=None,
                   help="escape hatch that overrides --judge-provider with a raw spec: "
                        "whissle | litellm:<model>. Use to reproduce a specific "
                        "published configuration.")
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
    p.add_argument("--voice-subset", type=int, default=0, metavar="N",
                   help="after the text pass, RE-RUN N of the same cases through the "
                        "real voice pipeline, into <out>/voice/. Scale and depth at "
                        "once: 100 text cases give the score, a handful of voice "
                        "cases give the per-turn signals (hesitation, shadow, "
                        "emotion/intent, barge-in, latency) that only exist over "
                        "audio. The slice is the seeded head of the sampled set, so "
                        "it is reproducible; voice cases are scored and reported "
                        "SEPARATELY and never averaged into the text number.")
    return p


def _percentile(xs: list[int], q: float) -> Optional[int]:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def _run_one(scenario: Scenario, args: argparse.Namespace, cfg: DoctorConfig,
             out: Path, meta: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """One case, end to end, never raising: an unexpected exception is recorded on
    the case (and shows up in the report) rather than killing the run.

    ``meta`` is the run-level provenance block; it is copied down onto the case's
    ``diagnostics`` envelope so a case file lifted out of the run directory still
    says which agent, which judge, which seed and which stratum produced it."""
    support = make_judge_llm(args.support_llm or args.judge_provider,
                             args.judge_model)
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
    # One support LLM is built PER CASE, so its counters are this case's spend. The
    # run summary sums them — judge calls are the bulk of what a run costs and, on the
    # default route, they bill our own wallet.
    case["support_llm_calls"] = int(getattr(support, "calls", 0))
    case.setdefault("voice_subset", bool(getattr(args, "_is_voice_subset", False)))
    # The shared diagnostic envelope (tau2.health.diagnostics): flow trace where one
    # exists, per-turn voice signals where the transport produces them, explicit
    # unavailability where it does not, tool forensics, per-case provenance + cost.
    diag_attach(case, case_diag.build(
        case,
        meta={**(meta or {}), "mode": args.mode},
        run_dir=str(out),
        trace_client=diagnostics.TraceClient(base=cfg.base, api_key=cfg.api_key),
    ))
    write_case(out, case)
    (out / "transcripts").mkdir(parents=True, exist_ok=True)
    (out / "transcripts" / f"{scenario.id}.txt").write_text(
        render_transcript(case), encoding="utf-8")
    return case


def voice_subset(chosen: list[Scenario], n: int) -> list[Scenario]:
    """The deterministic head of the already-sampled set.

    ``chosen`` is itself the output of the seeded ``select(...)``, so taking its
    first N is reproducible for a given ``--seed``/``--sample`` without adding a
    second, independently-seeded draw that nobody could reconstruct."""
    return list(chosen[:max(0, n)])


def _run_voice_subset(chosen: list[Scenario], args: argparse.Namespace,
                      cfg: DoctorConfig, out: Path,
                      meta: dict[str, Any]) -> list[dict[str, Any]]:
    """Re-drive a slice of a TEXT run through the real voice pipeline.

    Why a slice and not a mode: a 100-case run needs the text channel (parallel,
    cheap, deterministic) to produce a score anyone can stand behind, but the
    per-turn signals that make a failure explainable — hesitation, shadow/eager
    reply activity, speculative tools, emotion/intent, barge-in, real spoken
    latency — exist ONLY over audio. Running a handful of the same cases over voice
    buys the deep signals without paying for 100 live calls.

    The two populations are kept apart on disk (``<out>/voice/``) and in the
    reports (``SUMMARY.voice.json``). They are different measurements — a voice
    number carries ASR and TTS error the text number does not — and averaging them
    would be the exact dishonesty the split exists to prevent."""
    n = int(getattr(args, "voice_subset", 0) or 0)
    if n <= 0:
        return []
    if args.mode == "voice":
        print("note: --voice-subset is a no-op in --mode voice (the whole run is "
              "already voice)", file=sys.stderr)
        return []
    if args.vision != OFF:
        print("note: --voice-subset skipped — voice carries no image channel and this "
              f"run is --vision {args.vision}", file=sys.stderr)
        return []

    slice_ = voice_subset(chosen, n)
    vout = out / "voice"
    vout.mkdir(parents=True, exist_ok=True)
    # A clone of the run's arguments with ONLY the transport changed, so the voice
    # slice is the same cases, the same budgets and the same judges.
    vargs = argparse.Namespace(**vars(args))
    vargs.mode = "voice"
    vargs.protocol = "tools"
    vargs.vision = OFF
    vargs.doctor_image_request = False
    vargs.concurrency = 1
    vargs._is_voice_subset = True
    vcfg = replace(cfg, protocol="tools", vision=OFF, img_request=False)
    vmeta = {**meta, "mode": "voice", "protocol": "tools", "vision": OFF,
             "voice_subset_of": str(out), "voice_subset_n": len(slice_)}

    print(f"agentclinic: voice subset — re-running {len(slice_)} case(s) over the "
          f"real voice pipeline → {vout}", file=sys.stderr)
    out_cases: list[dict[str, Any]] = []
    for s in slice_:
        try:
            c = _run_one(s, vargs, vcfg, vout, vmeta)
        except Exception as e:  # noqa: BLE001 — the voice slice never sinks the run
            print(f"  voice subset {s.id}: FAILED {type(e).__name__}: {e}",
                  file=sys.stderr)
            continue
        out_cases.append(c)
        print(f"  [voice {len(out_cases)}/{len(slice_)}] {c['scenario_id']}: "
              f"{c['score']['outcome']}", file=sys.stderr)
    return out_cases


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

    # Fail in the first second, not the fortieth minute: an external judge provider
    # without its key would otherwise die on the first moderator call of case 1.
    if not args.support_llm and args.judge_provider != WHISSLE:
        require_provider_key(args.judge_provider)

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
        "support_llm": args.support_llm or args.judge_provider,
        # Which judge produced these numbers, and the caveat that goes with it. Stamped
        # on RUN.json and carried into SUMMARY.json/SUMMARY.md so a number can never be
        # read as something it isn't.
        **judge_provenance(args.support_llm or args.judge_provider, args.judge_model),
        "decline_judge": not args.no_decline_judge,
        "patient_bias": args.patient_bias,
        "doctor_bias": args.doctor_bias,
        "base": cfg.base,
        "upstream": "github.com/SamuelSchmidgall/AgentClinic (arXiv:2405.07960)",
    }
    (out / "RUN.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"agentclinic: {len(chosen)}/{len(scenarios)} {args.dataset} cases • "
          f"mode={args.mode} protocol={args.protocol} vision={args.vision} • "
          f"agent={provisioned.agent_id[:8]}… • judge={meta['judge_endpoint']}"
          f"{'' if meta['judge_independent'] else ' (NOT independent)'}"
          f" → {out}", file=sys.stderr)

    cases: list[dict[str, Any]] = []
    voice_cases: list[dict[str, Any]] = []
    try:
        if args.concurrency > 1:
            with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
                futs = {ex.submit(_run_one, s, args, cfg, out, meta): s for s in chosen}
                for f in as_completed(futs):
                    c = f.result()
                    cases.append(c)
                    print(f"  [{len(cases)}/{len(chosen)}] {c['scenario_id']}: "
                          f"{c['score']['outcome']}", file=sys.stderr)
        else:
            for s in chosen:
                c = _run_one(s, args, cfg, out, meta)
                cases.append(c)
                print(f"  [{len(cases)}/{len(chosen)}] {c['scenario_id']}: "
                      f"{c['score']['outcome']}", file=sys.stderr)
        voice_cases = _run_voice_subset(chosen, args, cfg, out, meta)
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

    # The voice slice is summarized SEPARATELY. Same cases, different transport,
    # different error surface — one table, two rows, never one average.
    if voice_cases:
        voice_cases.sort(key=lambda c: c.get("scenario_index", 0))
        vlat = [t.get("latency_ms") for c in voice_cases
                for t in ((c.get("voice") or {}).get("turns") or [])
                if isinstance(t.get("latency_ms"), int)]
        vsummary = aggregate(voice_cases, meta={
            **meta, "mode": "voice", "protocol": "tools", "vision": OFF,
            "voice_subset_of": str(out), "voice_subset_n": len(voice_cases),
            "latency_p50_ms": _percentile(vlat, 0.5),
            "latency_p90_ms": _percentile(vlat, 0.9)})
        (out / "SUMMARY.voice.json").write_text(
            json.dumps(vsummary, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "SUMMARY.voice.md").write_text(
            summary_markdown(vsummary), encoding="utf-8")
        print(f"\nvoice subset ({len(voice_cases)} case(s), scored separately):\n")
        print(summary_markdown(vsummary))

    print(f"artifacts: {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
