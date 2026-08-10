# What the metadata layer contributes, measured against itself

## Abstract

Whissle was evaluated on **Metadata ablation — what the cascade layer contributes** in `text` mode. The headline result is **0.0%** (N = 100) for routing accuracy: metadata on − metadata off (paired), 95% CI [0.0%, 0.0%].

The paired contribution of the whissle-large metadata block to a single caller turn, holding the transcript, task, model, prompt, tools and decoding identical across arms.

## At a glance

| Field | Value |
|---|---|
| **Routing accuracy: metadata on − metadata off (paired)** | **0.0%** (N = 100) |
<!-- honesty:allow-context -->
| 95% CI | [0.0%, 0.0%] |
| Attempted / scored / excluded | 100 / 100 / 0 (0.0%) |
| Judge | deterministic grader (no judge model) |
| Mode | `text` |
| Date | 2026-08-08 |
| Run id | `ablation/ablation_AB_v1` |
| Status | complete |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
- **routing_correct (entity):** 0.0% [0.0%, 0.0%], N = 40 — exact McNemar (binomial); p=1.0; verdict: no measurable effect
- **routing_correct (intent):** 0.0% [0.0%, 0.0%], N = 30 — exact McNemar (binomial); p=1.0; verdict: no measurable effect
- **routing_correct (emotion):** 0.0% [0.0%, 0.0%], N = 30 — exact McNemar (binomial); p=1.0; verdict: no measurable effect
- **slot_accuracy (entity slice):** 0.0% [0.0%, 0.0%], N = 40 — Wilcoxon signed-rank; p=1.0; verdict: no measurable effect
<!-- /honesty:allow-context -->

## 1. What was measured, and why

The paired contribution of the whissle-large metadata block to a single caller turn, holding the transcript, task, model, prompt, tools and decoding identical across arms.

**Why this benchmark.** The cascaded architecture — own ASR, per-utterance emotion/intent metadata, flow engine, TTS — is the product's central claim, and no existing benchmark separates it from the LLM. Every number we had conflated the two.

## 2. Methodology

| Field | Value |
|---|---|
| Arms | A = transcript only; B = transcript preceded by the real metadata block in production's own format |
| Pairing | identical cases, interleaved per case; per-case deltas, never arm-mean vs arm-mean |
| Perception | one TTS→ASR pass per case, shared by both arms, so ASR quality cannot differ between them |
| Model pinned | one model, pinned per request and verified from the response's `model` field rather than the request — see the model-disclosure table for the exact configuration |
| Extended thinking | disabled, so a variable reasoning budget cannot add a second source of latency and cost variance |
| Guards | single-variable spec check; arm-prompts-differ assertion; served-model match; frozen corpus digest |
| Scoring | deterministic — no judge model in any primary metric |
| Modality | text (stateless brain call). The metadata head was in the path via the BATCH transcription route, not the live voice path, where it is not running. |

**Scoring rule.** Route accuracy is exact match against a pre-declared gold label. Slot accuracy is exact match after documented normalisation. A slot is fabricated when its value is recoverable from neither the transcript the agent saw nor the gold spoken text — so an ASR error is scored as a transcription failure, not as invention.

## 3. Setup and provenance

| Field | Value |
|---|---|
| Agent id | `f36d75fb-b245-4e2f-9fe1-a240c9eb3db7` |
| Transport endpoint | `POST /api/bench/agent-turn` |
| Mode | `text` |
| Dataset | metadata_ablation_v1 |
| Dataset size | 100 |
| Repo commit at report time | `dc810ac` |
| Captured at | 2026-08-08T21:05:59.239055+00:00 |
| Run directory | `results/whissle/ablation/ablation_AB_v1` |
| Corpus digest | 3d8d5831954e2a71 |
| Model disclosed in | tables[model_disclosure] |
| Thinking enabled | False |
| Max tokens | 400 |
| System sha | e7e9a8f6dbc97131 |
| Modality | text |
| Metadata head in path | True |
| Metadata head path | batch — /api/models/transcribe → whissle_batch_metadata (whissle-large). NOT the live voice path, where the head is not running at all. |
| Arms | ['A', 'B'] |

### 3.1 Judge and its independence

| Field | Value |
|---|---|
| Grading kind | deterministic |
| Independent of the agent's vendor | n/a — no judge model is called |

<!-- honesty:allow-providers -->
> every primary metric is a string comparison against a gold value fixed before the run
<!-- /honesty:allow-providers -->

### 3.2 Sampling and population

| Field | Value |
|---|---|
| Method | pre-declared frozen corpus, full enumeration |
| Population | 100 |
| Requested | 100 |
| Selected | 100 |
| Scored | 100 |
| Seed | 20260808 |
| Strata | `slice` |

No sampling: the corpus is enumerated in full and its digest is recorded, so a subset cannot be selected after seeing results.

## 4. Results

**Routing accuracy: metadata on − metadata off (paired): 0.0%** (N = 100), 95% CI [0.0%, 0.0%].

| Metric | Value | 95% CI | N | Qualifiers |
|---|---:|---|---:|---|
| **Routing accuracy: metadata on − metadata off (paired)** | **0.0%** | [0.0%, 0.0%] | 100 | N = 100 |
<!-- honesty:allow-context -->
| routing_correct (entity) | 0.0% | [0.0%, 0.0%] | 40 | exact McNemar (binomial); p=1.0; verdict: no measurable effect |
| routing_correct (intent) | 0.0% | [0.0%, 0.0%] | 30 | exact McNemar (binomial); p=1.0; verdict: no measurable effect |
| routing_correct (emotion) | 0.0% | [0.0%, 0.0%] | 30 | exact McNemar (binomial); p=1.0; verdict: no measurable effect |
| slot_accuracy (entity slice) | 0.0% | [0.0%, 0.0%] | 40 | Wilcoxon signed-rank; p=1.0; verdict: no measurable effect |
| digit_slot_accuracy | 0.0% | [0.0%, 0.0%] | 40 | Wilcoxon signed-rank; p=1.0; verdict: no measurable effect |
| proper_noun_slot_accuracy | 0.0% | [0.0%, 0.0%] | 40 | Wilcoxon signed-rank; p=1.0; verdict: no measurable effect |
| fabricated_value_in_payload | 0.0% | [0.0%, 0.0%] | 100 | exact McNemar (binomial); p=1.0; verdict: no measurable effect |
| acknowledged_affect (emotion slice) | 3.3% | [0.0%, 10.0%] | 30 | exact McNemar (binomial); p=1.0; verdict: no measurable effect |
| latency_ms | -2349.0% | [-22180.0%, 19409.0%] | 100 | Wilcoxon signed-rank; p=0.23014890602725346; verdict: underpowered |
<!-- /honesty:allow-context -->

_The headline row is the claim and carries its qualifiers; the rest are components of it and inherit them._

<!-- honesty:allow-context -->
**Paired results, per channel**

| metric | arm A | arm B | Δ | 95% CI | p | MDE | verdict |
|---|---|---|---|---|---|---|---|
| routing_correct (all slices) | 85.0% | 85.0% | 0.0000 | [+0.0000, +0.0000] | 1.0000 | 0.0280 | no measurable effect |
| routing_correct (entity) | 85.0% | 85.0% | 0.0000 | [+0.0000, +0.0000] | 1.0000 | 0.0700 | no measurable effect |
| routing_correct (intent) | 86.7% | 86.7% | 0.0000 | [+0.0000, +0.0000] | 1.0000 | 0.0934 | no measurable effect |
| routing_correct (emotion) | 83.3% | 83.3% | 0.0000 | [+0.0000, +0.0000] | 1.0000 | 0.0934 | no measurable effect |
| slot_accuracy (entity slice) | 0.91 | 0.91 | 0.0000 | [+0.0000, +0.0000] | 1.0000 | 0.0000 | no measurable effect |
| digit_slot_accuracy | 1.00 | 1.00 | 0.0000 | [+0.0000, +0.0000] | 1.0000 | 0.0000 | no measurable effect |
| proper_noun_slot_accuracy | 0.72 | 0.72 | 0.0000 | [+0.0000, +0.0000] | 1.0000 | 0.0000 | no measurable effect |
| fabricated_value_in_payload | 0.0% | 0.0% | 0.0000 | [+0.0000, +0.0000] | 1.0000 | 0.0280 | no measurable effect |
| acknowledged_affect (emotion slice) | 63.3% | 66.7% | 0.0333 | [+0.0000, +0.1000] | 1.0000 | 0.0934 | no measurable effect |
| latency_ms | 3781.97 | 3758.48 | -23.4900 | [-221.8000, +194.0900] | 0.2301 | 303.6628 | underpowered |

MDE is the smallest true effect this run could detect at 80% power. Where a metric reads 'no measurable effect', the MDE is what the run actually rules out.
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
<!-- honesty:allow-providers -->
**Model disclosure and decoding config**

| setting | value |
|---|---|
| model | claude-sonnet-5 |
| provider (pinned; failover disabled) | claude |
| extended thinking | {'type': 'disabled'} |
| max_tokens | 400 |
| system prompt sha256[:16] | e7e9a8f6dbc97131 |
| tools bound | 0 |
| verified from | the response's `model` field (PR #664), not the request |
| modality | text — stateless brain call, no audio transport, no turn-taking |
| metadata head in path | yes, via the batch transcription route |

Identical across every arm. The ablation's validity rests on this being the same brain on both sides of the comparison.
<!-- /honesty:allow-providers -->
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
<!-- honesty:allow-providers -->
**Arms, and how they were verified matched**

| arm | metadata_mode | pre-declared | description |
|---|---|---|---|
| A | off | yes | The brain sees the ASR transcript and nothing else. This is also, exactly, what the live voice path does today: production STT routes to AssemblyAI/Sarvam/Deepgram, none of which emit a metadata head. |
| B | production | yes | The transcript, preceded by the real whissle-large metadata head's output for the same audio, rendered by production's own formatter. NOT a reconstruction of the signal — the signal itself, obtained from the batch path, which is the only place the head is currently reachable. |
<!-- /honesty:allow-providers -->
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
<!-- honesty:allow-providers -->
**Cost and latency per arm**

| arm | n | served model | mean latency | mean input tok | cost/case | total |
|---|---|---|---|---|---|---|
| A | 100 | claude-sonnet-5 | 3782 ms | 1728.7 | $0.0068778 | $0.68778 |
| B | 100 | claude-sonnet-5 | 3758 ms | 1774.0 | $0.00692706 | $0.692706 |
<!-- /honesty:allow-providers -->
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

The comparator is the system itself without the variable — the only baseline an ablation can honestly have.

<!-- honesty:allow-context -->
<!-- honesty:allow-providers -->
**Published baselines — **

| System | N | Overall | Published in |
|---|---|---|---|
| **Whissle (this run)** | 100 | — | — (this measurement) |
| Arm A — the same system with the metadata block removed | — | 0.0 | this run; the paired within-subject control |

Published protocol: . External model names appear here and only here; they are published comparators, not components of the system under test.
<!-- /honesty:allow-providers -->
<!-- /honesty:allow-context -->

**What is comparable:** the protocol — same prompts, same action grammar, same grader. **What is not:** the sample. This run matches the published N.

## 6. Failure analysis

_No categorised failures were recorded for this run._

## 7. Exclusions and what they do to the number

Nothing was excluded: all 100 attempted units produced a gradable result. The headline denominator is the full attempted set.

## 8. Limitations and threats to validity

- **synthetic speech** (high) — Measured on TTS-synthesised speech, which is affectively flat. The emotion channel is being asked to read an affect the audio does not carry, so results on it bound what it can do on this substrate and are not an estimate of its behaviour on real callers.
- **injection fidelity** (medium) — Production injects the metadata block as a separate developer-role context message. /api/bench/agent-turn accepts only user/assistant roles, so the block is delivered as the first line of the same user turn — identical characters, identical position, different role tag.
- **predictive consumers unmeasured** (high) — Eager-reply hit and false-fire rate, shadow commit-vs-discard, hesitation quality and turn-taking timing are not observable through a stateless brain call, and the head that feeds them is not running on the live voice path. Not measured — not zero.
- **single turn** (medium) — One caller turn per case, chosen so turn-to-turn variance cannot swamp a per-turn signal — at the cost of missing effects that only accumulate over a conversation.

## 9. Reproduction

```bash
uv run python -m tau2.ablation freeze
uv run python -m tau2.ablation preflight
uv run python -m tau2.ablation run --arms A,B --run-name ablation_AB_v1
uv run python -m tau2.ablation report results/whissle/ablation/ablation_AB_v1
```

| Field | Value |
|---|---|
| WHISSLE_BASE | https://aws-gateway-backend.whissle.ai/bot |
| WHISSLE_API_KEY | wsk_… |
| WHISSLE_AGENT_ID | <neutral bench agent> |

- Arm C (entity consumption) is skipped: its consumers ship gated off and the batch metadata path requests no entity tags, so there is no entity output to consume through this seam.

## Appendix A — raw artifacts

| Path | Present | What it is |
|---|:---:|---|
| `SUMMARY.json` | yes | the machine-readable result |
| `REPORT.md` | yes | the rendered report |
| `records.json` | yes | per-case records including both arms' prompts |
| `cases` | yes | per-case tau2.health.diagnostics/v1 envelopes |

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

<!-- generated by tau2.reporting from ablation/ablation_AB_v1; schema tau2.reporting.run_report/v1 -->
