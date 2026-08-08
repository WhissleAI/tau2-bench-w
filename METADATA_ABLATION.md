# The metadata ablation

**What does Whissle's cascade layer actually contribute?**

The cascaded architecture — our own ASR, per-utterance emotion/intent metadata, a
flow engine, TTS — is the product's central claim, and until this suite nothing
measured it. Every benchmark number we had conflated that layer with the LLM
behind it. This suite separates them.

Two commands, two layers, one report each:

```bash
uv run python -m tau2.ablation substrate --run-name <name>   # layer 1, no LLM
uv run python -m tau2.ablation run --arms A,B --run-name <name>  # layer 2, paired
uv run python -m tau2.ablation report results/whissle/ablation/<name>
```

---

## 1. What is under test

**The probability substrate, not entity strings.** We do not transcribe with
whissle-large — Deepgram, Sarvam and AssemblyAI do that. The metadata head exists
to emit something none of them emit: emotion probabilities, intent probabilities,
age/gender, and the per-interim `metadata_probs_timeline` that hesitation and the
other predictive consumers are derived from. Entities are a by-product of the same
decode. The substrate has **no third-party substitute**, which is the whole reason
it is worth measuring rather than assuming.

## 2. The two layers, and why there are two

| Layer | Question | Needs the LLM? |
|---|---|---|
| 1 — substrate | Does the head's output discriminate between the states it claims to describe? | no |
| 2 — downstream | Does putting that output in front of the brain change what the brain does, and does the change help? | yes |

Layer 1 is the precondition for layer 2 and for every consumer that will ever be
built on the head. A consumer cannot extract value from a channel that does not
discriminate, however well the consumer is written — and layer 1 costs a few
dollars of TTS and ASR, while layer 2 costs LLM calls against a balance shared
with production. Answer the cheap question first.

## 3. Arms

| arm | metadata_mode | what it is |
|---|---|---|
| **A** | `off` | The brain sees the ASR transcript and nothing else. |
| **B** | `production` | The transcript, preceded by the **real** metadata head output for the same audio, rendered by production's own formatter. |
| **C** | — | Metadata + entity consumption. **Not run** — see §7. |
| B-oracle | `oracle` | *Exploratory.* Gold affect at 100% confidence — the ceiling a perfect head could reach. |
| B-noisy | `noisy` | *Exploratory.* Gold affect corrupted on a fixed seeded schedule to a stated accuracy — measures the **sign** of a noisy channel rather than assuming it helps. |

Arm B's block is not an imitation. `arms.speech_analysis_block` reimplements
`bot/services_build.py::_MetadataContextMixin._format_field` character for
character — same field order, same prefix stripping, same 5% probability floor,
same top-4 truncation, same `[User speech analysis: …]` wrapper — and feeds it the
head's own output.

## 4. Why the ablation is by construction, not by suppression

There is no switch to turn metadata off. `/api/bench/agent-turn` never injects
cascade metadata at all, and no env var or request field suppresses the block on
the voice path either. So arm A is the untouched endpoint and arm B *adds* the
real block, rather than arm B being production and arm A removing something.

That framing matters for reading the results, because of what the structural
audit found (§6): **on the live voice path the head is not running**, so
production today behaves like arm A. Arm B is therefore not "production as it is"
— it is "production with the layer we say we have, actually switched on."

## 5. How the arms are kept single-variable

An ablation whose arms differ in more than the variable is worse than none: it
produces a confident number about nothing. Five guards, all of which fail the run
or drop the case rather than warn:

1. **One perception pass per case.** TTS → `/api/models/transcribe` runs once; both
   arms consume the same transcript. ASR quality cannot differ between arms.
2. **Single-variable spec check.** Arm specifications are compared field by field
   and must differ only in `metadata_mode`.
3. **Arms-differ assertion.** Arm B's prompt must not be character-identical to arm
   A's. This is the guard the whole design exists for — an empty block silently
   turns arm B into arm A, the run completes, and every delta is a fake zero that
   reads as "metadata does not help" and means "there was no metadata."
4. **Served-model match.** The model is read off the *response* (PR #664), not the
   request. A case served by a different model is dropped, never averaged in.
5. **Frozen, digested corpus.** The task list is declared before the run and its
   digest recorded; a hand-edited corpus is a load error, not a warning.

Arms are also **interleaved per case** — A then B for case 1, then case 2 — so
backend load and provider drift are common-mode rather than confounded with the arm.

## 6. Statistics

Everything is **paired**: per-case B − A, never arm-mean against arm-mean. At
n = 100 that is not a stylistic preference. An unpaired comparison of two ~70%
rates cannot resolve anything below roughly ±12 points; the paired test only has
to resolve the cases where the arms actually disagreed.

- **Exact McNemar** (binomial on the discordant pairs) for binary outcomes. Exact,
  not chi-square, because the approximation is wrong exactly when the discordant
  count is small — which is the regime an ablation lives in.
- **Wilcoxon signed-rank** for per-case scores and latency.
- **Percentile bootstrap** on the paired mean difference, resampling *pairs*.
- **MDE** reported for every metric. In a paired binary design power comes from the
  discordant pairs, not from N: a run with n = 100 and four disagreements has the
  power of a study of four, and the report says so.

Every result is classified as **gain**, **regression**, **no measurable effect**,
or **underpowered** — never a shrug. "No effect" and "could not have seen this
effect" are different findings and are rendered differently.

## 7. What this suite deliberately does not measure

- **Arm C — entity consumption.** The consumers ship gated off, and the batch
  metadata path this suite reaches requests no entity tags at all
  (`metadata_tags=None`), so there is no entity output to consume through this seam
  even in principle. Recorded as *not measured*, never as a zero.
- **The predictive consumers** — eager-reply hit and false-fire rate, shadow draft
  commit-versus-discard, hesitation prediction quality, turn-taking timing,
  barge-in and false-cut rates. These change *when* the agent acts rather than
  *what it says*, they live in the live voice pipeline, and
  `/api/bench/agent-turn` is a stateless brain call with no turn-taking. On the
  live path the head that would feed them is not running. Measuring them needs the
  head enabled on a voice path, which is a capacity decision about the shared T4 —
  not a harness change.
- **What each consumer does with an empty timeline.** Production has been running
  with an empty `metadata_probs_timeline`, so arm A is not necessarily a clean
  no-signal baseline: it is whatever each consumer's fallback does, and a
  well-tuned prior could make it a much stronger baseline than "no signal". That is
  source-reading plus a live-path run.

## 8. Cost, and why it is stated before the run

Benchmark spend and production spend share one Anthropic balance —
`services/anthropic_http.py` reads the production key, and there is no separate
benchmark key. A model sweep drained that balance earlier and took production
down with it. So the runner is deliberately cheap by design: one turn per case, no
tools, no KB, ~1,750 input tokens per call.

**100 cases × 2 arms ≈ $1.40.** Estimate before you run, check
`/api/llm-health` before and between arms, and stop rather than retry into a dead
balance.

## 9. The corpus

`data/ablation/metadata_ablation_v1.json` — 100 cases, frozen and digested,
generated deterministically by `corpus.build_corpus`.

| slice | n | what it isolates |
|---|---|---|
| entity | 40 | names, dates, IDs, amounts, phone numbers — where the mechanism predicts gains and where a general WER would hide them |
| intent | 30 | utterances whose correct routing is unambiguous to a human but never named on the surface |
| emotion | 30 | affect-laden utterances with an intended affect and a required acknowledgement |

Regenerating is a **new corpus version**, not an edit to this one. That is the only
way "no cherry-picked task subsets" can be a property of the artifact rather than a
promise in prose.

## 10. Artifacts

```
results/whissle/ablation/<run>/
    SUMMARY.json     machine-readable result
    REPORT.md        research-paper structure, regenerable
    records.json     per-case records, both arms' prompts included
    cases/           per-case tau2.health.diagnostics/v1 envelopes
```

Published to the DB-backed store with `python -m tau2.reporting publish
results/whissle/ablation/<run>`, and mirrored into the shared archive at
`~/Downloads/whissle_benchmarks/metadata_ablation/<timestamp>_<arm>/`.

Every record states **modality** (`text`) and **whether the metadata head was in
the path** (and by which route) explicitly rather than by inference — two earlier
runs elsewhere were mislabelled "Voice" while being driven entirely over text, and
that came from artifacts that did not say.
