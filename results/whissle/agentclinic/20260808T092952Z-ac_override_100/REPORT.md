# AgentClinic — Whissle as the doctor (MedQA)

## Abstract

Whissle was evaluated on **AgentClinic** in `text` mode. The headline result is **75.0%** (N = 100 · judge not independent) for diagnostic accuracy, 95% CI [65.7%, 82.5%].

Whether an agent can run a diagnostic consultation: take a patient's presentation, ask the questions that discriminate between the candidate diagnoses, order the tests it needs, and commit to an answer within a bounded number of inferences. The agent plays the doctor; a simulated patient and a simulated measurement device play the other side.

**The judge is not independent of the agent's vendor.** This number is a sound internal regression instrument and is not a leaderboard result; §3 says exactly why.

## At a glance

| Field | Value |
|---|---|
| **Diagnostic accuracy** | **75.0%** (N = 100 · judge not independent) |
<!-- honesty:allow-context -->
| 95% CI | [65.7%, 82.5%] |
| Attempted / scored / excluded | 100 / 100 / 0 (0.0%) |
| Judge | whissle (NOT independent) |
| Mode | `text` |
| Date | 2026-08-08 |
| Harness commit | `86b4475` |
| Run id | `agentclinic/20260808T092952Z-ac_override_100` |
| Status | complete |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
- **Accuracy when a diagnosis was actually given:** 83.3% [74.3%, 89.6%], N = 90 — refusals and non-commits removed from the denominator
- **Commit rate:** 90.0% [82.6%, 94.5%], N = 100 — how often the agent named a diagnosis at all
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
| Agent id | `c8aa2355-9fd7-46da-9993-07ea566ec3d6` |
| Base URL | `https://aws-gateway-backend.whissle.ai/bot` |
| Transport endpoint | `POST /api/bench/agent-turn` |
| Mode | `text` |
| Dataset | MedQA |
| Dataset size | 107 |
| Upstream | github.com/SamuelSchmidgall/AgentClinic (arXiv:2405.07960) |
| Harness commit | `86b4475` |
| Repo commit at report time | `89f2e02` |
| Captured at | 2026-08-08T09:30:16+00:00 |
| Run directory | `results/whissle/agentclinic/20260808T092952Z-ac_override_100` |
| Protocol | markers |
| History | native |
| Prompt mode | override |
| Vision | off |
| Agent created for run | False |

### 3.1 Judge and its independence

| Field | Value |
|---|---|
| Grading kind | llm jury |
| Provider | `whissle` |
| Model | `default` |
| Endpoint | `whissle:/api/models/chat` |
| Independent of the agent's vendor | **NO** |
| K (grading passes) | 1 |
| Judge calls | 1183 |
| Judge spend | $0.0643 |

<!-- honesty:allow-providers -->
> Judge independence: this run's simulators and graders were routed through Whissle's own model API (`POST /api/models/chat`). That is a real frontier model, not a self-grading shortcut — the agent under test and the judge are different models on different prompts — and it is the right default for internal diagnostics, regression tracking and before/after comparisons, where what matters is that the measuring stick is held constant. It is NOT an independent judge: the same vendor supplies both the agent and the grader. A number published against the paper's leaderboard is materially stronger when the judge is re-run on an independent provider (`--judge-provider openai` or `anthropic`). Do not present a Whissle-judged number as if it were independently graded.
<!-- /honesty:allow-providers -->

### 3.2 Sampling and population

| Field | Value |
|---|---|
| Method | head-of-set selection |
| Population | 107 |
| Requested | 100 |
| Selected | 100 |
| Scored | 100 |
| Seed | 42 |

`head` selection takes the leading N scenarios of the dataset. It is deterministic and it is not random — any ordering structure in the dataset is inherited wholesale.

## 4. Results

**Diagnostic accuracy: 75.0%** (N = 100 · judge not independent), 95% CI [65.7%, 82.5%].

| Metric | Value | 95% CI | N | Qualifiers |
|---|---:|---|---:|---|
| **Diagnostic accuracy** | **75.0%** | [65.7%, 82.5%] | 100 | N = 100 · judge not independent |
<!-- honesty:allow-context -->
| Accuracy when a diagnosis was actually given | 83.3% | [74.3%, 89.6%] | 90 | refusals and non-commits removed from the denominator |
| Commit rate | 90.0% | [82.6%, 94.5%] | 100 | how often the agent named a diagnosis at all |
<!-- /honesty:allow-context -->

_The headline row is the claim and carries its qualifiers; the rest are components of it and inherit them._

<!-- honesty:allow-context -->
**Outcome distribution**

| Outcome | N | Share | Reading |
|---|---|---|---|
| correct | 75 | 75.0% | named the right diagnosis |
| incorrect | 15 | 15.0% | named a diagnosis; it was wrong |
| no_commit | 10 | 10.0% | ran out of inferences without committing |

`declined` and `no_commit` both count as incorrect in the headline accuracy, which is upstream's rule. Separating them is how you tell a cautious agent from a lost one.
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
**Diagnostic effort**

| Measure | Mean | Max | Budget |
|---|---|---|---|
| Inferences used | 11.7 | 20 | 20 |
| Tests ordered | 2.8 | 11 | unbounded |

An agent that hits the inference budget is being cut off mid-workup, and its `no_commit` count is a budget artefact rather than a capability finding.
<!-- /honesty:allow-context -->

## 5. Comparison to published baselines

No published AgentClinic baseline is registered in this harness, so no comparison table is printed. The upstream paper reports accuracies on this dataset, but under a different moderator and with a different inference budget; transcribing those numbers here without re-running under a matched protocol would produce a comparison that looks rigorous and is not. This section stays empty until the protocol is matched.

_An empty comparison section is a result. Printing a number next to a differently-measured one would not be._

## 6. Failure analysis

| Category | Count | Rate | Severity |
|---|---:|---:|---|
<!-- honesty:allow-context -->
| Committed to the wrong diagnosis | 15 | 15.0% | high |
| Never committed — ran out of budget | 10 | 10.0% | medium |
<!-- /honesty:allow-context -->

### 6.1 Committed to the wrong diagnosis — 15 of 100

<!-- honesty:allow-context -->
The agent named a diagnosis and it was not the reference one. These are the cases worth reading: a confident wrong answer is the failure mode with clinical consequences, and the transcript shows which question was never asked.
<!-- /honesty:allow-context -->

- **`MedQA-1`** — said “Multiple Sclerosis (MS) with natalizumab-associated neuroinflammation”, reference “Progressive multifocal encephalopathy (PML)” · 12 inferences · 2 tests
  > Based on your presentation—progressive ataxia and arm weakness over three weeks, demyelinating lesions on MRI, and your immunosuppressed state on natalizumab—the clinical picture is most consistent with MS, likely unmasked or accelerated by the natalizumab treatment. You need urgent neurology referr
  _artifact:_ `cases/MedQA-1.json`
- **`MedQA-15`** — said “Benign Subcutaneous Cyst or Lipoma of the Neck

You can follow up with your primary care doctor, and if the swelling continues to grow or becomes bothersome, we can discuss removal options with a surgeon. Take care!”, reference “Epidermoid cyst” · 8 inferences · 1 tests
  > I'm glad the imaging is reassuring for you. Based on the ultrasound showing a cystic lesion, the normal CT, the firm mobile nodule on examination, and your lack of systemic symptoms, this appears to be a benign lesion—most likely either a simple cyst or a lipoma, which are both common and harmless g
  _artifact:_ `cases/MedQA-15.json`
- **`MedQA-24`** — said “Benign Ovarian Tumor (likely mature cystic teratoma or serous cystadenoma) with Secondary Ascites and Pleural Effusion due to mechanical obstruction and fluid redistribution”, reference “Meigs syndrome” · 13 inferences · 2 tests
  > I've gathered substantial information, and based on the clinical picture—a large ovarian mass with ascites, right-sided pleural effusion, normal CA-125, normal pelvic MRI imaging, normal liver function, no family cancer history, and your presentation of progressive dyspnea with orthopnea—I believe w
  _artifact:_ `cases/MedQA-24.json`

### 6.2 Never committed — ran out of budget — 10 of 100

<!-- honesty:allow-context -->
The inference budget expired before the agent named a diagnosis. Scored as incorrect, but it is a different defect from being wrong: it is an agent that gathers indefinitely and never concludes. Where these cluster, the accuracy number is partly a measure of pacing.
<!-- /honesty:allow-context -->

- **`MedQA-44`** — used 20/20 inferences · 4 tests ordered
  > That’s good to know, because the coughing and the tightness in my chest are what's really bothering me right now. I’m ready to start the inhaler if it means I can finally sleep through the night and walk up my stairs without stopping. What should I do first to get this under control?
  _artifact:_ `cases/MedQA-44.json`
- **`MedQA-48`** — used 20/20 inferences · 5 tests ordered
  > It's a relief to hear you might be narrowing it down to something, even if it's rare. Does this rare thing explain why my uncle's joints get so big and why I'm still bleeding so much from my mouth? I just want to know if there's a specific treatment once you figure out which one it is.
  _artifact:_ `cases/MedQA-48.json`
- **`MedQA-52`** — used 20/20 inferences · 5 tests ordered
  > I see, it's strange to think something could be there my whole life and only start acting up now. Will the next steps tell us for sure if that's what's causing the weight loss and this yellow skin?
  _artifact:_ `cases/MedQA-52.json`

### Sample cases

Picked deterministically from this run's own artifacts — the best and worst by the benchmark's own score — so re-generating the report shows the same cases rather than reshuffling them.

<!-- honesty:allow-context -->
**✓ `MedQA-0`** — correct
  - _task:_ MedQA consultation · 9/20 inferences · 1 tests ordered
  - _reference:_ Myasthenia gravis  ·  _agent said:_ Myasthenia Gravis
  > These test results are highly diagnostic and align perfectly with your clinical presentation. The combination of decreased muscle response on repetitive stimulation, positive acetylcholine receptor antibodies, and your characteristic pattern of fatigable weakness with ocular involvement is conclusive.  DIAGNOSIS READY: Myasthenia Gravis
  - _why this one:_ committed the reference diagnosis
  - _artifact:_ `cases/MedQA-0.json`

**✓ `MedQA-10`** — correct
  - _task:_ MedQA consultation · 10/20 inferences · 1 tests ordered
  - _reference:_ Hemorrhoids  ·  _agent said:_ Internal Hemorrhoids (Prolapsed)

Wishing you well with your colonoscopy procedure and your recovery ahead.
  > That was the right approach. Based on everything we've gathered—your examination showing prolapsed internal hemorrhoids on anoscopy, normal blood work, the characteristic bright red bleeding with bowel movements, no systemic symptoms, and the mass being easily reducible—I'm confident in my diagnosis. The colonoscopy will still proceed to ensure there are no other lesions higher in the colon, but I
  - _why this one:_ committed the reference diagnosis
  - _artifact:_ `cases/MedQA-10.json`

**✗ `MedQA-1`** — incorrect
  - _task:_ MedQA consultation · 12/20 inferences · 2 tests ordered
  - _reference:_ Progressive multifocal encephalopathy (PML)  ·  _agent said:_ Multiple Sclerosis (MS) with natalizumab-associated neuroinflammation
  > Based on your presentation—progressive ataxia and arm weakness over three weeks, demyelinating lesions on MRI, and your immunosuppressed state on natalizumab—the clinical picture is most consistent with MS, likely unmasked or accelerated by the natalizumab treatment. You need urgent neurology referral and likely an urgent MRI of your spinal cord to assess for additional lesions, and your natalizum
  - _why this one:_ committed a different diagnosis, or never committed at all
  - _artifact:_ `cases/MedQA-1.json`

**✗ `MedQA-15`** — incorrect
  - _task:_ MedQA consultation · 8/20 inferences · 1 tests ordered
  - _reference:_ Epidermoid cyst  ·  _agent said:_ Benign Subcutaneous Cyst or Lipoma of the Neck

You can follow up with your primary care doctor, and if the swelling continues to grow or becomes bothersome, we can discuss removal options with a surg
  > I'm glad the imaging is reassuring for you. Based on the ultrasound showing a cystic lesion, the normal CT, the firm mobile nodule on examination, and your lack of systemic symptoms, this appears to be a benign lesion—most likely either a simple cyst or a lipoma, which are both common and harmless growths in the subcutaneous tissue.  DIAGNOSIS READY: Benign Subcutaneous Cyst or Lipoma of the Neck
  - _why this one:_ committed a different diagnosis, or never committed at all
  - _artifact:_ `cases/MedQA-15.json`

<!-- /honesty:allow-context -->

## 7. Exclusions and what they do to the number

Nothing was excluded: all 100 attempted units produced a gradable result. The headline denominator is the full attempted set.

## 8. Limitations and threats to validity

- **judge independence** (high) — The moderator, the patient simulator and the decline-judge all ran on the same vendor's model API as the agent under test. Constant across runs it measures change honestly; against an external leaderboard it does not.
- **moderator leniency** (medium) — Accuracy depends on a moderator model deciding whether free text matches a reference diagnosis. The strict and lenient counts differ, and the headline uses the strict one — but the boundary is a model's judgement, not a string match.
- **answer options leak** (medium) — The upstream dataset presents the reference diagnosis among a small set of options in some configurations, which inflates accuracy for every model equally. It is left as-is so the number stays comparable, but it is not a measure of open-ended diagnostic ability.
- **head selection** (medium) — 100 scenarios were taken from the head of a 107-scenario dataset rather than drawn at random.
- **simulated patient** (high) — The patient is a language model following a case card. It answers questions more cooperatively, more fluently and more consistently than a person in a waiting room, so the intake task here is easier than the product's real one.

## 9. Reproduction

```bash
uv sync --extra dev
python -m tau2.health.agentclinic.run --dataset MedQA --limit 100 --prompt-mode override --seed 42
python -m tau2.reporting.cli build results/whissle/agentclinic/20260808T092952Z-ac_override_100
```

| Field | Value |
|---|---|
| WHISSLE_BASE | https://aws-gateway-backend.whissle.ai/bot |
| harness commit | 86b4475 |
| repo commit at report time | 89f2e02 |

- `head` selection with a fixed limit reproduces the same scenario set exactly.
- The run provisions a throwaway agent and deletes it afterwards (`agent_deleted: None`), so the agent id in provenance will not resolve after the fact.

## Appendix A — raw artifacts

| Path | Present | What it is |
|---|:---:|---|
| `RUN.json` | yes | run configuration, written before the first case |
| `SUMMARY.json` | yes | run-level aggregation, written on completion |
| `cases/` | yes | 100 per-case records with `diagnostics` |
| `transcripts/` | yes | human-readable consultation transcripts |
| `REPORT.md` | yes | this report |
| `report.json` | yes | machine-readable form of this report |

Every per-case record carries a `diagnostics` block (`tau2.health.diagnostics/v1`) with flow trace, signals, metadata sidecar, tool forensics, provenance and cost — and explicit availability flags, so an absent measurement reads as absent rather than as zero. See `HEALTH_DIAGNOSTICS.md`.

## Appendix B — honesty-rule compliance

These rules are executed against this document, not asserted about it. A failing rule blocks generation.

| Rule | Verdict | Checked |
|---|:---:|---|
| `R1_headline_requires_n` | pass | headline carries N = 100 everywhere it is stated |
| `R2_judge_independence_disclosed` | pass | non-independent judge disclosed beside the number |
| `R3_exclusion_rate_adjacent` | pass | not applicable — nothing was excluded |
| `R4_preliminary_labelled` | pass | not applicable — N = 100 ≥ 30 and the run is complete |
| `R5_no_provider_names` | pass | no LLM vendor named outside the published-baseline table |
| `R6_comparability_stated` | pass | not applicable — no published baseline is registered |
| `R7_baseline_named` | pass | not applicable — no published baseline is registered |

---

_AgentClinic, arXiv:2405.07960. Research measurement only — not a clinical evaluation of anything._

<!-- generated by tau2.reporting from agentclinic/20260808T092952Z-ac_override_100; schema tau2.reporting.run_report/v1 -->
