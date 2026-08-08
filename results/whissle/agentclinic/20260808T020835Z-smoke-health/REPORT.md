# AgentClinic — Whissle as the doctor (MedQA)

> **PRELIMINARY** — N = 5 is below the 30-unit threshold for a settled number. Treat every figure below as directional.

## Abstract

Whissle was evaluated on **AgentClinic** in `text` mode. The headline result is **100.0%** (N = 5 · PRELIMINARY) for diagnostic accuracy, 95% CI [56.6%, 100.0%].

Whether an agent can run a diagnostic consultation: take a patient's presentation, ask the questions that discriminate between the candidate diagnoses, order the tests it needs, and commit to an answer within a bounded number of inferences. The agent plays the doctor; a simulated patient and a simulated measurement device play the other side.

## At a glance

| Field | Value |
|---|---|
| **Diagnostic accuracy** | **100.0%** (N = 5 · PRELIMINARY) |
<!-- honesty:allow-context -->
| 95% CI | [56.6%, 100.0%] |
| Attempted / scored / excluded | 5 / 5 / 0 (0.0%) |
| Judge | unknown |
| Mode | `text` |
| Date | 2026-08-08 |
| Run id | `agentclinic/20260808T020835Z-smoke-health` |
| Status | **PRELIMINARY** |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
- **Accuracy when a diagnosis was actually given:** 100.0% [56.6%, 100.0%], N = 5 — refusals and non-commits removed from the denominator
- **Commit rate:** 100.0% [56.6%, 100.0%], N = 5 — how often the agent named a diagnosis at all
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
| Agent id | `51ec89e2-a107-46ff-9af5-9882020a0ea0` |
| Base URL | `https://aws-gateway-backend.whissle.ai/bot` |
| Transport endpoint | `POST /api/bench/agent-turn` |
| Mode | `text` |
| Dataset | MedQA |
| Dataset size | 107 |
| Upstream | github.com/SamuelSchmidgall/AgentClinic (arXiv:2405.07960) |
| Repo commit at report time | `89f2e02` |
| Captured at | 2026-08-08 |
| Run directory | `results/whissle/agentclinic/20260808T020835Z-smoke-health` |
| Protocol | markers |
| History | native |
| Vision | off |
| Agent type | patient_checkin |
| Agent created for run | True |
| Agent deleted | True |

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
| Population | 107 |
| Requested | 5 |
| Selected | 5 |
| Scored | 5 |
| Seed | 0 |

`head` selection takes the leading N scenarios of the dataset. It is deterministic and it is not random — any ordering structure in the dataset is inherited wholesale.

## 4. Results

**Diagnostic accuracy: 100.0%** (N = 5 · PRELIMINARY), 95% CI [56.6%, 100.0%].

| Metric | Value | 95% CI | N | Qualifiers |
|---|---:|---|---:|---|
| **Diagnostic accuracy** | **100.0%** | [56.6%, 100.0%] | 5 | N = 5 · PRELIMINARY |
<!-- honesty:allow-context -->
| Accuracy when a diagnosis was actually given | 100.0% | [56.6%, 100.0%] | 5 | refusals and non-commits removed from the denominator |
| Commit rate | 100.0% | [56.6%, 100.0%] | 5 | how often the agent named a diagnosis at all |
<!-- /honesty:allow-context -->

_The headline row is the claim and carries its qualifiers; the rest are components of it and inherit them._

<!-- honesty:allow-context -->
**Outcome distribution**

| Outcome | N | Share | Reading |
|---|---|---|---|
| correct | 5 | 100.0% | named the right diagnosis |

`declined` and `no_commit` both count as incorrect in the headline accuracy, which is upstream's rule. Separating them is how you tell a cautious agent from a lost one.
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
**Diagnostic effort**

| Measure | Mean | Max | Budget |
|---|---|---|---|
| Inferences used | 7.4 | 9 | 20 |
| Tests ordered | 1.8 | 3 | unbounded |

An agent that hits the inference budget is being cut off mid-workup, and its `no_commit` count is a budget artefact rather than a capability finding.
<!-- /honesty:allow-context -->

## 5. Comparison to published baselines

No published AgentClinic baseline is registered in this harness, so no comparison table is printed. The upstream paper reports accuracies on this dataset, but under a different moderator and with a different inference budget; transcribing those numbers here without re-running under a matched protocol would produce a comparison that looks rigorous and is not. This section stays empty until the protocol is matched.

_An empty comparison section is a result. Printing a number next to a differently-measured one would not be._

## 6. Failure analysis

_No categorised failures were recorded for this run._

### Sample cases

Picked deterministically from this run's own artifacts — the best and worst by the benchmark's own score — so re-generating the report shows the same cases rather than reshuffling them.

<!-- honesty:allow-context -->
**✓ `MedQA-0`** — correct
  - _task:_ MedQA consultation · 6/20 inferences · 1 tests ordered
  - _reference:_ Myasthenia gravis  ·  _agent said:_ Myasthenia Gravis

You have myasthenia gravis, an autoimmune condition where your body produces antibodies that interfere with communication between nerves and muscles. This explains your fatigue-rela
  > Excellent — the test results confirm what I suspected based on your symptoms. Your acetylcholine receptor antibodies are elevated, and your electromyography shows the characteristic pattern of a neuromuscular junction disorder. Your chest CT was normal, which is good news.  DIAGNOSIS READY: Myasthenia Gravis  You have myasthenia gravis, an autoimmune condition where your body produces antibodies t
  - _why this one:_ committed the reference diagnosis
  - _artifact:_ `cases/MedQA-0.json`

**✓ `MedQA-1`** — correct
  - _task:_ MedQA consultation · 9/20 inferences · 2 tests ordered
  - _reference:_ Progressive multifocal encephalopathy (PML)  ·  _agent said:_ Progressive Multifocal Leukoencephalopathy (PML) associated with natalizumab use in the setting of Crohn's disease with severe immunosuppression.

You need immediate specialist care—neurology and infe
  > Based on your clinical presentation, imaging findings, immunosuppressive therapy with natalizumab, and the characteristic neurological exam findings, I have enough evidence to make my assessment.  DIAGNOSIS READY: Progressive Multifocal Leukoencephalopathy (PML) associated with natalizumab use in the setting of Crohn's disease with severe immunosuppression.  You need immediate specialist care—neur
  - _why this one:_ committed the reference diagnosis
  - _artifact:_ `cases/MedQA-1.json`

<!-- /honesty:allow-context -->

## 7. Exclusions and what they do to the number

Nothing was excluded: all 5 attempted units produced a gradable result. The headline denominator is the full attempted set.

## 8. Limitations and threats to validity

- **moderator leniency** (medium) — Accuracy depends on a moderator model deciding whether free text matches a reference diagnosis. The strict and lenient counts differ, and the headline uses the strict one — but the boundary is a model's judgement, not a string match.
- **answer options leak** (medium) — The upstream dataset presents the reference diagnosis among a small set of options in some configurations, which inflates accuracy for every model equally. It is left as-is so the number stays comparable, but it is not a measure of open-ended diagnostic ability.
- **head selection** (medium) — 5 scenarios were taken from the head of a 107-scenario dataset rather than drawn at random.
- **simulated patient** (high) — The patient is a language model following a case card. It answers questions more cooperatively, more fluently and more consistently than a person in a waiting room, so the intake task here is easier than the product's real one.

- **sample size** (high) — N = 5 is below the 30-unit threshold this reporting layer uses to call a figure settled. The report is labelled PRELIMINARY throughout.

## 9. Reproduction

```bash
uv sync --extra dev
python -m tau2.health.agentclinic.run --dataset MedQA --limit 5 --prompt-mode override --seed 0
python -m tau2.reporting.cli build results/whissle/agentclinic/20260808T020835Z-smoke-health
```

| Field | Value |
|---|---|
| WHISSLE_BASE | https://aws-gateway-backend.whissle.ai/bot |
| harness commit | unknown |
| repo commit at report time | 89f2e02 |

- `head` selection with a fixed limit reproduces the same scenario set exactly.
- The run provisions a throwaway agent and deletes it afterwards (`agent_deleted: True`), so the agent id in provenance will not resolve after the fact.

## Appendix A — raw artifacts

| Path | Present | What it is |
|---|:---:|---|
| `RUN.json` | yes | run configuration, written before the first case |
| `SUMMARY.json` | yes | run-level aggregation, written on completion |
| `cases/` | yes | 5 per-case records with `diagnostics` |
| `transcripts/` | yes | human-readable consultation transcripts |
| `REPORT.md` | yes | this report |
| `report.json` | yes | machine-readable form of this report |

Every per-case record carries a `diagnostics` block (`tau2.health.diagnostics/v1`) with flow trace, signals, metadata sidecar, tool forensics, provenance and cost — and explicit availability flags, so an absent measurement reads as absent rather than as zero. See `HEALTH_DIAGNOSTICS.md`.

## Appendix B — honesty-rule compliance

These rules are executed against this document, not asserted about it. A failing rule blocks generation.

| Rule | Verdict | Checked |
|---|:---:|---|
| `R1_headline_requires_n` | pass | headline carries N = 5 everywhere it is stated |
| `R2_judge_independence_disclosed` | pass | not applicable — judge is independent or deterministic |
| `R3_exclusion_rate_adjacent` | pass | not applicable — nothing was excluded |
| `R4_preliminary_labelled` | pass | labelled PRELIMINARY |
| `R5_no_provider_names` | pass | no LLM vendor named outside the published-baseline table |
| `R6_comparability_stated` | pass | not applicable — no published baseline is registered |
| `R7_baseline_named` | pass | not applicable — no published baseline is registered |

---

_AgentClinic, arXiv:2405.07960. Research measurement only — not a clinical evaluation of anything._

<!-- generated by tau2.reporting from agentclinic/20260808T020835Z-smoke-health; schema tau2.reporting.run_report/v1 -->
