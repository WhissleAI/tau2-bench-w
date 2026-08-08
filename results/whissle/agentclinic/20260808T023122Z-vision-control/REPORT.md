# AgentClinic — Whissle as the doctor (NEJM)

> **PRELIMINARY** — N = 3 is below the 30-unit threshold for a settled number. Treat every figure below as directional.

## Abstract

Whissle was evaluated on **AgentClinic** in `text` mode. The headline result is **66.7%** (N = 3 · PRELIMINARY) for diagnostic accuracy, 95% CI [20.8%, 93.9%].

Whether an agent can run a diagnostic consultation: take a patient's presentation, ask the questions that discriminate between the candidate diagnoses, order the tests it needs, and commit to an answer within a bounded number of inferences. The agent plays the doctor; a simulated patient and a simulated measurement device play the other side.

## At a glance

| Field | Value |
|---|---|
| **Diagnostic accuracy** | **66.7%** (N = 3 · PRELIMINARY) |
<!-- honesty:allow-context -->
| 95% CI | [20.8%, 93.9%] |
| Attempted / scored / excluded | 3 / 3 / 0 (0.0%) |
| Judge | unknown |
| Mode | `text` |
| Date | 2026-08-08 |
| Run id | `agentclinic/20260808T023122Z-vision-control` |
| Status | **PRELIMINARY** |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
- **Accuracy when a diagnosis was actually given:** 66.7% [20.8%, 93.9%], N = 3 — refusals and non-commits removed from the denominator
- **Commit rate:** 100.0% [43.8%, 100.0%], N = 3 — how often the agent named a diagnosis at all
<!-- /honesty:allow-context -->

## 1. What was measured, and why

Whether an agent can run a diagnostic consultation: take a patient's presentation, ask the questions that discriminate between the candidate diagnoses, order the tests it needs, and commit to an answer within a bounded number of inferences. The agent plays the doctor; a simulated patient and a simulated measurement device play the other side.

**Why this benchmark.** A single-turn medical QA score says whether a model knows the answer. This says whether it can *get* to the answer through a conversation where the information arrives only if it asks — which is the shape of every real intake.

## 2. Methodology

| Field | Value |
|---|---|
| Agent under test | the deployed Whissle agent brain, unmodified |
| Mode | `text` transport, `markers` action protocol, vision `off` |
| Endpoint | `POST /api/bench/agent-turn` |
| Prompt handling | `override` — the benchmark's doctor prompt is used verbatim, which is what keeps the number in the same units as the published table |
| Turn limit | 20 inferences per case; a case that has not committed by then is scored `no_commit`, and `no_commit` counts as incorrect |
| Tools bound | the benchmark's own action markers (ask / order test / commit diagnosis), parsed by the harness |
| Judge | a moderator model decides whether the committed free-text diagnosis matches the reference, and a decline-judge separates a refusal from a wrong answer |
| Scoring rule | accuracy = correct / presented, upstream's formula unmodified |

**Scoring rule.** accuracy = total_correct / total_presents (upstream formula)

## 3. Setup and provenance

| Field | Value |
|---|---|
| Agent id | `135a8daf-436d-450e-98c2-3d160561f293` |
| Base URL | `https://aws-gateway-backend.whissle.ai/bot` |
| Transport endpoint | `POST /api/bench/agent-turn` |
| Mode | `text` |
| Dataset | NEJM |
| Dataset size | 15 |
| Upstream | github.com/SamuelSchmidgall/AgentClinic (arXiv:2405.07960) |
| Repo commit at report time | `89f2e02` |
| Captured at | 2026-08-08 |
| Run directory | `results/whissle/agentclinic/20260808T023122Z-vision-control` |
| Protocol | markers |
| History | native |
| Prompt mode | override |
| Vision | off |
| Agent created for run | False |

### 3.1 Judge and its independence

| Field | Value |
|---|---|
| Grading kind | llm jury |
| Independent of the agent's vendor | n/a — no judge model is called |
| K (grading passes) | 1 |

### 3.2 Sampling and population

| Field | Value |
|---|---|
| Method | head-of-set selection |
| Population | 15 |
| Requested | 3 |
| Selected | 3 |
| Scored | 3 |
| Seed | 0 |

`head` selection takes the leading N scenarios of the dataset. It is deterministic and it is not random — any ordering structure in the dataset is inherited wholesale.

## 4. Results

**Diagnostic accuracy: 66.7%** (N = 3 · PRELIMINARY), 95% CI [20.8%, 93.9%].

| Metric | Value | 95% CI | N | Qualifiers |
|---|---:|---|---:|---|
| **Diagnostic accuracy** | **66.7%** | [20.8%, 93.9%] | 3 | N = 3 · PRELIMINARY |
<!-- honesty:allow-context -->
| Accuracy when a diagnosis was actually given | 66.7% | [20.8%, 93.9%] | 3 | refusals and non-commits removed from the denominator |
| Commit rate | 100.0% | [43.8%, 100.0%] | 3 | how often the agent named a diagnosis at all |
<!-- /honesty:allow-context -->

_The headline row is the claim and carries its qualifiers; the rest are components of it and inherit them._

<!-- honesty:allow-context -->
**Outcome distribution**

| Outcome | N | Share | Reading |
|---|---|---|---|
| correct | 2 | 66.7% | named the right diagnosis |
| incorrect | 1 | 33.3% | named a diagnosis; it was wrong |

`declined` and `no_commit` both count as incorrect in the headline accuracy, which is upstream's rule. Separating them is how you tell a cautious agent from a lost one.
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
**Diagnostic effort**

| Measure | Mean | Max | Budget |
|---|---|---|---|
| Inferences used | 8.7 | 13 | 20 |
| Tests ordered | 2.0 | 4 | unbounded |

An agent that hits the inference budget is being cut off mid-workup, and its `no_commit` count is a budget artefact rather than a capability finding.
<!-- /honesty:allow-context -->

## 5. Comparison to published baselines

No published AgentClinic baseline is registered in this harness, so no comparison table is printed. The upstream paper reports accuracies on this dataset, but under a different moderator and with a different inference budget; transcribing those numbers here without re-running under a matched protocol would produce a comparison that looks rigorous and is not. This section stays empty until the protocol is matched.

_An empty comparison section is a result. Printing a number next to a differently-measured one would not be._

## 6. Failure analysis

| Category | Count | Rate | Severity |
|---|---:|---:|---|
<!-- honesty:allow-context -->
| Committed to the wrong diagnosis | 1 | 33.3% | high |
<!-- /honesty:allow-context -->

### 6.1 Committed to the wrong diagnosis — 1 of 3

<!-- honesty:allow-context -->
The agent named a diagnosis and it was not the reference one. These are the cases worth reading: a confident wrong answer is the failure mode with clinical consequences, and the transcript shows which question was never asked.
<!-- /honesty:allow-context -->

- **`NEJM-1`** — said “Cat-Scratch Disease (Bartonella henselae infection)”, reference “Paracoccidiomycosis” · 13 inferences · 4 tests
  > That's a critical detail. Given your cat exposure, the bilateral cervical lymphadenopathy, fever, weight loss, eosinophilia, and the timeline matching cat-scratch disease, I believe I have enough information. DIAGNOSIS READY: Cat-Scratch Disease (Bartonella henselae infection)
  _artifact:_ `cases/NEJM-1.json`

### Sample cases

Picked deterministically from this run's own artifacts — the best and worst by the benchmark's own score — so re-generating the report shows the same cases rather than reshuffling them.

<!-- honesty:allow-context -->
**✓ `NEJM-0`** — correct
  - _task:_ NEJM consultation · 5/20 inferences · 0 tests ordered
  - _reference:_ Exogenous ochronosis  ·  _agent said:_ Ochronosis (exogenous ochronosis caused by prolonged hydroquinone exposure)
  > That's a very astute observation, and the timeline is quite significant. Given the bluish-brown discoloration, the redness, visible vascularity, and the fact that it worsened after prolonged hydroquinone use, I'm fairly confident about what we're dealing with here.  DIAGNOSIS READY: Ochronosis (exogenous ochronosis caused by prolonged hydroquinone exposure)
  - _why this one:_ committed the reference diagnosis
  - _artifact:_ `cases/NEJM-0.json`

**✓ `NEJM-2`** — correct
  - _task:_ NEJM consultation · 8/20 inferences · 2 tests ordered
  - _reference:_ Pulmonary Sarcoidosis  ·  _agent said:_ Sarcoidosis (Pulmonary Stage II)
  > Based on the clinical presentation, imaging findings showing mediastinal lymphadenopathy with ground-glass opacities and a "crazy paving" pattern, and the transbronchial biopsy confirming noncaseating granulomas with negative infectious workup, combined with normal calcium and ACE levels, the diagnosis is clear.  DIAGNOSIS READY: Sarcoidosis (Pulmonary Stage II)
  - _why this one:_ committed the reference diagnosis
  - _artifact:_ `cases/NEJM-2.json`

**✗ `NEJM-1`** — incorrect
  - _task:_ NEJM consultation · 13/20 inferences · 4 tests ordered
  - _reference:_ Paracoccidiomycosis  ·  _agent said:_ Cat-Scratch Disease (Bartonella henselae infection)
  > That's a critical detail. Given your cat exposure, the bilateral cervical lymphadenopathy, fever, weight loss, eosinophilia, and the timeline matching cat-scratch disease, I believe I have enough information. DIAGNOSIS READY: Cat-Scratch Disease (Bartonella henselae infection)
  - _why this one:_ committed a different diagnosis, or never committed at all
  - _artifact:_ `cases/NEJM-1.json`

<!-- /honesty:allow-context -->

## 7. Exclusions and what they do to the number

Nothing was excluded: all 3 attempted units produced a gradable result. The headline denominator is the full attempted set.

## 8. Limitations and threats to validity

- **moderator leniency** (medium) — Accuracy depends on a moderator model deciding whether free text matches a reference diagnosis. The strict and lenient counts differ, and the headline uses the strict one — but the boundary is a model's judgement, not a string match.
- **answer options leak** (medium) — The upstream dataset presents the reference diagnosis among a small set of options in some configurations, which inflates accuracy for every model equally. It is left as-is so the number stays comparable, but it is not a measure of open-ended diagnostic ability.
- **head selection** (medium) — 3 scenarios were taken from the head of a 15-scenario dataset rather than drawn at random.
- **simulated patient** (high) — The patient is a language model following a case card. It answers questions more cooperatively, more fluently and more consistently than a person in a waiting room, so the intake task here is easier than the product's real one.

- **sample size** (high) — N = 3 is below the 30-unit threshold this reporting layer uses to call a figure settled. The report is labelled PRELIMINARY throughout.

## 9. Reproduction

```bash
uv sync --extra dev
python -m tau2.health.agentclinic.run --dataset NEJM --limit 3 --prompt-mode override --seed 0
python -m tau2.reporting.cli build results/whissle/agentclinic/20260808T023122Z-vision-control
```

| Field | Value |
|---|---|
| WHISSLE_BASE | https://aws-gateway-backend.whissle.ai/bot |
| harness commit | unknown |
| repo commit at report time | 89f2e02 |

- `head` selection with a fixed limit reproduces the same scenario set exactly.
- The run provisions a throwaway agent and deletes it afterwards (`agent_deleted: None`), so the agent id in provenance will not resolve after the fact.

## Appendix A — raw artifacts

| Path | Present | What it is |
|---|:---:|---|
| `RUN.json` | yes | run configuration, written before the first case |
| `SUMMARY.json` | yes | run-level aggregation, written on completion |
| `cases/` | yes | 3 per-case records with `diagnostics` |
| `transcripts/` | yes | human-readable consultation transcripts |
| `REPORT.md` | yes | this report |
| `report.json` | yes | machine-readable form of this report |

Every per-case record carries a `diagnostics` block (`tau2.health.diagnostics/v1`) with flow trace, signals, metadata sidecar, tool forensics, provenance and cost — and explicit availability flags, so an absent measurement reads as absent rather than as zero. See `HEALTH_DIAGNOSTICS.md`.

## Appendix B — honesty-rule compliance

These rules are executed against this document, not asserted about it. A failing rule blocks generation.

| Rule | Verdict | Checked |
|---|:---:|---|
| `R1_headline_requires_n` | pass | headline carries N = 3 everywhere it is stated |
| `R2_judge_independence_disclosed` | pass | not applicable — judge is independent or deterministic |
| `R3_exclusion_rate_adjacent` | pass | not applicable — nothing was excluded |
| `R4_preliminary_labelled` | pass | labelled PRELIMINARY |
| `R5_no_provider_names` | pass | no LLM vendor named outside the published-baseline table |
| `R6_comparability_stated` | pass | not applicable — no published baseline is registered |
| `R7_baseline_named` | pass | not applicable — no published baseline is registered |

---

_AgentClinic, arXiv:2405.07960. Research measurement only — not a clinical evaluation of anything._

<!-- generated by tau2.reporting from agentclinic/20260808T023122Z-vision-control; schema tau2.reporting.run_report/v1 -->
