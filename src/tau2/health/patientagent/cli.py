"""CLI for running PatientAgentBench against Whissle.

    python -m tau2.health.patientagent.cli run    --mode harness --limit 10
    python -m tau2.health.patientagent.cli sample --cases cases.json --limit 40
    python -m tau2.health.patientagent.cli report --run-dir output/... --mode harness

``run`` needs PatientAgentBench installed (see PATIENTAGENTBENCH.md — it wants its own
venv, because its pinned langchain 1.x conflicts with tau2's 0.3.x). ``sample`` and
``report`` are pure and run anywhere, which is also how they are unit-tested.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

from tau2.health.model_router import DEFAULT_JUDGE_MODELS, JUDGE_PROVIDERS, WHISSLE
from tau2.health.patientagent.collect import (
    DiagnosticsContext,
    case_metadata_from_run,
    collect_outcomes,
    find_experiment_dirs,
)
from tau2.health.patientagent.report import write_report
from tau2.health.patientagent.sampling import (
    DEFAULT_STRATA_KEYS,
    load_cases,
    stratified_sample,
    write_cases,
)
from tau2.health.patientagent.scoring import compare_runs, summarize_run

# Imported as a module (not by name) because ``judge_model`` pulls in langchain_core,
# which only exists inside the PatientAgentBench venv. ``sample``/``report`` must keep
# working anywhere, so the import is deferred to the functions that need it.
DEFAULT_RESULTS_ROOT = "results/whissle/patientagentbench"

MODE_TO_AGENT_CLASS = {
    "harness": "whissle",
    "native": "whissle-native",
    "voice": "whissle-voice",
}

# The paper's setup, which the shipped default config does NOT reproduce: it runs
# 10 turns and a single evaluator. Both are overridden here so our numbers sit on
# the same footing as the published leaderboard.
PAPER_MAX_TURNS = 15
PAPER_JURY = ["claude-opus-4.8-bedrock", "gpt-5.5-api"]


def build_pab_config(
    *,
    mode: str,
    max_turns: int,
    jury: list[str],
    patient_model: str,
    sandbox_model: str,
    label: str,
) -> dict[str, Any]:
    """Build a PatientAgentBench config JSON with the Whissle assistant selected.

    Only ``assistant_agent`` is ours by definition. Everything else — patient
    simulator, sandbox, jury — is selected by ``--judge-provider``:

    * ``whissle`` (default): those three route through our own model API, so the whole
      benchmark runs on one ``WHISSLE_API_KEY``. Right for internal diagnostics; NOT an
      independent evaluation (see ``model_router.INDEPENDENCE_CAVEAT``).
    * ``anthropic`` / ``openai``: the measurement environment is a third party's, which
      is the footing a number published against the paper's leaderboard needs.
    """
    return {
        "max_turns": max_turns,
        "strip_thinking_content": True,
        "aggregation_method": "average",
        "assistant_agent": [
            {
                # model_id "whissle" is a sentinel: the agent's own configured model
                # is used unless WHISSLE_MODEL overrides it.
                "model": {"model_id": "whissle", "max_tokens": 8192, "provider": "bedrock"},
                "agent_class": MODE_TO_AGENT_CLASS[mode],
                "prompt": "default_prompt",
                "label": label,
            }
        ],
        "user_agent": [{"model": {"model": patient_model}, "prompt": "default_prompt"}],
        "evaluator_model": [{"model": name} for name in jury],
        "sandbox_model": {"model": sandbox_model},
        "seed_generator_model": {"model": patient_model},
    }


def cmd_sample(args: argparse.Namespace) -> int:
    entries = load_cases(args.cases)
    selected, report = stratified_sample(
        entries, args.limit, seed=args.seed, strata_keys=args.strata
    )
    if args.out:
        write_cases(args.out, selected)
        print(f"wrote {len(selected)} cases -> {args.out}")
    print(report.to_json())
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Re-derive the report from an existing run directory."""
    experiment_dirs = find_experiment_dirs(args.run_dir)
    if not experiment_dirs:
        print(f"no experiment directories under {args.run_dir}", file=sys.stderr)
        return 1

    out_dir = args.out or os.path.join(DEFAULT_RESULTS_ROOT, os.path.basename(args.run_dir.rstrip("/")))
    metadata = case_metadata_from_run(args.run_dir)

    sample_report = _read_sidecar(args.run_dir, "whissle_sampling.json")
    # Which judge graded this run. Written by `run`; absent for a run produced before
    # judge routing existed, in which case the report says "unrecorded" rather than
    # guessing — an unlabelled judge is exactly the ambiguity this record prevents.
    judge = _read_sidecar(args.run_dir, "whissle_judge.json")

    provenance = {
        "run_dir": args.run_dir,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "whissle_base": os.getenv("WHISSLE_BASE", ""),
        "whissle_agent_id": os.getenv("WHISSLE_AGENT_ID", "")[:8] + "…",
    }

    # Per-case diagnostics: the run-level facts (mode, judge + independence, seed and
    # strata, agent, base URL, judge spend) copied down onto every case file so one
    # case travels self-describing. A voice run additionally gets its flow trace
    # fetched — it is the only mode that can have one.
    diagnostics_ctx = DiagnosticsContext(
        mode=args.mode,
        judge=judge,
        sampling=sample_report,
        provenance={"agent_id": os.getenv("WHISSLE_AGENT_ID", "") or None,
                    "base_url": os.getenv("WHISSLE_BASE", "") or None,
                    "label": args.label,
                    "voice_subset": bool(getattr(args, "voice_subset_run", False))},
        run_dir=args.run_dir,
        n_cases=max(1, int((sample_report or {}).get("n_selected") or 1)),
        fetch_flow_trace=(args.mode == "voice"),
    )

    outcomes = []
    for experiment_dir in experiment_dirs:
        outcomes.extend(
            collect_outcomes(
                experiment_dir,
                artifact_dir=os.path.join(out_dir, "cases"),
                case_metadata=metadata,
                diagnostics=diagnostics_ctx,
            )
        )

    mode = {"harness": "harness_tools", "native": "agent_tools", "voice": "agent_tools"}.get(
        args.mode, args.mode
    )
    summary = summarize_run(outcomes, label=args.label, mode=mode)

    comparison = None
    if args.compare_to:
        with open(args.compare_to, "r", encoding="utf-8") as handle:
            other = json.load(handle)
        # The delta only means something when both runs used the same tool surface.
        comparison = compare_runs(other, summary) if args.mode == "voice" else compare_runs(summary, other)

    paths = write_report(
        out_dir,
        summary,
        agent_label=args.label,
        sample_report=sample_report,
        comparison=comparison,
        judge=judge,
        provenance=provenance,
    )
    print(json.dumps({"summary": summary, "paths": paths}, indent=2, default=str))
    return 0


def _read_sidecar(run_dir: str, name: str) -> Optional[dict[str, Any]]:
    """A run sidecar (sampling / judge record), or None when the run predates it."""
    path = os.path.join(run_dir, name)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        try:
            return json.load(handle)
        except ValueError:
            return None


def resolve_judge(args: argparse.Namespace) -> dict[str, Any]:
    """Resolve ``--judge-provider`` into the registry keys their config wants.

    On the default (``whissle``) route this also INSTALLS the Whissle model keys into
    PatientAgentBench's registry and wraps its model factory — see
    ``judge_model.install``. Nothing is forked or patched on disk.
    """
    from tau2.health import model_router
    from tau2.health.patientagent import judge_model

    provider = args.judge_provider
    if provider == model_router.WHISSLE:
        judge_model.install()
    else:
        model_router.require_provider_key(provider)

    jury = list(args.jury) if args.jury else judge_model.jury_for(provider, args.judge_model)
    patient = args.patient_model or judge_model.patient_for(provider, args.judge_model)
    sandbox = args.sandbox_model or judge_model.sandbox_for(provider, args.judge_model)
    return {
        "jury": jury,
        "patient_model": patient,
        "sandbox_model": sandbox,
        **model_router.judge_provenance(provider, args.judge_model),
        # The paper averages K=2 evaluators. On the Whissle route both would be the
        # same endpoint — one grader sampled twice, not a jury — so K=1 there, stated
        # rather than implied.
        "jury_k": len(jury),
    }


def cmd_run(args: argparse.Namespace) -> int:
    """Sample, register the Whissle agents, run their harness, then report."""
    from tau2.health.patientagent.register import register

    judge = resolve_judge(args)
    print(f"[whissle] judge provider: {judge['judge_endpoint']} "
          f"(K={judge['jury_k']}, "
          f"{'independent' if judge['judge_independent'] else 'NOT independent'}); "
          f"patient={judge['patient_model']} sandbox={judge['sandbox_model']}")

    registered = register(include_voice=(args.mode == "voice"))
    agent_class = MODE_TO_AGENT_CLASS[args.mode]
    if agent_class not in registered:
        print(
            f"agent_class {agent_class} is unavailable "
            f"(registered: {sorted(registered)}). For voice, install the tau2 voice extras.",
            file=sys.stderr,
        )
        return 1

    from patient_agent_bench.run import cmd_benchmark, setup_parser

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = args.name or f"whissle_{args.mode}_{stamp}"
    work_dir = os.path.join(args.output_dir, run_name)
    os.makedirs(work_dir, exist_ok=True)

    # 1. Sample the cases (seeded, stratified) and record how.
    entries = load_cases(args.cases)
    selected, sample_report = stratified_sample(
        entries, args.limit, seed=args.seed, strata_keys=args.strata
    )
    cases_path = os.path.join(work_dir, "sampled_cases.json")
    write_cases(cases_path, selected)
    with open(os.path.join(work_dir, "whissle_sampling.json"), "w", encoding="utf-8") as handle:
        json.dump(sample_report.to_dict(), handle, indent=2)
    print(f"[whissle] sampled {sample_report.n_selected}/{sample_report.n_population} "
          f"cases (seed={args.seed}, strata={'x'.join(args.strata)})")

    # 2. Config: our assistant, and the judge/simulator stack --judge-provider chose.
    config = build_pab_config(
        mode=args.mode,
        max_turns=args.max_turns,
        jury=judge["jury"],
        patient_model=judge["patient_model"],
        sandbox_model=judge["sandbox_model"],
        label=args.label,
    )
    config_path = os.path.join(work_dir, "pab_config.json")
    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
    # Which judge produced this run's numbers, written beside the config and picked up
    # by `report` so it lands in summary.json / REPORT.md. A number must never travel
    # without it.
    with open(os.path.join(work_dir, "whissle_judge.json"), "w", encoding="utf-8") as handle:
        json.dump(judge, handle, indent=2)

    # 3. Their runner, via their own argument parser.
    argv = [
        "benchmark",
        "--cases", cases_path,
        "--config", config_path,
        "--output-dir", work_dir,
        "--max-parallel", str(args.max_parallel),
    ]
    if args.log_level:
        argv += ["--log-level", args.log_level]
    print(f"[whissle] patient-agent-bench {' '.join(argv)}")
    bench_args = setup_parser().parse_args(argv)
    cmd_benchmark(bench_args)

    # 3b. What the judge/simulator stack cost. On the Whissle route those calls are
    # metered against our own wallet, and a jury is many calls per session, so the
    # total is printed and written into the run's judge record rather than left
    # invisible until the invoice arrives.
    if args.judge_provider == WHISSLE:
        from tau2.health.patientagent import judge_model

        judge.update(judge_model.spend())
        n = max(1, sample_report.n_selected)
        judge["judge_calls_per_case"] = round(judge["judge_calls"] / n, 1)
        judge["judge_cost_usd_per_case"] = round(judge["judge_cost_usd"] / n, 5)
        print(f"[whissle] judge spend: {judge['judge_calls']} calls, "
              f"${judge['judge_cost_usd']:.4f} "
              f"({judge['judge_calls_per_case']}/case, "
              f"${judge['judge_cost_usd_per_case']:.4f}/case)")
        with open(os.path.join(work_dir, "whissle_judge.json"), "w", encoding="utf-8") as handle:
            json.dump(judge, handle, indent=2)

    # 4. Locate the run directory their runner created and report on it.
    run_dir = _latest_run_dir(work_dir)
    if not run_dir:
        print(f"[whissle] no run directory produced under {work_dir}", file=sys.stderr)
        return 1
    report_args = argparse.Namespace(
        run_dir=run_dir,
        out=os.path.join(DEFAULT_RESULTS_ROOT, run_name),
        mode=args.mode,
        label=args.label,
        compare_to=args.compare_to,
    )
    # The sampling + judge records live beside the config; copy them where report
    # expects them.
    _link_sampling(work_dir, run_dir)
    _copy_sidecar(work_dir, run_dir, "whissle_judge.json")
    rc = cmd_report(report_args)

    # 5. Optional voice slice — the same cases over the real spoken pipeline.
    rc_voice = cmd_voice_subset(args, selected, judge, run_name)
    return rc or rc_voice


def cmd_voice_subset(args: argparse.Namespace, selected: list,
                     judge: dict[str, Any], run_name: str) -> int:
    """Re-run the head N of the sampled set through the REAL voice pipeline.

    The trade this exists to make: a 100-case text run is what produces a score
    (parallel, cheap, deterministic), but the per-turn signals that make a bad score
    explainable — hesitation, shadow/eager-reply activity, speculative tools,
    emotion/intent, barge-in, real spoken latency — exist ONLY over audio. A small
    voice slice of the SAME cases buys those signals without paying for 100 live
    calls, and because the slice is the head of an already-seeded stratified sample
    it is reproducible.

    The slice runs as its OWN PatientAgentBench run, into ``<work>/voice``, and is
    reported separately. A voice number carries ASR and TTS error a text number does
    not; averaging the two would destroy the only thing that makes the comparison
    interesting."""
    n = int(getattr(args, "voice_subset", 0) or 0)
    if n <= 0:
        return 0
    if args.mode == "voice":
        print("[whissle] --voice-subset is a no-op in --mode voice (the whole run is "
              "already voice)", file=sys.stderr)
        return 0

    from tau2.health.patientagent.register import register

    registered = register(include_voice=True)
    if MODE_TO_AGENT_CLASS["voice"] not in registered:
        print("[whissle] --voice-subset skipped: the voice agent is unavailable "
              "(install the tau2 voice extras)", file=sys.stderr)
        return 0

    from patient_agent_bench.run import cmd_benchmark, setup_parser

    slice_ = list(selected[:n])
    work_dir = os.path.join(args.output_dir, run_name, "voice")
    os.makedirs(work_dir, exist_ok=True)
    cases_path = os.path.join(work_dir, "sampled_cases.json")
    write_cases(cases_path, slice_)

    config = build_pab_config(
        mode="voice", max_turns=args.max_turns, jury=judge["jury"],
        patient_model=judge["patient_model"], sandbox_model=judge["sandbox_model"],
        label=f"{args.label} (voice)",
    )
    config_path = os.path.join(work_dir, "pab_config.json")
    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
    with open(os.path.join(work_dir, "whissle_judge.json"), "w", encoding="utf-8") as handle:
        json.dump(judge, handle, indent=2)
    with open(os.path.join(work_dir, "whissle_sampling.json"), "w", encoding="utf-8") as handle:
        json.dump({"n_requested": n, "n_selected": len(slice_),
                   "n_population": len(selected), "seed": args.seed,
                   "strata_keys": list(args.strata),
                   "selection": "head of the seeded stratified sample (reproducible)",
                   "voice_subset_of": run_name}, handle, indent=2)

    print(f"[whissle] voice subset: {len(slice_)} case(s) over the real voice "
          f"pipeline → {work_dir}", file=sys.stderr)
    argv = ["benchmark", "--cases", cases_path, "--config", config_path,
            "--output-dir", work_dir, "--max-parallel", "1"]
    if args.log_level:
        argv += ["--log-level", args.log_level]
    cmd_benchmark(setup_parser().parse_args(argv))

    run_dir = _latest_run_dir(work_dir)
    if not run_dir:
        print("[whissle] voice subset produced no run directory", file=sys.stderr)
        return 0
    _link_sampling(work_dir, run_dir)
    _copy_sidecar(work_dir, run_dir, "whissle_judge.json")
    return cmd_report(argparse.Namespace(
        run_dir=run_dir,
        out=os.path.join(DEFAULT_RESULTS_ROOT, run_name, "voice"),
        mode="voice", label=f"{args.label} (voice)", compare_to=None,
        voice_subset_run=True,
    ))


def _latest_run_dir(work_dir: str) -> Optional[str]:
    candidates = [
        os.path.join(work_dir, name)
        for name in os.listdir(work_dir)
        if os.path.isdir(os.path.join(work_dir, name)) and find_experiment_dirs(os.path.join(work_dir, name))
    ]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def _copy_sidecar(work_dir: str, run_dir: str, name: str) -> None:
    source, target = os.path.join(work_dir, name), os.path.join(run_dir, name)
    if os.path.exists(source) and not os.path.exists(target):
        with open(source, "r", encoding="utf-8") as src, open(target, "w", encoding="utf-8") as dst:
            dst.write(src.read())


def _link_sampling(work_dir: str, run_dir: str) -> None:
    _copy_sidecar(work_dir, run_dir, "whissle_sampling.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tau2-patientagentbench",
        description="Run PatientAgentBench against a Whissle agent.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cases", required=False, default="data/sample_benchmark.json",
                        help="benchmark case file (JSON list of entries)")
    common.add_argument("--limit", type=int, default=0,
                        help="sample size; 0 or >= population runs everything")
    common.add_argument("--seed", type=int, default=42, help="sampling seed")
    common.add_argument("--strata", nargs="+", default=list(DEFAULT_STRATA_KEYS),
                        help="case attributes to stratify on")

    p_sample = sub.add_parser("sample", parents=[common], help="preview a seeded stratified sample")
    p_sample.add_argument("--out", help="write the sampled case file here")
    p_sample.set_defaults(func=cmd_sample)

    p_run = sub.add_parser("run", parents=[common], help="run the benchmark end to end")
    p_run.add_argument("--mode", choices=sorted(MODE_TO_AGENT_CLASS), default="harness",
                       help="harness = their tools (publishable); native/voice = our tools")
    p_run.add_argument("--max-turns", type=int, default=PAPER_MAX_TURNS)
    p_run.add_argument("--judge-provider", default=WHISSLE, choices=list(JUDGE_PROVIDERS),
                       help="who runs the benchmark's OWN LLMs — patient simulator, "
                            "K=2 jury, sandbox. Default `whissle` routes them through "
                            "our own model API, so the run needs ONLY WHISSLE_API_KEY "
                            "(and is NOT an independent evaluation); `anthropic` / "
                            "`openai` give an independent judge and are what a number "
                            "published against the paper should use")
    p_run.add_argument("--judge-model", default=None,
                       help="override the model for --judge-provider "
                            f"(external defaults: {DEFAULT_JUDGE_MODELS})")
    p_run.add_argument("--jury", nargs="+", default=None,
                       help="explicit evaluator registry keys, overriding "
                            "--judge-provider (paper uses K=2: "
                            f"{' '.join(PAPER_JURY)})")
    p_run.add_argument("--patient-model", default=None,
                       help="explicit patient-simulator registry key, overriding "
                            "--judge-provider")
    p_run.add_argument("--sandbox-model", default=None,
                       help="explicit sandbox registry key, overriding "
                            "--judge-provider")
    p_run.add_argument("--max-parallel", type=int, default=1)
    p_run.add_argument("--output-dir", default="output")
    p_run.add_argument("--name", help="run name (default whissle_<mode>_<timestamp>)")
    p_run.add_argument("--label", default="Whissle")
    p_run.add_argument("--log-level", default="INFO")
    p_run.add_argument("--compare-to", help="a previous summary.json to diff against")
    p_run.add_argument("--voice-subset", type=int, default=0, metavar="N",
                       help="after the run, RE-RUN N of the same cases through the "
                            "REAL voice pipeline as a separate PatientAgentBench run "
                            "(<output-dir>/<name>/voice). Scale and depth at once: the "
                            "text pass gives the score at N=100, the voice slice gives "
                            "the per-turn signals — hesitation, shadow/eager reply, "
                            "speculative tools, emotion/intent, barge-in, spoken "
                            "latency — that exist only over audio. The slice is the "
                            "head of the seeded stratified sample, so it reproduces; "
                            "it is scored and reported SEPARATELY and never averaged "
                            "into the text number.")
    p_run.set_defaults(func=cmd_run)

    p_report = sub.add_parser("report", help="regenerate the report from a run directory")
    p_report.add_argument("--run-dir", required=True)
    p_report.add_argument("--out")
    p_report.add_argument("--mode", choices=sorted(MODE_TO_AGENT_CLASS), default="harness")
    p_report.add_argument("--label", default="Whissle")
    p_report.add_argument("--compare-to")
    p_report.set_defaults(func=cmd_report)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
