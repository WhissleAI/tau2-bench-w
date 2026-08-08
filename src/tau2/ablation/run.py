"""The ablation runner.

    uv run python -m tau2.ablation preflight
    uv run python -m tau2.ablation freeze
    uv run python -m tau2.ablation run --limit 100 --arms A,B
    uv run python -m tau2.ablation report results/whissle/ablation/<run>

Order of operations, and why it is this order:

1. **Freeze/verify the corpus.** The task list and the metrics are declared before
   anything runs, and the corpus digest goes on the run record. A metric added
   after seeing results is marked exploratory, in the artifact, not in a footnote.
2. **Preflight the metadata head.** If the head is not serving, arm B degrades to
   arm A, every delta is zero, and the run looks like a finding. Abort instead.
3. **Perceive once per case.** TTS → ASR → metadata. Shared by every arm, so the
   arms cannot differ in what was heard.
4. **Interleave the arms per case.** Arm A and arm B for case 1, then case 2 — not
   all of A then all of B. Backend load, model routing and provider state drift
   over a run; interleaving makes that drift common-mode across arms instead of
   confounded with them.
5. **Guard.** Same served model per case (read off the response, not the request),
   arm prompts actually different, single-variable spec check. Any failure marks
   the case not-comparable and drops it from the paired analysis.
6. **Score, test, write.** Deterministic graders, paired tests, the shared
   ``tau2.health.diagnostics/v1`` envelope per case, and a run summary.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests
import typer

from tau2.health import diagnostics as diag

from . import SCHEMA
from .arms import (ALL_ARMS, ArmMismatch, Decoding, arm_by_key, assert_arms_differ,
                   assert_served_model, assert_single_variable, build_messages,
                   speech_analysis_block, user_content)
from .audit import find_backend_root, run_audit
from .corpus import CORPUS_PATH, Case, corpus_digest, freeze as freeze_corpus, load as load_corpus
from .grade import grade_asr, grade_case
from .perception import Ear, PerceptionError
from .stats import mcnemar_exact, summarise, wilcoxon

app = typer.Typer(add_completion=False, help="The Whissle metadata ablation.")

RESULTS_ROOT = Path("results/whissle/ablation")
DEFAULT_BASE = "https://aws-gateway-backend.whissle.ai/bot"
BENCHMARK = "metadata_ablation"


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------


class TurnError(RuntimeError):
    pass


class BenchTurn:
    """``POST /api/bench/agent-turn`` with the PR #664 model controls.

    The response's ``model`` and ``usage`` are the reason this endpoint is used
    rather than ``/api/models/chat``: the arms have to be provably served by the
    same model, and cost per case has to be measurable from outside. Earlier runs
    recorded ``null`` for both.
    """

    def __init__(self, base: Optional[str] = None, api_key: Optional[str] = None,
                 timeout: float = 180.0, retries: int = 4) -> None:
        self.base = (base or os.getenv("WHISSLE_BASE") or DEFAULT_BASE).rstrip("/")
        self.api_key = api_key or os.getenv("WHISSLE_API_KEY") or ""
        if not self.api_key:
            raise TurnError("WHISSLE_API_KEY not set")
        self.timeout = timeout
        self.retries = retries
        self._s = requests.Session()
        self._s.headers.update({"Authorization": f"Bearer {self.api_key}",
                                "Content-Type": "application/json"})

    def turn(self, body: dict[str, Any]) -> dict[str, Any]:
        last = "unknown"
        for attempt in range(self.retries):
            t0 = time.time()
            try:
                r = self._s.post(f"{self.base}/api/bench/agent-turn",
                                 data=json.dumps(body), timeout=self.timeout)
            except requests.RequestException as e:
                last = str(e)
            else:
                if r.status_code < 300:
                    d = r.json()
                    d["_latency_ms"] = int((time.time() - t0) * 1000)
                    return d
                if r.status_code < 500 and r.status_code != 429:
                    raise TurnError(f"agent-turn -> HTTP {r.status_code}: {r.text[:300]}")
                last = f"HTTP {r.status_code}: {r.text[:200]}"
            time.sleep(min(3.0 * (attempt + 1), 15.0))
        raise TurnError(f"agent-turn failed after {self.retries} attempts: {last}")


# ---------------------------------------------------------------------------
# cost
# ---------------------------------------------------------------------------

#: Published list prices for the pinned model, USD per million tokens. Cost is
#: computed from the provider-reported ``usage`` #664 now returns, not estimated
#: from characters.
_PRICES = {
    "claude-sonnet-5": (3.0, 15.0),
    "claude-opus-5": (15.0, 75.0),
    "claude-haiku-5": (1.0, 5.0),
}


def _cost(model: str, usage: Optional[dict]) -> Optional[float]:
    if not usage:
        return None
    price = _PRICES.get(model)
    if not price:
        return None
    tin = (usage.get("input_tokens") or 0) + (usage.get("cache_read_input_tokens") or 0)
    tout = usage.get("output_tokens") or 0
    return round(tin * price[0] / 1e6 + tout * price[1] / 1e6, 8)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


@app.command()
def freeze(seed: int = typer.Option(20260808)) -> None:
    """Regenerate and freeze the pre-declared corpus."""
    p = freeze_corpus(CORPUS_PATH, seed)
    cases, meta = load_corpus(p)
    typer.echo(f"froze {len(cases)} cases -> {p}")
    typer.echo(f"digest={meta['digest']} slices={meta['slices']}")


@app.command()
def preflight(probes: int = typer.Option(6, help="metadata-head probes")) -> None:
    """Prove the metadata head is serving before spending a run."""
    ear = Ear()
    pf = ear.preflight(probes=probes)
    typer.echo(json.dumps(pf, indent=2))
    if not pf["serving"]:
        typer.echo(
            "\nABORT: the metadata head answered 0 of "
            f"{probes} probes. Arm B would be byte-identical to arm A and every "
            "delta would be a fake zero. Fix the head before running.", err=True)
        raise typer.Exit(2)


@app.command("audit")
def audit_cmd(backend_root: Optional[str] = typer.Option(None)) -> None:
    """Print the structural audit: which channels reach the brain at all."""
    root = Path(backend_root) if backend_root else find_backend_root()
    typer.echo(json.dumps(run_audit(root), indent=2))


@app.command("substrate")
def substrate_cmd(
    limit: Optional[int] = typer.Option(None),
    slices: str = typer.Option("entity,intent,emotion"),
    concurrency: int = typer.Option(3, help="keep low — the head is capacity-bound"),
    metadata_attempts: int = typer.Option(6),
    run_name: Optional[str] = typer.Option(None),
) -> None:
    """Layer 1: is there information in the probability substrate at all?

    Needs no LLM — only TTS and the transcribe endpoint — so it runs when the brain
    is unavailable, and it answers the question every consumer depends on. A
    consumer cannot extract value from a channel that does not discriminate,
    however well the consumer is written.
    """
    import concurrent.futures as cf

    from .substrate import analyse

    started = datetime.now(timezone.utc)
    cases, corpus_meta = load_corpus(CORPUS_PATH, slices=[s.strip() for s in slices.split(",")])
    if limit:
        cases = cases[:limit]
    name = run_name or f"substrate-{started.strftime('%Y%m%dT%H%M%SZ')}"
    out = RESULTS_ROOT / name
    out.mkdir(parents=True, exist_ok=True)

    ear = Ear(metadata_attempts=metadata_attempts)
    typer.echo(f"substrate {name}: {len(cases)} cases, concurrency={concurrency}")

    def one(case: Case) -> dict[str, Any]:
        rec: dict[str, Any] = {
            "case_id": case.case_id, "slice": case.slice, "spoken": case.spoken,
            "gold_route": case.gold_route, "gold_affect": case.gold_affect,
            "gold_slots": case.gold_slots,
        }
        try:
            p = ear.perceive(case.case_id, case.spoken, require_metadata=True)
        except PerceptionError as e:
            rec["error"] = str(e)
            return rec
        rec["perception"] = p.to_dict()
        rec["asr"] = grade_asr(case, p.asr_text).to_dict()
        rec["speech_analysis_block"] = speech_analysis_block(p.metadata)
        return rec

    records: list[dict[str, Any]] = []
    with cf.ThreadPoolExecutor(max(1, concurrency)) as ex:
        for i, rec in enumerate(ex.map(one, cases), 1):
            records.append(rec)
            has = bool((rec.get("perception") or {}).get("metadata"))
            typer.echo(f"  [{i}/{len(cases)}] {rec['case_id']}: "
                       f"{'substrate' if has else 'NO SUBSTRATE'}")

    analysis = analyse(records)
    asr = [r["asr"] for r in records if r.get("asr")]
    d_tot = sum(a["digit_slots"] for a in asr)
    d_ok = sum(a["digit_recoverable"] for a in asr)
    n_tot = sum(a["noun_slots"] for a in asr)
    n_ok = sum(a["noun_recoverable"] for a in asr)
    firsts = [(r.get("perception") or {}).get("metadata_first_attempt")
              for r in records if r.get("perception")]

    summary = {
        "schema": SCHEMA,
        "layer": "substrate",
        "run_id": name,
        "benchmark": BENCHMARK,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "corpus": {**corpus_meta, "n_run": len(records)},
        "n_total": len(records),
        "n_with_substrate": analysis["n_cases_with_substrate"],
        "metadata_head": {
            "first_attempt_availability": (
                round(sum(1 for f in firsts if f) / len(firsts), 3) if firsts else None),
            "note": ("What an ordinary caller of /api/models/transcribe experiences. "
                     "The head is called in parallel and fails open, so a timeout "
                     "simply omits the `metadata` key with no error."),
        },
        "asr_cascade": {
            "digit_slots": d_tot, "digit_recoverable": d_ok,
            "digit_error_rate": round(1 - d_ok / d_tot, 4) if d_tot else None,
            "proper_noun_slots": n_tot, "proper_noun_recoverable": n_ok,
            "proper_noun_error_rate": round(1 - n_ok / n_tot, 4) if n_tot else None,
        },
        "substrate": analysis,
        "structural_audit": run_audit(find_backend_root()),
    }
    (out / "records.json").write_text(json.dumps(records, indent=1, ensure_ascii=False))
    (out / "SUMMARY.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    typer.echo(f"\nwrote {out}/SUMMARY.json")
    typer.echo(f"  {analysis['headline']}")
    for ch, r in analysis["channels"].items():
        typer.echo(f"  {ch:<8} acc={r['accuracy']} MI={r['mutual_information_bits']}b "
                   f"(bias {r['mi_small_sample_bias_bits']}b) modal={r['modal_label']}"
                   f"@{r['modal_share']} -> {r['verdict']}")


@app.command()
def run(
    agent_id: Optional[str] = typer.Option(None, help="defaults to WHISSLE_AGENT_ID"),
    arms: str = typer.Option("A,B", help="comma-separated arm keys"),
    limit: Optional[int] = typer.Option(None),
    slices: str = typer.Option("entity,intent,emotion"),
    model: str = typer.Option("claude-sonnet-5"),
    provider: str = typer.Option("claude"),
    run_name: Optional[str] = typer.Option(None),
    metadata_attempts: int = typer.Option(6),
    skip_preflight: bool = typer.Option(False, help="never use for a reported run"),
) -> None:
    """Run the ablation."""
    started = datetime.now(timezone.utc)
    agent_id = agent_id or os.getenv("WHISSLE_AGENT_ID") or ""
    if not agent_id:
        raise typer.BadParameter("WHISSLE_AGENT_ID (or --agent-id) is required")

    specs = [arm_by_key(k.strip()) for k in arms.split(",") if k.strip()]
    primary = [s for s in specs if not s.exploratory]
    assert_single_variable(specs)

    cases, corpus_meta = load_corpus(CORPUS_PATH, slices=[s.strip() for s in slices.split(",")])
    if limit:
        cases = cases[:limit]
    digest = corpus_digest(cases)

    name = run_name or started.strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS_ROOT / name
    (out / "cases").mkdir(parents=True, exist_ok=True)

    dec = Decoding(agent_id=agent_id, model=model, provider=provider)
    ear = Ear(metadata_attempts=metadata_attempts)
    turner = BenchTurn()

    # The precondition is "arm B carries real substrate", not "the head answered a
    # probe just now". Perception is cached per case, so a run over a warm cache
    # already has real substrate for every case even if the head is down at this
    # instant — and the head is genuinely volatile (measured between 0% and 99%
    # availability within one session). Check what the run actually needs: the
    # substrate it will use. Probe the head only when the cache cannot supply it.
    cached = 0
    for c in cases:
        p = ear.cache_dir / f"{c.case_id}.json"
        try:
            d = json.loads(p.read_text())
            if d.get("spoken") == c.spoken and d.get("metadata"):
                cached += 1
        except Exception:
            pass
    cache_coverage = cached / len(cases) if cases else 0.0

    if skip_preflight:
        pf: dict[str, Any] = {"skipped": True}
    elif cache_coverage >= 1.0:
        pf = {
            "satisfied_by_cache": True,
            "cache_coverage": 1.0,
            "serving": True,
            "note": ("Every case already has a real metadata sidecar cached from an "
                     "earlier perception pass, so arm B carries genuine substrate on "
                     "every case. The head was not probed live for this run; the "
                     "substrate it uses is the head's own output, recorded when it "
                     "answered."),
        }
    else:
        pf = ear.preflight(probes=6)
        pf["cache_coverage"] = round(cache_coverage, 3)
        if not pf["serving"]:
            typer.echo(
                f"ABORT: the metadata head answered no probes and only "
                f"{100 * cache_coverage:.0f}% of cases have cached substrate. Arm B "
                "would collapse into arm A on the remainder and the run would report "
                "a fake zero. Re-run when the head is serving, or warm the cache with "
                "`substrate` first.", err=True)
            raise typer.Exit(2)

    backend_root = find_backend_root()
    structural = run_audit(backend_root)

    typer.echo(f"ablation {name}: {len(cases)} cases x {len(specs)} arms "
               f"({', '.join(s.key for s in specs)}) model={model}")
    typer.echo(f"corpus digest={digest}  metadata-head preflight={pf.get('availability')}")

    records: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []

    for idx, case in enumerate(cases, 1):
        rec: dict[str, Any] = {
            "case_id": case.case_id, "slice": case.slice, "gold_route": case.gold_route,
            "gold_slots": case.gold_slots, "gold_affect": case.gold_affect,
            "spoken": case.spoken, "arms": {}, "comparable": False, "exclusion": "",
        }
        # -- perception ---------------------------------------------------
        try:
            p = ear.perceive(case.case_id, case.spoken, require_metadata=True)
        except PerceptionError as e:
            rec["exclusion"] = f"perception_failed: {e}"
            excluded.append({"case_id": case.case_id, "reason": rec["exclusion"]})
            records.append(rec)
            typer.echo(f"  [{idx}/{len(cases)}] {case.case_id}: EXCLUDED ({e})")
            continue

        rec["perception"] = p.to_dict()
        rec["asr"] = grade_asr(case, p.asr_text).to_dict()

        if not p.metadata_available:
            rec["exclusion"] = "metadata_head_silent"
            excluded.append({"case_id": case.case_id,
                             "reason": "metadata head never answered — arm B would "
                                       "equal arm A"})
            records.append(rec)
            typer.echo(f"  [{idx}/{len(cases)}] {case.case_id}: EXCLUDED (no metadata)")
            continue

        rec["speech_analysis_block"] = speech_analysis_block(p.metadata)

        # -- the arms, interleaved ----------------------------------------
        contents = {s.key: user_content(case, p, s) for s in specs}
        try:
            assert_arms_differ(case.case_id, contents)
        except ArmMismatch as e:
            rec["exclusion"] = f"arms_identical: {e}"
            excluded.append({"case_id": case.case_id, "reason": "arm prompts identical"})
            records.append(rec)
            typer.echo(f"  [{idx}/{len(cases)}] {case.case_id}: EXCLUDED (arms identical)")
            continue

        served, failed = {}, ""
        for s in specs:
            msgs = build_messages(case, p, s)
            try:
                resp = turner.turn(dec.body_for(msgs))
            except TurnError as e:
                failed = f"{s.key}: {e}"
                break
            text = (resp.get("reply") or "").strip()
            g = grade_case(case, s.key, text, p.asr_text)
            served[s.key] = resp.get("model")
            rec["arms"][s.key] = {
                "metadata_mode": s.metadata_mode,
                "exploratory": s.exploratory,
                "user_content": contents[s.key],
                "reply": text,
                "grade": g.to_dict(),
                "served_model": resp.get("model"),
                "usage": resp.get("usage"),
                "stop_reason": resp.get("stop_reason"),
                "stop_details": resp.get("stop_details"),
                "latency_ms": resp.get("_latency_ms"),
                "cost_usd": _cost(resp.get("model") or model, resp.get("usage")),
            }

        if failed:
            rec["exclusion"] = f"turn_failed: {failed}"
            excluded.append({"case_id": case.case_id, "reason": rec["exclusion"]})
            records.append(rec)
            typer.echo(f"  [{idx}/{len(cases)}] {case.case_id}: EXCLUDED ({failed})")
            continue

        try:
            assert_served_model(case.case_id, served, model)
        except ArmMismatch as e:
            rec["exclusion"] = f"model_mismatch: {e}"
            excluded.append({"case_id": case.case_id, "reason": "served model differed"})
            records.append(rec)
            typer.echo(f"  [{idx}/{len(cases)}] {case.case_id}: EXCLUDED (model mismatch)")
            continue

        rec["comparable"] = True
        records.append(rec)
        _write_case_diagnostics(out / "cases", case, rec, dec, name, digest)
        marks = "".join("+" if rec["arms"][s.key]["grade"]["route_correct"] else "."
                        for s in specs)
        typer.echo(f"  [{idx}/{len(cases)}] {case.case_id}: {marks}")

    summary = _summarise(records, specs, primary, dec, corpus_meta, digest, pf,
                         structural, started, name)
    (out / "records.json").write_text(json.dumps(records, indent=1, ensure_ascii=False))
    (out / "SUMMARY.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    typer.echo(f"\nwrote {out}/SUMMARY.json  ({summary['n_comparable']} comparable "
               f"of {summary['n_total']})")
    for r in summary["paired"]["results"]:
        typer.echo(f"  {r['metric']:<28} delta={r['delta']} p={r['p_value']} "
                   f"[{r['verdict']}]")


def _write_case_diagnostics(directory: Path, case: Case, rec: dict,
                            dec: Decoding, run_name: str, digest: str) -> None:
    """One ``tau2.health.diagnostics/v1`` envelope per case, so this suite's
    artifacts read the same as every other benchmark's."""
    arms = rec.get("arms", {})
    tools = diag.tools_section([], source="ablation (no tools bound)",
                              writes=None,
                              writes_reason="slot payloads are graded in-harness; "
                                            "no external system is mutated")
    envelope = diag.build(
        benchmark=BENCHMARK,
        case_id=case.case_id,
        mode="text",
        flow=diag.flow_unavailable(diag.REASON_NO_FLOW),
        signals=diag.signals_unavailable(
            "the ablation drives /api/bench/agent-turn, a stateless brain call with "
            "no live signal stream"),
        metadata_sidecar=diag.metadata_section(
            [{"n": 1, "user_metadata": [rec["perception"]["metadata"]]}],
            source="/api/models/transcribe — whissle_batch_metadata (whissle-large head)")
        if (rec.get("perception") or {}).get("metadata")
        else diag.metadata_unavailable(
            "the whissle-large metadata head did not answer for this case; it fails "
            "open, so the transcript arrived without a metadata key"),
        tools=tools,
        provenance=diag.provenance(
            BENCHMARK, mode="text",
            transport_endpoint="POST /api/bench/agent-turn",
            agent_id=dec.agent_id, base_url=os.getenv("WHISSLE_BASE") or DEFAULT_BASE,
            seed=20260808, stratum=case.slice, run_id=run_name,
            extra={"corpus_digest": digest, "model": dec.model,
                   "provider": dec.provider, "thinking": dec.thinking,
                   "system_sha": dec.to_dict()["system_sha"],
                   "arms": list(arms.keys())},
        ),
        cost=diag.cost_section(
            agent_calls=len(arms),
            judge_calls=None, judge_cost_usd=None,
            reason="no judge — every primary metric is deterministic",
            agent_cost_usd=round(sum((a.get("cost_usd") or 0.0) for a in arms.values()), 8),
        ),
        extra={"ablation": {k: {kk: vv for kk, vv in v.items() if kk != "user_content"}
                            for k, v in arms.items()},
               "comparable": rec.get("comparable"), "exclusion": rec.get("exclusion")},
    )
    diag.write_case(directory, case.case_id, diag.attach(dict(rec), envelope))


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------


def _pairs(records: list[dict], a_key: str, b_key: str, fn, *, slices=None):
    A, B = [], []
    for r in records:
        if not r.get("comparable"):
            continue
        if slices and r["slice"] not in slices:
            continue
        ga = (r["arms"].get(a_key) or {}).get("grade")
        gb = (r["arms"].get(b_key) or {}).get("grade")
        if not ga or not gb:
            continue
        va, vb = fn(ga, r["arms"][a_key]), fn(gb, r["arms"][b_key])
        if va is None or vb is None:
            continue
        A.append(va)
        B.append(vb)
    return A, B


def _summarise(records, specs, primary, dec: Decoding, corpus_meta, digest,
               preflight_result, structural, started, run_name) -> dict[str, Any]:
    comparable = [r for r in records if r.get("comparable")]
    a_key = primary[0].key if primary else specs[0].key
    b_key = primary[1].key if len(primary) > 1 else (specs[1].key if len(specs) > 1 else None)

    results = []
    if b_key:
        # --- intent / routing --------------------------------------------
        A, B = _pairs(comparable, a_key, b_key, lambda g, _a: bool(g["route_correct"]))
        results.append(mcnemar_exact("routing_correct (all slices)", A, B))
        for sl in ("entity", "intent", "emotion"):
            A, B = _pairs(comparable, a_key, b_key,
                          lambda g, _a: bool(g["route_correct"]), slices=[sl])
            if A:
                results.append(mcnemar_exact(f"routing_correct ({sl})", A, B))

        # --- entities -----------------------------------------------------
        A, B = _pairs(comparable, a_key, b_key,
                      lambda g, _a: g["slot_accuracy"], slices=["entity"])
        if A:
            results.append(wilcoxon("slot_accuracy (entity slice)", A, B))
        A, B = _pairs(comparable, a_key, b_key,
                      lambda g, _a: (g["digit_correct"] / g["digit_expected"])
                      if g["digit_expected"] else None, slices=["entity"])
        if A:
            results.append(wilcoxon("digit_slot_accuracy", A, B))
        A, B = _pairs(comparable, a_key, b_key,
                      lambda g, _a: (g["noun_correct"] / g["noun_expected"])
                      if g["noun_expected"] else None, slices=["entity"])
        if A:
            results.append(wilcoxon("proper_noun_slot_accuracy", A, B))

        # --- write integrity ----------------------------------------------
        A, B = _pairs(comparable, a_key, b_key, lambda g, _a: bool(g["fabricated"]))
        if A:
            results.append(mcnemar_exact("fabricated_value_in_payload", A, B,
                                         higher_is_better=False))

        # --- emotion --------------------------------------------------------
        A, B = _pairs(comparable, a_key, b_key,
                      lambda g, _a: g["acknowledged"], slices=["emotion"])
        if A:
            results.append(mcnemar_exact("acknowledged_affect (emotion slice)", A, B))

        # --- did the reply change at all -------------------------------------
        changed = sum(1 for r in comparable
                      if (r["arms"][a_key]["reply"] or "") != (r["arms"][b_key]["reply"] or ""))

        # --- latency / cost ---------------------------------------------------
        LA = [r["arms"][a_key]["latency_ms"] for r in comparable]
        LB = [r["arms"][b_key]["latency_ms"] for r in comparable]
        results.append(wilcoxon("latency_ms", LA, LB, higher_is_better=False))
    else:
        changed = 0

    per_arm = {}
    for s in specs:
        arms = [r["arms"][s.key] for r in comparable if s.key in r["arms"]]
        if not arms:
            continue
        costs = [a["cost_usd"] for a in arms if a["cost_usd"] is not None]
        lats = [a["latency_ms"] for a in arms if a["latency_ms"] is not None]
        tin = [(a["usage"] or {}).get("input_tokens") or 0 for a in arms]
        tout = [(a["usage"] or {}).get("output_tokens") or 0 for a in arms]
        per_arm[s.key] = {
            "label": s.label, "metadata_mode": s.metadata_mode,
            "exploratory": s.exploratory, "n": len(arms),
            "served_models": sorted({a["served_model"] for a in arms}),
            "route_accuracy": round(sum(a["grade"]["route_correct"] for a in arms) / len(arms), 4),
            "fabrication_rate": round(sum(bool(a["grade"]["fabricated"]) for a in arms) / len(arms), 4),
            "mean_latency_ms": round(sum(lats) / len(lats)) if lats else None,
            "total_cost_usd": round(sum(costs), 6) if costs else None,
            "cost_per_case_usd": round(sum(costs) / len(costs), 8) if costs else None,
            "mean_input_tokens": round(sum(tin) / len(tin), 1) if tin else None,
            "mean_output_tokens": round(sum(tout) / len(tout), 1) if tout else None,
        }

    asr = [r["asr"] for r in records if r.get("asr")]
    d_tot = sum(a["digit_slots"] for a in asr)
    d_ok = sum(a["digit_recoverable"] for a in asr)
    n_tot = sum(a["noun_slots"] for a in asr)
    n_ok = sum(a["noun_recoverable"] for a in asr)

    firsts = [r["perception"]["metadata_first_attempt"] for r in records
              if r.get("perception")]
    return {
        "schema": SCHEMA,
        "run_id": run_name,
        "benchmark": BENCHMARK,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "corpus": {**corpus_meta, "digest_of_run": digest, "n_run": len(records)},
        "decoding": dec.to_dict(),
        "arms": [s.to_dict() for s in specs],
        "primary_comparison": [a_key, b_key],
        "n_total": len(records),
        "n_comparable": len(comparable),
        "n_excluded": len(records) - len(comparable),
        "exclusions": [
            {"case_id": r["case_id"], "reason": r.get("exclusion", "")}
            for r in records if not r.get("comparable")
        ],
        "metadata_head": {
            "preflight": preflight_result,
            "first_attempt_availability": (
                round(sum(1 for f in firsts if f) / len(firsts), 3) if firsts else None),
            "note": (
                "First-attempt availability is what an ordinary caller of "
                "/api/models/transcribe experiences. The suite retries to obtain the "
                "metadata; the retries change nothing about the audio or transcript."),
        },
        "asr_cascade": {
            "digit_slots": d_tot, "digit_recoverable": d_ok,
            "digit_error_rate": round(1 - d_ok / d_tot, 4) if d_tot else None,
            "proper_noun_slots": n_tot, "proper_noun_recoverable": n_ok,
            "proper_noun_error_rate": round(1 - n_ok / n_tot, 4) if n_tot else None,
            "note": ("A property of the ear, measured once, identical for both arms. "
                     "It bounds what any entity consumption could achieve: a value "
                     "the ear never heard cannot be filled correctly downstream."),
        },
        "replies_changed_by_metadata": changed,
        "per_arm": per_arm,
        "paired": summarise(results),
        "structural_audit": structural,
    }


@app.command("report")
def report_cmd(
    run_dir: str,
    archive: bool = typer.Option(True, help="mirror into ~/Downloads/whissle_benchmarks"),
    publish: bool = typer.Option(False, help="POST to the benchmark results store"),
) -> None:
    """Render the research report, archive it, and optionally publish it.

    Pure function of the run directory: re-running regenerates the report from the
    artifacts rather than from anything remembered, so improving the analysis never
    requires re-spending a run.
    """
    from .archive import write_run
    from .render import render_report

    d = Path(run_dir)
    summary = json.loads((d / "SUMMARY.json").read_text())
    md = render_report(summary)
    (d / "REPORT.md").write_text(md, encoding="utf-8")
    typer.echo(f"wrote {d / 'REPORT.md'}")

    if archive:
        try:
            for p in write_run(d, summary, report_md=md, modality="text",
                               metadata_head_in_path=True):
                typer.echo(f"archived {p}")
        except Exception as e:  # an archive failure must not lose the report
            typer.echo(f"archive skipped: {e}", err=True)

    if publish:
        from tau2.reporting import build_report
        from tau2.reporting.publish import publish_reports

        report, report_md = build_report(d, repo_root=Path.cwd())
        results, violations = publish_reports([(report, report_md)])
        for v in violations:
            typer.echo(f"honesty violation: {v}", err=True)
        if violations and not results:
            typer.echo(
                "NOT published — the store's honesty rules rejected the run. A run "
                "that cannot state its sample size, exclusions and judge independence "
                "does not belong on the public page.", err=True)
            raise typer.Exit(3)
        for r in results:
            typer.echo(f"published {r.run_id}: ok={r.ok} status={r.status} {r.detail}")


if __name__ == "__main__":  # pragma: no cover
    app()
