# Metadata ablation — Layer 1: is there information in the substrate?

*Run `substrate_v1` · 2026-08-08T21:01:06.787849+00:00 · N = 100 · schema `tau2.ablation.metadata/v1`*

## Abstract

Of the two probability channels that reach the model, 2 carry information about the state they are supposed to describe (emotion, intent) at n = 100.

This layer measures the whissle-large metadata head's **probability substrate** — emotion probs, intent probs and the per-interim `metadata_probs_timeline` — against gold labels, upstream of every consumer and without involving the LLM. It answers the precondition question for the whole programme: a consumer cannot extract value from a channel that does not discriminate, however well the consumer is written.

## Method

Each case is one caller utterance, synthesised through `/api/models/tts` and heard once through `/api/models/transcribe`, which runs the external transcription engine and the whissle-large metadata head in parallel. Head output is compared against the corpus's declared gold labels.

- Corpus: `metadata_ablation_v1`, digest `3d8d5831954e2a71`, slices {'entity': 40, 'intent': 30, 'emotion': 30}

- Metadata head first-attempt availability: 99.0%

- Cases with substrate: 100 / 100


## Results — per channel

| channel | top-1 acc | majority-class baseline | acc off majority class | MI (bits) | MI bias floor | modal label | verdict |
|---|---|---|---|---|---|---|---|
| emotion | 78.0% | 75.0% | 40.0% (n=25) | 0.401 | 0.173 | `neutral` @ 79.0% | informative |
| intent | 59.0% | 18.0% | 50.0% (n=82) | 0.643 | 0.404 | `inform` @ 56.0% | informative |

Read the two accuracy columns together. Top-1 accuracy on a corpus with a dominant class is largely a restatement of the base rate; the column that decides whether a consumer can *act* on a channel is its accuracy on the cases where the answer is not the majority label. Mutual information answers a different question again — whether the channel is worth **gating** on, which does not require it to be authoritative.

### emotion

MI = 0.4014 bits, above the 0.1731-bit bias floor. Informative enough to gate on even where top-1 accuracy is low — a predictive consumer does not need an authoritative channel.

Labels emitted: `{'neutral': 79, 'surprise': 4, 'sad': 8, 'angry': 4, 'disgust': 2, 'fear': 1, 'happy': 2}`  ·  marginal entropy 1.224 bits  ·  mean reported confidence 80.8%

| gold | head output |
|---|---|
| angry | `neutral`×4, `disgust`×2, `angry`×2, `sad`×2 |
| frustrated | `neutral`×3, `sad`×1, `surprise`×1 |
| happy | `neutral`×3, `happy`×2 |
| neutral | `neutral`×68, `surprise`×3, `sad`×2, `angry`×2 |
| sad | `sad`×3, `neutral`×1, `fear`×1 |

### intent

MI = 0.6432 bits, above the 0.404-bit bias floor. Informative enough to gate on even where top-1 accuracy is low — a predictive consumer does not need an authoritative channel.

Labels emitted: `{'inform': 56, 'request': 19, 'question': 14, 'greeting': 6, 'greet': 1, 'thank': 1, 'exclaim': 1, 'propose': 1, 'order': 1}`  ·  marginal entropy 1.897 bits  ·  mean reported confidence 81.5%

| gold | head output |
|---|---|
| billing_question | `inform`×12, `question`×6 |
| book_appointment | `inform`×6, `greeting`×2, `question`×2, `request`×2, `propose`×1, `order`×1 |
| cancel_appointment | `request`×5, `inform`×5 |
| escalate_to_human | `inform`×15, `question`×2 |
| other | `question`×3, `inform`×2, `thank`×1, `exclaim`×1 |
| prescription_refill | `request`×5, `inform`×4, `question`×1 |
| reschedule_appointment | `inform`×8, `request`×1 |
| update_contact_details | `request`×6, `greeting`×4, `inform`×4, `greet`×1 |

## Results — the probability timeline

Populated on **100 / 100** cases, mean 8.39 snapshots per utterance (range 2–18).

metadata_probs_timeline IS populated on the batch path — the per-interim snapshots hesitation is derived from exist here, which is what makes this layer measurable at all. On the live voice path the same timeline is empty, because the head that fills it is not running there.

| feature | mean | sd | range | d (corpus-wide) | d (within emotion slice) | note |
|---|---|---|---|---|---|---|
| `hesitation_emotion_entropy` | 0.4183 | 0.2491 | [0.0, 0.982] | 0.1595 | 0.8416 (n=[5, 25]) |  |
| `hesitation_emotion_flips` | 2.05 | 1.6598 | [0.0, 7.0] | 0.4142 | 1.0502 (n=[5, 25]) |  |
| `hesitation_emotion_instability` | 0.2897 | 0.0973 | [0.113, 0.547] | 0.6004 | 0.4758 (n=[5, 25]) |  |
| `hesitation_emotion_snapshots` | 8.39 | 4.2281 | [2.0, 18.0] | -0.5932 | 1.6794 (n=[5, 25]) | duration-sensitive — corpus-wide d is a length artefact |
| `hesitation_intent_entropy` | 0.3277 | 0.3025 | [0.0, 0.974] | 0.0149 | -1.4773 (n=[5, 25]) |  |
| `hesitation_span_s` | 3.3493 | 1.8124 | [0.579, 7.556] | -0.5872 | 1.6614 (n=[5, 25]) | duration-sensitive — corpus-wide d is a length artefact |

## Threats to validity

- Measured on TTS-synthesised speech. Synthetic audio is affectively flat, so the emotion channel is being asked to read an affect the audio does not carry: a degenerate result here bounds what the channel can do on THIS substrate and is NOT an estimate of its accuracy on real callers. The intent, age/gender and timeline channels do not depend on affective prosody in the same way and are less compromised by it.

- **Affect and utterance type are confounded in this corpus.** The neutral cases are dominated by the entity slice (long, information-dense utterances) and the affective cases by the emotion slice (short ones). Any corpus-wide separation on a duration-sensitive timeline feature is therefore a length artefact, and the within-slice control that would remove it has only 5 neutral cases to work with. The timeline features are reported as **not cleanly measured**; the fix is to add length-matched neutral utterances to the emotion slice, which is a corpus change, not an analysis change.

- Mutual information is positively biased at this sample size; the bias floor is printed beside every estimate and a value below it is reported as no information rather than a small one.

## The ear, measured once

A property of the cascade rather than of any arm, and the ceiling on anything downstream: a value the ear never heard cannot be recovered by a consumer of the ear's output.

- Digit slots (IDs, amounts, phone numbers): **20.0% not recoverable** from the transcript (10 of 50).

- Proper-noun slots (caller names): **27.5% not recoverable** (11 of 40).


## Structural audit — which channels reach the brain at all

Some of this question is not experimental. A channel that is produced and then never read contributes zero by construction, and no sample size will show otherwise. Verified against the backend source, with line numbers.

Of 8 cascade channels, 2 (emotion, intent) reach the model at all; the rest are produced and discarded. On the live voice path none of them reach it, because the head that produces them is not running there.

| channel | produced | reaches the prompt | reaches the flow engine | evidence |
|---|---|---|---|---|
| emotion | yes | **yes** | no | `pipecat-bot/bot/services_build.py:227`, `pipecat-bot/services/metadata_processor.py:28` |
| intent | yes | **yes** | no | `pipecat-bot/bot/services_build.py:227`, `pipecat-bot/services/flow/expr.py:125` |
| entity | yes | no | no | `pipecat-bot/services/whissle_stt.py:102`, `pipecat-bot/services/flow/collector.py:258` |
| age | yes | no | no | `pipecat-bot/bot/services_build.py:227` |
| gender | yes | no | no | `pipecat-bot/bot/services_build.py:227` |
| hesitation | yes | no | no | `pipecat-bot/services/hesitation.py:130` |
| shadow | yes | no | no | `pipecat-bot/services/shadow_llm.py:16` |
| speculation | yes | no | no | `pipecat-bot/services/speculative_tools.py:129` |

### The live voice path

On the live voice path the metadata head is not running. Production STT is routed to AssemblyAI (English/Hinglish), Sarvam (Indian languages) or Deepgram; none of them emit a metadata head. The whissle-large sidecar that would supply one is gated behind WHISSLE_STT_TRANSPORT=grpc plus WHISSLE_GRPC_TARGET. Consequence: for live calls, arm B and arm A are the same prompt, and the metadata layer's contribution to production today is structurally zero — independent of any experiment.

Evidence: `pipecat-bot/bot/services_build.py:706`, `pipecat-bot/services/whissle_metadata_sidecar.py:89` (verified: True)

The BATCH path is different and is why this ablation can run at all: /api/models/transcribe calls whissle_batch_metadata in parallel with the external transcription, and that head IS serving in production. It is fail-open, so a timeout simply omits the `metadata` key.

## Reproduction

```bash
uv run python -m tau2.ablation freeze
uv run python -m tau2.ablation substrate --run-name <name>
uv run python -m tau2.ablation report results/whissle/ablation/<name>
```

