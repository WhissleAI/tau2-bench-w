"""Reporting adapter for the metadata ablation.

The ablation is not a pass-rate benchmark, and forcing it into one would be the
whole problem in miniature: its headline is a **paired difference between two
arms**, which is a different kind of number from "the agent got 64% right". The
adapter therefore publishes the delta as the headline and carries the arm
definitions, the matching guards and the structural audit into the record — a
delta whose arms cannot be inspected is not a result, it is a rumour.

Two run shapes are recognised, because the suite has two layers:

``layer: substrate``  Layer 1 — head accuracy and mutual information against gold,
                      no LLM involved. Headline is the informative-channel count.
``(default)``         Layer 2 — the paired A/B. Headline is the primary metric's
                      paired delta.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from ..model import (Baseline, BaselineSet, Exclusions, Judge, Limitation, Metric,
                     Provenance, Reproduction, RunReport, Sampling, Table)
from .base import BuildContext, artifacts_for, dig, read_json


class MetadataAblationAdapter:
    benchmark = "metadata_ablation"
    benchmark_title = "Metadata ablation — what the cascade layer contributes"

    @classmethod
    def detect(cls, run_dir: Path) -> bool:
        try:
            doc = read_json(run_dir / "SUMMARY.json")
            return bool(doc) and str(dig(doc, "schema", default="")).startswith(
                "tau2.ablation.metadata")
        except Exception:
            return False

    @classmethod
    def build(cls, run_dir: Path, ctx: BuildContext) -> RunReport:
        doc = read_json(run_dir / "SUMMARY.json") or {}
        if doc.get("layer") == "substrate":
            return cls._substrate(run_dir, ctx, doc)
        return cls._ablation(run_dir, ctx, doc)

    # -- shared ----------------------------------------------------------

    @staticmethod
    def _provenance(run_dir: Path, ctx: BuildContext, doc: dict) -> Provenance:
        dec = doc.get("decoding") or {}
        corpus = doc.get("corpus") or {}
        return Provenance(
            agent_id=dec.get("agent_id"),
            base_url=None,
            endpoint="POST /api/bench/agent-turn",
            mode="text",
            repo_commit=ctx.repo_commit(),
            run_dir=str(run_dir),
            captured_at=doc.get("finished_at"),
            dataset=corpus.get("version"),
            dataset_size=corpus.get("n"),
            extra={
                "corpus_digest": corpus.get("digest_of_run") or corpus.get("digest"),
                # The model and its supplier are disclosed in the `model_disclosure`
                # TABLE, not here. Provenance extras are flattened into agent-facing
                # prose by the renderer, where naming a supplier is branding rather
                # than disclosure; a table marked `allow_providers` is the span the
                # honesty rules reserve for exactly this, and it is carried in the
                # published envelope, so the page reads the full configuration either
                # way.
                "model_disclosed_in": "tables[model_disclosure]",
                "thinking_enabled": bool((dec.get("thinking") or {}).get("type")
                                         not in (None, "disabled")),
                "max_tokens": dec.get("max_tokens"),
                "system_sha": dec.get("system_sha"),
                # Recorded explicitly, never inferred: two earlier runs were
                # mislabelled "Voice" while being driven entirely over text.
                "modality": "text",
                "metadata_head_in_path": True,
                "metadata_head_path": (
                    "batch — /api/models/transcribe → whissle_batch_metadata "
                    "(whissle-large). NOT the live voice path, where the head is "
                    "not running at all."),
                "arms": [a.get("key") for a in doc.get("arms") or []],
            },
        )

    @staticmethod
    def _limitations(doc: dict) -> list[Limitation]:
        return [
            Limitation(
                key="synthetic_speech",
                text=("Measured on TTS-synthesised speech, which is affectively flat. "
                      "The emotion channel is being asked to read an affect the audio "
                      "does not carry, so results on it bound what it can do on this "
                      "substrate and are not an estimate of its behaviour on real "
                      "callers."),
                severity="high"),
            Limitation(
                key="injection_fidelity",
                text=("Production injects the metadata block as a separate "
                      "developer-role context message. /api/bench/agent-turn accepts "
                      "only user/assistant roles, so the block is delivered as the "
                      "first line of the same user turn — identical characters, "
                      "identical position, different role tag."),
                severity="medium"),
            Limitation(
                key="predictive_consumers_unmeasured",
                text=("Eager-reply hit and false-fire rate, shadow commit-vs-discard, "
                      "hesitation quality and turn-taking timing are not observable "
                      "through a stateless brain call, and the head that feeds them is "
                      "not running on the live voice path. Not measured — not zero."),
                severity="high"),
            Limitation(
                key="single_turn",
                text=("One caller turn per case, chosen so turn-to-turn variance "
                      "cannot swamp a per-turn signal — at the cost of missing effects "
                      "that only accumulate over a conversation."),
                severity="medium"),
        ]

    # -- layer 2 ---------------------------------------------------------

    @classmethod
    def _ablation(cls, run_dir: Path, ctx: BuildContext, doc: dict) -> RunReport:
        paired = doc.get("paired") or {}
        results = paired.get("results") or []
        primary = next((r for r in results
                        if r.get("metric", "").startswith("routing_correct (all")), None)
        n = doc.get("n_comparable") or 0

        headline = Metric(
            key="metadata_delta_routing",
            label="Routing accuracy: metadata on − metadata off (paired)",
            value=(primary.get("delta") * 100) if primary and primary.get("delta") is not None else None,
            unit="pct", n=n, floor=-100.0, ceiling=100.0, higher_is_better=True,
            ci=(tuple(x * 100 for x in primary["ci"]) if primary and primary.get("ci") else None),
            note=("A paired difference, not a pass rate. Positive means the metadata "
                  "block helped; the CI is a percentile bootstrap over case pairs."),
        )

        secondary = [
            Metric(key=_key(r["metric"]), label=r["metric"],
                   value=(r.get("delta") * 100 if r.get("delta") is not None else None),
                   unit="pct", n=r.get("n_pairs"), floor=-100.0, ceiling=100.0,
                   ci=(tuple(x * 100 for x in r["ci"]) if r.get("ci") else None),
                   higher_is_better=not r["metric"].startswith("fabricated"),
                   note=f"{r.get('test')}; p={r.get('p_value')}; verdict: {r.get('verdict')}")
            for r in results if r is not primary
        ]

        tables = [
            Table(key="paired", title="Paired results, per channel",
                  columns=["metric", "arm A", "arm B", "Δ", "95% CI", "p", "MDE", "verdict"],
                  rows=[[r["metric"], _fmt(r.get("rate_a"), r.get("mean_a")),
                         _fmt(r.get("rate_b"), r.get("mean_b")),
                         _f(r.get("delta")), _ci(r.get("ci")), _f(r.get("p_value")),
                         _f(r.get("mde")), r.get("verdict", "")]
                        for r in results],
                  note=("MDE is the smallest true effect this run could detect at 80% "
                        "power. Where a metric reads 'no measurable effect', the MDE is "
                        "what the run actually rules out."),
                  allow_context=True),
            _model_table(doc),
            Table(key="arms", title="Arms, and how they were verified matched",
                  allow_providers=True,
                  columns=["arm", "metadata_mode", "pre-declared", "description"],
                  rows=[[a["key"], a["metadata_mode"],
                         "exploratory" if a.get("exploratory") else "yes",
                         a.get("description", "")]
                        for a in doc.get("arms") or []],
                  allow_context=True),
            Table(key="cost", title="Cost and latency per arm", allow_providers=True,
                  columns=["arm", "n", "served model", "mean latency", "mean input tok",
                           "cost/case", "total"],
                  rows=[[k, str(p["n"]), ", ".join(p["served_models"]),
                         f"{p['mean_latency_ms']} ms", str(p["mean_input_tokens"]),
                         f"${p['cost_per_case_usd']}", f"${p['total_cost_usd']}"]
                        for k, p in (doc.get("per_arm") or {}).items()],
                  allow_context=True),
            _structural_table(doc),
        ]

        exc = doc.get("exclusions") or []
        exclusions = Exclusions(
            n_total=doc.get("n_total") or 0,
            n_scored=n,
            n_excluded=doc.get("n_excluded") or 0,
            breakdown=_breakdown(exc),
            reason_examples=sorted({e.get("reason", "")[:160] for e in exc})[:5],
            excluded_ids=[e.get("case_id", "") for e in exc][:50],
        )

        return RunReport(
            run_id=f"ablation/{doc.get('run_id')}",
            benchmark=cls.benchmark,
            benchmark_title=cls.benchmark_title,
            series_key="metadata_ablation:text:AB",
            title="What the metadata layer contributes, measured against itself",
            headline=headline,
            secondary_metrics=secondary,
            mode="text",
            date=(doc.get("finished_at") or "")[:10],
            status="complete" if n else "partial",
            partial_reason="" if n else "no comparable cases",
            what_measured=(
                "The paired contribution of the whissle-large metadata block to a "
                "single caller turn, holding the transcript, task, model, prompt, "
                "tools and decoding identical across arms."),
            why_measured=(
                "The cascaded architecture — own ASR, per-utterance emotion/intent "
                "metadata, flow engine, TTS — is the product's central claim, and no "
                "existing benchmark separates it from the LLM. Every number we had "
                "conflated the two."),
            methodology=[
                ("Arms", "A = transcript only; B = transcript preceded by the real "
                         "metadata block in production's own format"),
                ("Pairing", "identical cases, interleaved per case; per-case deltas, "
                            "never arm-mean vs arm-mean"),
                ("Perception", "one TTS→ASR pass per case, shared by both arms, so "
                               "ASR quality cannot differ between them"),
                ("Model pinned", "one model, pinned per request and verified from the "
                                 "response's `model` field rather than the request — see "
                                 "the model-disclosure table for the exact configuration"),
                ("Extended thinking", "disabled, so a variable reasoning budget cannot "
                                      "add a second source of latency and cost variance"),
                ("Guards", "single-variable spec check; arm-prompts-differ assertion; "
                           "served-model match; frozen corpus digest"),
                ("Scoring", "deterministic — no judge model in any primary metric"),
                ("Modality", "text (stateless brain call). The metadata head was in "
                             "the path via the BATCH transcription route, not the live "
                             "voice path, where it is not running."),
            ],
            scoring_rule=(
                "Route accuracy is exact match against a pre-declared gold label. Slot "
                "accuracy is exact match after documented normalisation. A slot is "
                "fabricated when its value is recoverable from neither the transcript "
                "the agent saw nor the gold spoken text — so an ASR error is scored as "
                "a transcription failure, not as invention."),
            tables=[t for t in tables if t],
            exclusions=exclusions,
            judge=Judge(kind="deterministic", independent=None,
                        note="every primary metric is a string comparison against a "
                             "gold value fixed before the run"),
            provenance=cls._provenance(run_dir, ctx, doc),
            sampling=Sampling(
                method="pre-declared frozen corpus, full enumeration",
                n_population=dig(doc, "corpus", "n", default=None),
                n_requested=doc.get("n_total"), n_selected=n,
                seed=dig(doc, "corpus", "seed", default=None),
                strata_keys=["slice"],
                note="No sampling: the corpus is enumerated in full and its digest is "
                     "recorded, so a subset cannot be selected after seeing results."),
            baselines=BaselineSet(
                baselines=[Baseline(
                    name="Arm A — the same system with the metadata block removed",
                    values={"overall": 0.0},
                    source="this run; the paired within-subject control",
                    protocol="identical cases, model, prompt and transcript")],
                comparable=True,
                comparability_note=("The comparator is the system itself without the "
                                    "variable — the only baseline an ablation can "
                                    "honestly have.")),
            limitations=cls._limitations(doc),
            reproduction=Reproduction(
                commands=[
                    "uv run python -m tau2.ablation freeze",
                    "uv run python -m tau2.ablation preflight",
                    f"uv run python -m tau2.ablation run --arms A,B --run-name {doc.get('run_id')}",
                    f"uv run python -m tau2.ablation report results/whissle/ablation/{doc.get('run_id')}",
                ],
                environment={"WHISSLE_BASE": "https://aws-gateway-backend.whissle.ai/bot",
                             "WHISSLE_API_KEY": "wsk_…",
                             "WHISSLE_AGENT_ID": "<neutral bench agent>"},
                notes=["Arm C (entity consumption) is skipped: its consumers ship "
                       "gated off and the batch metadata path requests no entity "
                       "tags, so there is no entity output to consume through this "
                       "seam."]),
            artifacts=artifacts_for(run_dir, [
                ("SUMMARY.json", "the machine-readable result"),
                ("REPORT.md", "the rendered report"),
                ("records.json", "per-case records including both arms' prompts"),
                ("cases", "per-case tau2.health.diagnostics/v1 envelopes"),
            ]),
        )

    # -- layer 1 ---------------------------------------------------------

    @classmethod
    def _substrate(cls, run_dir: Path, ctx: BuildContext, doc: dict) -> RunReport:
        sub = doc.get("substrate") or {}
        channels = sub.get("channels") or {}
        n = doc.get("n_with_substrate") or 0
        emo = channels.get("emotion") or {}

        headline = Metric(
            key="emotion_accuracy_off_majority",
            label="Emotion head accuracy on cases that are not the majority class",
            value=(emo.get("accuracy_off_majority") or 0) * 100,
            unit="pct", n=emo.get("n_off_majority"), floor=0.0, ceiling=100.0,
            note=("Overall accuracy on this corpus is dominated by the neutral base "
                  "rate; this is the number a consumer that acts on the channel "
                  "actually depends on."),
        )
        secondary = []
        for name, c in channels.items():
            secondary.append(Metric(
                key=f"{name}_mi", label=f"{name}: mutual information vs gold",
                value=c.get("mutual_information_bits"), unit="score",
                n=c.get("n"), floor=0.0,
                note=(f"bias floor {c.get('mi_small_sample_bias_bits')} bits at "
                      f"n={c.get('n')}; verdict: {c.get('verdict')}")))
            secondary.append(Metric(
                key=f"{name}_acc", label=f"{name}: top-1 accuracy",
                value=(c.get("accuracy") or 0) * 100, unit="pct", n=c.get("n"),
                floor=0.0, ceiling=100.0,
                note=f"majority-class baseline {100 * (c.get('majority_class_baseline') or 0):.0f}%"))

        asr = doc.get("asr_cascade") or {}
        tables = [
            Table(key="channels", title="Per-channel informativeness",
                  columns=["channel", "top-1 acc", "majority baseline",
                           "acc off majority", "MI (bits)", "MI bias floor",
                           "modal label", "verdict"],
                  rows=[[name, _pct(c.get("accuracy")),
                         _pct(c.get("majority_class_baseline")),
                         f"{_pct(c.get('accuracy_off_majority'))} (n={c.get('n_off_majority')})",
                         _f(c.get("mutual_information_bits")),
                         _f(c.get("mi_small_sample_bias_bits")),
                         f"{c.get('modal_label')} @ {_pct(c.get('modal_share'))}",
                         c.get("verdict", "")]
                        for name, c in channels.items()],
                  allow_context=True),
            _model_table(doc),
            Table(key="ear", title="The ear, measured once — the ceiling on anything downstream",
                  columns=["slot family", "slots", "recoverable from transcript", "error rate"],
                  rows=[["digits (IDs, amounts, phones)", str(asr.get("digit_slots")),
                         str(asr.get("digit_recoverable")), _pct(asr.get("digit_error_rate"))],
                        ["proper nouns (caller names)", str(asr.get("proper_noun_slots")),
                         str(asr.get("proper_noun_recoverable")),
                         _pct(asr.get("proper_noun_error_rate"))]],
                  note="A value the ear never heard cannot be recovered downstream, "
                       "however the metadata is consumed.",
                  allow_context=True),
            _structural_table(doc),
        ]

        return RunReport(
            run_id=f"ablation/{doc.get('run_id')}",
            benchmark=cls.benchmark,
            benchmark_title=cls.benchmark_title,
            series_key="metadata_ablation:substrate",
            title="Is there information in the metadata probability substrate?",
            headline=headline,
            secondary_metrics=secondary,
            mode="text",
            date=(doc.get("finished_at") or "")[:10],
            what_measured=(
                "The whissle-large metadata head's probability substrate — emotion "
                "probs, intent probs and the per-interim probability timeline — "
                "against pre-declared gold labels, upstream of every consumer and "
                "without the LLM."),
            why_measured=(
                "A consumer cannot extract value from a channel that does not "
                "discriminate, however well the consumer is written. This is the "
                "precondition for the whole programme, and it is cheap to answer."),
            methodology=[
                ("Source", "/api/models/transcribe — external transcription plus the "
                           "whissle-large metadata head in parallel"),
                ("Authoritative bar", "top-1 accuracy against gold, beside the "
                                      "majority-class baseline it has to beat"),
                ("Predictive bar", "mutual information in bits, beside its "
                                   "small-sample bias floor"),
                ("Modality", "text/batch audio — the head reached via the BATCH path, "
                             "which is serving; the live voice path runs without it"),
            ],
            scoring_rule=("Head labels are mapped onto the corpus's gold label space "
                          "through a documented mapping; a head label counts as "
                          "correct if it is in the gold label's accepted set."),
            tables=[t for t in tables if t],
            exclusions=Exclusions(n_total=doc.get("n_total") or 0, n_scored=n,
                                  n_excluded=(doc.get("n_total") or 0) - n),
            judge=Judge(kind="deterministic", independent=None,
                        note="labels compared against a frozen gold set"),
            provenance=cls._provenance(run_dir, ctx, doc),
            sampling=Sampling(method="pre-declared frozen corpus, full enumeration",
                              n_population=dig(doc, "corpus", "n", default=None),
                              n_selected=n, seed=dig(doc, "corpus", "seed", default=None),
                              strata_keys=["slice"]),
            baselines=BaselineSet(
                baselines=[Baseline(
                    name="Majority-class predictor (always answer the modal label)",
                    values={"overall": 100 * (emo.get("majority_class_baseline") or 0)},
                    source="this run's own corpus base rate",
                    protocol="the trivial baseline any channel must beat to be useful")],
                comparable=True,
                comparability_note="Same corpus, same labels, by construction."),
            limitations=[
                Limitation(key="synthetic_speech", severity="high", text=(
                    sub.get("caveat", ""))),
                Limitation(key="affect_slice_confound", severity="high", text=(
                    "Affect and utterance type are confounded: neutral cases are "
                    "dominated by the long entity-slice utterances and affective "
                    "cases by the short emotion-slice ones. Any corpus-wide "
                    "separation on a duration-sensitive timeline feature is a length "
                    "artefact, and the within-slice control has only 5 neutral cases. "
                    "The timeline features are reported as NOT CLEANLY MEASURED; the "
                    "fix is length-matched neutral utterances in the emotion slice.")),
                Limitation(key="mi_bias", severity="medium", text=(
                    "Mutual information is positively biased at this sample size; the "
                    "bias floor is printed beside every estimate and a value below it "
                    "is reported as no information rather than a small one.")),
            ],
            reproduction=Reproduction(commands=[
                "uv run python -m tau2.ablation freeze",
                f"uv run python -m tau2.ablation substrate --run-name {doc.get('run_id')}",
            ]),
            artifacts=artifacts_for(run_dir, [
                ("SUMMARY.json", "the machine-readable result"),
                ("REPORT.md", "the rendered report"),
                ("records.json", "per-case perception + head output"),
            ]),
        )


# ---------------------------------------------------------------------------


def _model_table(doc: dict) -> Optional[Table]:
    """Full model disclosure, in the one span where naming a supplier is legitimate.

    The honesty rules forbid vendor names in agent-facing prose — a benchmark page
    should describe the Whissle agent, not advertise whose model is behind it. But an
    ablation is worthless if a reader cannot tell whether both arms ran on the same
    brain, so the configuration is disclosed in full here, as configuration, rather
    than smuggled into the narrative."""
    dec = doc.get("decoding") or {}
    if not dec:
        return None
    rows = [
        ["model", str(dec.get("model"))],
        ["provider (pinned; failover disabled)", str(dec.get("provider"))],
        ["extended thinking", str(dec.get("thinking"))],
        ["max_tokens", str(dec.get("max_tokens"))],
        ["system prompt sha256[:16]", str(dec.get("system_sha"))],
        ["tools bound", str(len(dec.get("tools") or []))],
        ["verified from", "the response's `model` field (PR #664), not the request"],
        ["modality", "text — stateless brain call, no audio transport, no turn-taking"],
        ["metadata head in path", "yes, via the batch transcription route"],
    ]
    return Table(key="model_disclosure", title="Model disclosure and decoding config",
                 columns=["setting", "value"], rows=rows, allow_providers=True,
                 allow_context=True,
                 note=("Identical across every arm. The ablation's validity rests on "
                       "this being the same brain on both sides of the comparison."))


def _structural_table(doc: dict) -> Optional[Table]:
    st = doc.get("structural_audit") or {}
    rows = [[c["channel"], "yes" if c["produced"] else "no",
             "YES" if c["reaches_prompt"] else "no",
             "yes" if c["reaches_flow"] else "no",
             "; ".join(c.get("evidence") or [])]
            for c in st.get("channels") or []]
    if not rows:
        return None
    return Table(
        key="structural", title="Which cascade channels reach the brain at all",
        columns=["channel", "produced", "reaches the prompt", "reaches the flow engine",
                 "evidence (file:line)"],
        rows=rows,
        note=("Not an experimental result. A channel that is produced and never read "
              "contributes zero by construction, and no sample size will show "
              "otherwise. Verified against the backend source."),
        allow_providers=True,
        allow_context=True)


def _breakdown(exc: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in exc:
        k = (e.get("reason") or "unknown").split(":")[0].strip()
        out[k] = out.get(k, 0) + 1
    return out


def _key(metric: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in metric.lower()).strip("_")


def _f(x: Any, nd: int = 4) -> str:
    return "—" if x is None else f"{float(x):.{nd}f}"


def _pct(x: Any) -> str:
    return "—" if x is None else f"{100 * float(x):.1f}%"


def _fmt(rate: Any, mean: Any) -> str:
    if rate is not None:
        return _pct(rate)
    return _f(mean, 2)


def _ci(ci: Any) -> str:
    if not ci:
        return "—"
    return f"[{ci[0]:+.4f}, {ci[1]:+.4f}]"
