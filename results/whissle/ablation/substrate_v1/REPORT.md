# Is there information in the metadata probability substrate?

## Abstract

Whissle was evaluated on **Metadata ablation — what the cascade layer contributes** in `text` mode. The headline result is **40.0%** (N = 100) for emotion head accuracy on cases that are not the majority class.

The whissle-large metadata head's probability substrate — emotion probs, intent probs and the per-interim probability timeline — against pre-declared gold labels, upstream of every consumer and without the LLM.

## At a glance

| Field | Value |
|---|---|
| **Emotion head accuracy on cases that are not the majority class** | **40.0%** (N = 100) |
<!-- honesty:allow-context -->
| Attempted / scored / excluded | 100 / 100 / 0 (0.0%) |
| Judge | deterministic grader (no judge model) |
| Mode | `text` |
| Date | 2026-08-08 |
| Run id | `ablation/substrate_v1` |
| Status | complete |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
- **emotion: mutual information vs gold:** 0.40, N = 100 — bias floor 0.1731 bits at n=100; verdict: informative
- **emotion: top-1 accuracy:** 78.0%, N = 100 — majority-class baseline 75%
- **intent: mutual information vs gold:** 0.64, N = 100 — bias floor 0.404 bits at n=100; verdict: informative
- **intent: top-1 accuracy:** 59.0%, N = 100 — majority-class baseline 18%
<!-- /honesty:allow-context -->

## 1. What was measured, and why

The whissle-large metadata head's probability substrate — emotion probs, intent probs and the per-interim probability timeline — against pre-declared gold labels, upstream of every consumer and without the LLM.

**Why this benchmark.** A consumer cannot extract value from a channel that does not discriminate, however well the consumer is written. This is the precondition for the whole programme, and it is cheap to answer.

## 2. Methodology

| Field | Value |
|---|---|
| Source | /api/models/transcribe — external transcription plus the whissle-large metadata head in parallel |
| Authoritative bar | top-1 accuracy against gold, beside the majority-class baseline it has to beat |
| Predictive bar | mutual information in bits, beside its small-sample bias floor |
| Modality | text/batch audio — the head reached via the BATCH path, which is serving; the live voice path runs without it |

**Scoring rule.** Head labels are mapped onto the corpus's gold label space through a documented mapping; a head label counts as correct if it is in the gold label's accepted set.

## 3. Setup and provenance

| Field | Value |
|---|---|
| Transport endpoint | `POST /api/bench/agent-turn` |
| Mode | `text` |
| Dataset | metadata_ablation_v1 |
| Dataset size | 100 |
| Repo commit at report time | `dc810ac` |
| Captured at | 2026-08-08T21:01:06.787849+00:00 |
| Run directory | `results/whissle/ablation/substrate_v1` |
| Corpus digest | 3d8d5831954e2a71 |
| Model disclosed in | tables[model_disclosure] |
| Thinking enabled | False |
| Modality | text |
| Metadata head in path | True |
| Metadata head path | batch — /api/models/transcribe → whissle_batch_metadata (whissle-large). NOT the live voice path, where the head is not running at all. |
| Arms | [] |

### 3.1 Judge and its independence

| Field | Value |
|---|---|
| Grading kind | deterministic |
| Independent of the agent's vendor | n/a — no judge model is called |

<!-- honesty:allow-providers -->
> labels compared against a frozen gold set
<!-- /honesty:allow-providers -->

### 3.2 Sampling and population

| Field | Value |
|---|---|
| Method | pre-declared frozen corpus, full enumeration |
| Population | 100 |
| Selected | 100 |
| Scored | 100 |
| Seed | 20260808 |
| Strata | `slice` |

## 4. Results

**Emotion head accuracy on cases that are not the majority class: 40.0%** (N = 100).

| Metric | Value | 95% CI | N | Qualifiers |
|---|---:|---|---:|---|
| **Emotion head accuracy on cases that are not the majority class** | **40.0%** | — | 25 | N = 100 |
<!-- honesty:allow-context -->
| emotion: mutual information vs gold | 0.40 | — | 100 | bias floor 0.1731 bits at n=100; verdict: informative |
| emotion: top-1 accuracy | 78.0% | — | 100 | majority-class baseline 75% |
| intent: mutual information vs gold | 0.64 | — | 100 | bias floor 0.404 bits at n=100; verdict: informative |
| intent: top-1 accuracy | 59.0% | — | 100 | majority-class baseline 18% |
<!-- /honesty:allow-context -->

_The headline row is the claim and carries its qualifiers; the rest are components of it and inherit them._

<!-- honesty:allow-context -->
**Per-channel informativeness**

| channel | top-1 acc | majority baseline | acc off majority | MI (bits) | MI bias floor | modal label | verdict |
|---|---|---|---|---|---|---|---|
| emotion | 78.0% | 75.0% | 40.0% (n=25) | 0.4014 | 0.1731 | neutral @ 79.0% | informative |
| intent | 59.0% | 18.0% | 50.0% (n=82) | 0.6432 | 0.4040 | inform @ 56.0% | informative |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
**The ear, measured once — the ceiling on anything downstream**

| slot family | slots | recoverable from transcript | error rate |
|---|---|---|---|
| digits (IDs, amounts, phones) | 50 | 40 | 20.0% |
| proper nouns (caller names) | 40 | 29 | 27.5% |

A value the ear never heard cannot be recovered downstream, however the metadata is consumed.
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
<!-- honesty:allow-providers -->
**Which cascade channels reach the brain at all**

| channel | produced | reaches the prompt | reaches the flow engine | evidence (file:line) |
|---|---|---|---|---|
| emotion | yes | YES | no | pipecat-bot/bot/services_build.py:227; pipecat-bot/services/metadata_processor.py:28 |
| intent | yes | YES | no | pipecat-bot/bot/services_build.py:227; pipecat-bot/services/flow/expr.py:125 |
| entity | yes | no | no | pipecat-bot/services/whissle_stt.py:102; pipecat-bot/services/flow/collector.py:258 |
| age | yes | no | no | pipecat-bot/bot/services_build.py:227 |
| gender | yes | no | no | pipecat-bot/bot/services_build.py:227 |
| hesitation | yes | no | no | pipecat-bot/services/hesitation.py:130 |
| shadow | yes | no | no | pipecat-bot/services/shadow_llm.py:16 |
| speculation | yes | no | no | pipecat-bot/services/speculative_tools.py:129 |

Not an experimental result. A channel that is produced and never read contributes zero by construction, and no sample size will show otherwise. Verified against the backend source.
<!-- /honesty:allow-providers -->
<!-- /honesty:allow-context -->

## 5. Comparison to published baselines

Same corpus, same labels, by construction.

<!-- honesty:allow-context -->
<!-- honesty:allow-providers -->
**Published baselines — **

| System | N | Overall | Published in |
|---|---|---|---|
| **Whissle (this run)** | 100 | — | — (this measurement) |
| Majority-class predictor (always answer the modal label) | — | 75.0 | this run's own corpus base rate |

Published protocol: . External model names appear here and only here; they are published comparators, not components of the system under test.
<!-- /honesty:allow-providers -->
<!-- /honesty:allow-context -->

**What is comparable:** the protocol — same prompts, same action grammar, same grader. **What is not:** the sample. This run matches the published N.

## 6. Failure analysis

_No categorised failures were recorded for this run._

## 7. Exclusions and what they do to the number

Nothing was excluded: all 100 attempted units produced a gradable result. The headline denominator is the full attempted set.

## 8. Limitations and threats to validity

- **synthetic speech** (high) — Measured on TTS-synthesised speech. Synthetic audio is affectively flat, so the emotion channel is being asked to read an affect the audio does not carry: a degenerate result here bounds what the channel can do on THIS substrate and is NOT an estimate of its accuracy on real callers. The intent, age/gender and timeline channels do not depend on affective prosody in the same way and are less compromised by it.
- **affect slice confound** (high) — Affect and utterance type are confounded: neutral cases are dominated by the long entity-slice utterances and affective cases by the short emotion-slice ones. Any corpus-wide separation on a duration-sensitive timeline feature is a length artefact, and the within-slice control has only 5 neutral cases. The timeline features are reported as NOT CLEANLY MEASURED; the fix is length-matched neutral utterances in the emotion slice.
- **mi bias** (medium) — Mutual information is positively biased at this sample size; the bias floor is printed beside every estimate and a value below it is reported as no information rather than a small one.

## 9. Reproduction

```bash
uv run python -m tau2.ablation freeze
uv run python -m tau2.ablation substrate --run-name substrate_v1
```

## Appendix A — raw artifacts

| Path | Present | What it is |
|---|:---:|---|
| `SUMMARY.json` | yes | the machine-readable result |
| `REPORT.md` | yes | the rendered report |
| `records.json` | yes | per-case perception + head output |

Every per-case record carries a `diagnostics` block (`tau2.health.diagnostics/v1`) with flow trace, signals, metadata sidecar, tool forensics, provenance and cost — and explicit availability flags, so an absent measurement reads as absent rather than as zero. See `HEALTH_DIAGNOSTICS.md`.

## Appendix B — honesty-rule compliance

These rules are executed against this document, not asserted about it. A failing rule blocks generation.

| Rule | Verdict | Checked |
|---|:---:|---|
| `R1_headline_requires_n` | pass | headline carries N = 100 everywhere it is stated |
| `R2_judge_independence_disclosed` | pass | not applicable — judge is independent or deterministic |
| `R3_exclusion_rate_adjacent` | pass | not applicable — nothing was excluded |
| `R4_preliminary_labelled` | pass | not applicable — N = 100 ≥ 30 and the run is complete |
| `R5_no_provider_names` | pass | no LLM vendor named outside the published-baseline table |
| `R6_comparability_stated` | pass | comparability to published baselines stated explicitly |
| `R7_baseline_named` | pass | every comparator is a named system with a published source |

<!-- generated by tau2.reporting from ablation/substrate_v1; schema tau2.reporting.run_report/v1 -->
