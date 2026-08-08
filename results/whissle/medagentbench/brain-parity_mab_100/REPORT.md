# MedAgentBench — Whissle (brain-parity)

## Abstract

Whissle was evaluated on **MedAgentBench** in `brain-parity` mode. The headline result is **54.0%** (N = 100) for overall success rate, 95% CI [44.3%, 63.4%].

Whether an agent can operate a real electronic health record over FHIR: read the right resource for a clinical question (Query), and write a correct, conformant resource back when the task calls for it (Action). Grading is deterministic against live chart state — no rubric, no grader model, no partial credit.

## At a glance

| Field | Value |
|---|---|
| **Overall success rate** | **54.0%** (N = 100) |
<!-- honesty:allow-context -->
| 95% CI | [44.3%, 63.4%] |
| Attempted / scored / excluded | 100 / 100 / 0 (0.0%) |
| Judge | deterministic grader (no judge model) |
| Mode | `brain-parity` |
| Date | 2026-08-08 |
| Harness commit | `86b4475` |
| Run id | `medagentbench/brain-parity_mab_100` |
| Status | complete |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
- **Query success rate:** 68.0% [54.2%, 79.2%], N = 50
- **Action success rate:** 40.0% [27.6%, 53.8%], N = 50
<!-- /honesty:allow-context -->

## 1. What was measured, and why

Whether an agent can operate a real electronic health record over FHIR: read the right resource for a clinical question (Query), and write a correct, conformant resource back when the task calls for it (Action). Grading is deterministic against live chart state — no rubric, no grader model, no partial credit.

**Why this benchmark.** A health assistant that can talk but cannot correctly read and write the chart is a demo. This is the benchmark that separates the two, and its Action half is the part almost every published model is worst at.

## 2. Methodology

| Field | Value |
|---|---|
| Agent under test | the deployed Whissle agent brain, unmodified |
| Mode | `brain-parity` — the benchmark's own prompt and protocol; the agent supplies reasoning only |
| Endpoint | `/api/bench/agent-turn` (stateless brain call) |
| Prompt handling | system mode `neutral`: the deployed persona is suppressed so the benchmark's instructions are the only instructions, which is what makes the number comparable |
| Turn limit | 8 rounds per task; a task that has not emitted FINISH by then is scored incorrect, not retried |
| Tools bound | none in the agent's own runtime — the protocol is textual `GET`/`POST`/`FINISH` strings that the harness parses and executes against the FHIR sandbox |
| Write checking | `execute` — POSTs are really executed against the sandbox and read back from the chart |
| Scoring rule | `builtin` deterministic grader; correct / attempted, infra failures excluded from the denominator |

**Scoring rule.** success rate = correct / scored; a task is correct only when the deterministic grader matches the expected value recomputed from chart state at grading time

## 3. Setup and provenance

| Field | Value |
|---|---|
| Agent id | `f52dfc18-7bde-4f10-8674-f646b358acf3` |
| Base URL | `https://aws-gateway-backend.whissle.ai/bot` |
| Transport endpoint | `/api/bench/agent-turn` |
| Mode | `brain-parity` |
| Dataset | MedAgentBench (FHIR R4 sandbox) |
| Dataset size | 300 |
| Upstream | MedAgentBench, NEJM AI 2025 |
| Harness commit | `86b4475` |
| Repo commit at report time | `89f2e02` |
| Captured at | 2026-08-08T09:36:34.307151+00:00 |
| Run directory | `results/whissle/medagentbench/brain-parity_mab_100` |
| Fhir api base | http://localhost:8090/fhir/ |
| Write check | execute |
| Max round | 8 |
| Grader | builtin |
| System mode | neutral |

### 3.1 Judge and its independence

| Field | Value |
|---|---|
| Grading kind | deterministic |
| Independent of the agent's vendor | n/a — no judge model is called |

<!-- honesty:allow-providers -->
> Grading is deterministic: an expected value recomputed from live chart state, compared to the agent's answer. No grader model is called, so judge independence is not a question this benchmark can raise.
<!-- /honesty:allow-providers -->

### 3.2 Sampling and population

| Field | Value |
|---|---|
| Method | head-of-set subset |
| Population | 300 |
| Requested | 100 |
| Selected | 100 |
| Scored | 100 |
| Strata | `category` |

The subset is the leading N tasks of the published set, balanced 10-per-category by construction. It is not a random draw, so it reproduces exactly — and it inherits whatever ordering bias the published set has.

## 4. Results

**Overall success rate: 54.0%** (N = 100), 95% CI [44.3%, 63.4%].

| Metric | Value | 95% CI | N | Qualifiers |
|---|---:|---|---:|---|
| **Overall success rate** | **54.0%** | [44.3%, 63.4%] | 100 | N = 100 |
<!-- honesty:allow-context -->
| Query success rate | 68.0% | [54.2%, 79.2%] | 50 | — |
| Action success rate | 40.0% | [27.6%, 53.8%] | 50 | — |
<!-- /honesty:allow-context -->

_The headline row is the claim and carries its qualifiers; the rest are components of it and inherit them._

<!-- honesty:allow-context -->
**Query vs Action**

| Split | N | Correct | Success rate | 95% CI (Wilson) |
|---|---|---|---|---|
| Query | 50 | 34 | 68.0% | [54.2%, 79.2%] |
| Action | 50 | 20 | 40.0% | [27.6%, 53.8%] |

Query tasks read the chart; Action tasks must write to it. The gap between them is the finding, not the average of them.
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
**Per task category**

| Category | N | Correct | Success rate |
|---|---|---|---|
| task1 | 10 | 10 | 100.0% |
| task2 | 10 | 9 | 90.0% |
| task3 | 10 | 6 | 60.0% |
| task4 | 10 | 9 | 90.0% |
| task5 | 10 | 6 | 60.0% |
| task6 | 10 | 6 | 60.0% |
| task7 | 10 | 0 | 0.0% |
| task8 | 10 | 7 | 70.0% |
| task9 | 10 | 0 | 0.0% |
| task10 | 10 | 1 | 10.0% |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
**Write integrity — said vs emitted vs landed**

| Measure | Value | Reading |
|---|---|---|
| Episodes that claimed an action | 19 | the agent told the user it had done something |
| Episodes that emitted a write | 29 | a POST actually left the harness |
| Writes accepted by the EHR | 35 | the FHIR server took it |
| Writes verified back in the chart | 35 | read back and found — the only proof it landed |
| Said but did not write | 0 (0.0%) | **the safety-critical count** — claiming an action that never happened |
| Wrote but did not say | 10 (20.0%) | silent side effect — the chart changed and the user was not told |
| Emitted but non-conformant FHIR | 7 (14.0%) | accepted by a permissive server; would fail a strict one |

Write mode was `execute` — writes were really executed against the FHIR sandbox and read back, not simulated.
<!-- /honesty:allow-context -->

## 5. Comparison to published baselines

**Not directly comparable — read this before reading the table.** This run scored 100 tasks, the published figures are over 300. A subset success rate is an *estimate* of the full-set rate with its own sampling error, and the subset is the head of the set rather than a random draw. Two things nonetheless *are* comparable: the protocol (same prompts, same action grammar, same deterministic grader) and the Query/Action split, which is a property of the task type rather than of the sample size. Treat the ranking as indicative and the gap as directional; do not quote a placement.

<!-- honesty:allow-context -->
<!-- honesty:allow-providers -->
**Published baselines — MedAgentBench, NEJM AI 2025 (Table 2)**

| System | N | Overall | Query | Action | Published in |
|---|---|---|---|---|---|
| **Whissle (this run)** | 100 | **54.0** | **68.0** | **40.0** | — (this measurement) |
| Claude 3.5 Sonnet v2 | 300 | 69.7 | 85.3 | 54.0 | MedAgentBench, NEJM AI 2025 (Table 2) |
| GPT-4o | 300 | 64.0 | — | — | MedAgentBench, NEJM AI 2025 (Table 2) |
| DeepSeek-V3 | 300 | 62.7 | — | — | MedAgentBench, NEJM AI 2025 (Table 2) |
| Gemini-1.5 Pro | 300 | 62.0 | — | — | MedAgentBench, NEJM AI 2025 (Table 2) |
| GPT-4o-mini | 300 | 56.3 | — | — | MedAgentBench, NEJM AI 2025 (Table 2) |
| o3-mini | 300 | 51.7 | — | — | MedAgentBench, NEJM AI 2025 (Table 2) |
| Qwen2.5 | 300 | 51.3 | — | — | MedAgentBench, NEJM AI 2025 (Table 2) |
| Llama 3.3 | 300 | 46.3 | — | — | MedAgentBench, NEJM AI 2025 (Table 2) |
| Gemini 2.0 Flash | 300 | 38.3 | — | — | MedAgentBench, NEJM AI 2025 (Table 2) |
| Gemma2 | 300 | 19.3 | — | — | MedAgentBench, NEJM AI 2025 (Table 2) |
| Mistral v0.3 | 300 | 4.0 | — | — | MedAgentBench, NEJM AI 2025 (Table 2) |

Published protocol: full 300-task set, same action grammar, same deterministic grader. External model names appear here and only here; they are published comparators, not components of the system under test.
<!-- /honesty:allow-providers -->
<!-- /honesty:allow-context -->

**What is comparable:** the protocol — same prompts, same action grammar, same grader. **What is not:** the sample. This run scored 100; the published figures are over 300.

## 6. Failure analysis

| Category | Count | Rate | Severity |
|---|---:|---:|---|
<!-- honesty:allow-context -->
| Protocol violation — the reply was not GET, POST or FINISH | 9 | 9.0% | high |
| Silent write — the chart changed and the user was not told | 10 | 20.0% | high |
| Non-conformant FHIR accepted by a permissive server | 7 | 20.0% | medium |
| Task categories at or near zero | 29 | 96.7% | high |
| Harness finding: `say_fidelity` | 9 | 9.0% | medium |
<!-- /honesty:allow-context -->

### 6.1 Protocol violation — the reply was not GET, POST or FINISH — 9 of 100

<!-- honesty:allow-context -->
The action grammar is three verbs. A reply that matches none of them cannot be executed, so the task is scored incorrect no matter how good the underlying reasoning was. This is a formatting failure sitting on top of the capability being measured, and it is cheap to fix relative to what it costs.
<!-- /honesty:allow-context -->

- **`task10_5`** — task10 · 2 rounds
  > The last HbA1C value in the chart for patient S0547588 is 6.6% and was recorded on 2023-11-04 at 14:54:00 UTC. Since this measurement was recorded only 9 days ago (within the 1-year threshold), no new HbA1C lab test needs to be ordered.  FINISH(["6.6%", "2023-11-04T14:54:00+00:00"])
  _artifact:_ `tasks/task10_5.json`
- **`task3_3`** — task3 · 1 rounds
  > I need to first find the patient using their MRN, then record the blood pressure observation.  GET http://localhost:8090/fhir/Patient?identifier=S6352985
  _artifact:_ `tasks/task3_3.json`
- **`task3_4`** — task3 · 1 rounds
  > I need to first find the patient by their MRN, then record the blood pressure observation.  GET http://localhost:8090/fhir/Patient?identifier=S3228213
  _artifact:_ `tasks/task3_4.json`

### 6.2 Silent write — the chart changed and the user was not told — 10 of 50

<!-- honesty:allow-context -->
A write was emitted, accepted and verified in the chart, but the agent's closing reply never told the user it had done it. The inverse failure (said-but-did-not-write) is the one that gets written about; this one is the same integrity gap pointing the other way, and on a medication order it is just as serious.
<!-- /honesty:allow-context -->

- **`task9_2`** — task9 · 2 write(s) landed
  > FINISH(["Most recent potassium level: 4.2 mmol/L on 2023-11-12T03:44:00+00:00. Previous level on 2023-11-10T23:08:00+00:00 was 3.3 mmol/L (LOW). Current level is above threshold of 3.5 mmol/L.", "Potassium replacement order created: 10 mEq oral potassium chloride (NDC 40032-917-01) ordered on 2023-1
  _artifact:_ `tasks/task9_2.json`
- **`task9_3`** — task9 · 2 write(s) landed
  > FINISH(["Most recent potassium level: 3.1 mmol/L (measured 2023-11-12T14:52:00+00:00) - LOW", "Potassium replacement order: 40 mEq oral potassium chloride (NDC 40032-917-01) ordered on 2023-11-13T10:15:00+00:00", "Follow-up serum potassium level ordered for 2023-11-14T08:00:00+00:00"])
  _artifact:_ `tasks/task9_3.json`
- **`task10_3`** — task10 · 1 write(s) landed
  > FINISH([-1, "No HbA1C value found in chart. New HbA1C lab test ordered on 2023-11-13T10:15:00+00:00 due to no previous measurement available."])
  _artifact:_ `tasks/task10_3.json`

### 6.3 Non-conformant FHIR accepted by a permissive server — 7 of 35

<!-- honesty:allow-context -->
These writes landed and scored correct. They would be rejected by a server that validates against the profile. The benchmark's grader does not check conformance, so this is a failure the score cannot see — which is exactly why it belongs in the report.
<!-- /honesty:allow-context -->

- **`task8_1`** — task8 · resource ServiceRequest
  > The property note must be a JSON Array, not an Object (at ServiceRequest)
  _artifact:_ `tasks/task8_1.json`
- **`task8_2`** — task8 · resource ServiceRequest
  > The property note must be a JSON Array, not an Object (at ServiceRequest)
  _artifact:_ `tasks/task8_2.json`
- **`task8_4`** — task8 · resource ServiceRequest
  > The property note must be a JSON Array, not an Object (at ServiceRequest)
  _artifact:_ `tasks/task8_4.json`

### 6.4 Task categories at or near zero — 29 of 30

<!-- honesty:allow-context -->
Categories scoring ≤10%: `task7` (0%), `task9` (0%), `task10` (10%). A near-zero category is qualitatively different from a weak one — it means the task shape is not being handled at all, and the overall average is hiding a cliff.
<!-- /honesty:allow-context -->

- **`task7_1`** — task7 · grader said: answer mismatch
  > FINISH(["The most recent CBG of patient S2823623 is 191.0 mg/dL, measured on 2023-11-13T03:35:00+00:00, with a HIGH interpretation."])
  _artifact:_ `tasks/task7_1.json`
- **`task9_1`** — task9 · grader said: episode ended without FINISH (status=agent_invalid_action)
  > The most recent potassium level for patient S3241217 is 3.6 mmol/L (from 2023-08-01T21:10:00+00:00). This is below the goal of 3.5 mmol/L threshold, so replacement potassium is needed.  Calculation: 3.6 - 3.5 = 0.1 mmol/L below goal, requiring 10 mEq potassium oral repletion.  Now I'll order the rep
  _artifact:_ `tasks/task9_1.json`
- **`task10_1`** — task10 · grader said: HbA1C lab order wrong: 
  > FINISH([-1, "No HbA1C measurement available in chart. New HbA1C lab test has been ordered on 2023-11-13T10:15:00+00:00 due to absence of recent lab results."])
  _artifact:_ `tasks/task10_1.json`

### 6.5 Harness finding: `say_fidelity` — 9 of 100

<!-- honesty:allow-context -->
Recorded by the harness's own integrity checks, independent of whether the task scored correct.
<!-- /honesty:allow-context -->

- **`task10_5`** — task10
  > the agent's reply matched none of GET / POST / FINISH
  _artifact:_ `tasks/task10_5.json`
- **`task3_3`** — task3
  > the agent's reply matched none of GET / POST / FINISH
  _artifact:_ `tasks/task3_3.json`

### Sample cases

Picked deterministically from this run's own artifacts — the best and worst by the benchmark's own score — so re-generating the report shows the same cases rather than reshuffling them.

<!-- honesty:allow-context -->
**✓ `task10_10`** — correct
  - _task:_ What’s the last HbA1C (hemoglobin A1C) value in the chart for patient S6488980 and when was it recorded? If the lab value result date is greater than 1 year old, order a new HbA1C lab test. 
  - _reference:_ [-1]  ·  _agent said:_ [-1]
  > FINISH([-1])
  - _why this one:_ graded correct against live chart state
  - _artifact:_ `tasks/task10_10.json`

**✓ `task1_1`** — correct
  - _task:_ What’s the MRN of the patient with name Peter Stafford and DOB of 1932-12-29? If the patient does not exist, the answer should be "Patient not found"
  - _reference:_ ['S6534835']  ·  _agent said:_ ["S6534835"]
  > FINISH(["S6534835"])
  - _why this one:_ graded correct against live chart state
  - _artifact:_ `tasks/task1_1.json`

**✗ `task10_1`** — incorrect
  - _task:_ What’s the last HbA1C (hemoglobin A1C) value in the chart for patient S6227720 and when was it recorded? If the lab value result date is greater than 1 year old, order a new HbA1C lab test. 
  - _reference:_ —  ·  _agent said:_ [-1, "No HbA1C measurement available in chart. New HbA1C lab test has been ordered on 2023-11-13T10:15:00+00:00 due to absence of recent lab results."]
  > FINISH([-1, "No HbA1C measurement available in chart. New HbA1C lab test has been ordered on 2023-11-13T10:15:00+00:00 due to absence of recent lab results."])
  - _why this one:_ HbA1C lab order wrong: 
  - _artifact:_ `tasks/task10_1.json`

**✗ `task10_2`** — incorrect
  - _task:_ What’s the last HbA1C (hemoglobin A1C) value in the chart for patient S1635224 and when was it recorded? If the lab value result date is greater than 1 year old, order a new HbA1C lab test. 
  - _reference:_ [5.9, '2023-11-09T03:05:00+00:00']  ·  _agent said:_ ["5.9%", "2023-11-09T03:05:00+00:00"]
  > FINISH(["5.9%", "2023-11-09T03:05:00+00:00"])
  - _why this one:_ answer mismatch
  - _artifact:_ `tasks/task10_2.json`

<!-- /honesty:allow-context -->

## 7. Exclusions and what they do to the number

Nothing was excluded: all 100 attempted units produced a gradable result. The headline denominator is the full attempted set.

## 8. Limitations and threats to validity

- **subset not full set** (high) — 100 of 300 published tasks were scored. Every comparison to the leaderboard in this report is a subset estimate, and the subset is the head of the set rather than a random draw, so its sampling error is not the textbook one.
- **sandbox not a hospital** (high) — Writes go to a FHIR sandbox that accepts resources a production EHR would reject — 7 of 35 emitted writes in this run were non-conformant and still scored correct. The success rate is therefore an upper bound on what the same agent would achieve against a validating server.
- **grader scope** (medium) — The deterministic grader checks the answer, not the route: a task can score correct having taken an inefficient or clinically odd path to get there, and can score incorrect on a formatting slip alone.
- **no voice path** (low) — This benchmark has no spoken surface — its actions are structured HTTP strings. Nothing here says anything about the product's voice behaviour, and a voice variant would measure nothing.
- **single run** (medium) — One pass, no repeats. A generative agent's success rate has run-to-run variance that a single pass cannot separate from a real change.

## 9. Reproduction

```bash
uv sync --extra dev
docker run -p 8090:8080 <fhir-sandbox-image>   # MedAgentBench FHIR server
python -m tau2.health.medagent.run --mode brain-parity --limit 100 --write-check execute
python -m tau2.reporting.cli build results/whissle/medagentbench/brain-parity_mab_100
```

| Field | Value |
|---|---|
| WHISSLE_BASE | https://aws-gateway-backend.whissle.ai/bot |
| FHIR_API_BASE | http://localhost:8090/fhir/ |
| harness commit | 86b4475 |
| repo commit at report time | 89f2e02 |

- The subset is the head of the published set — deterministic, no seed needed.
- The FHIR sandbox must be reset between runs, or Action tasks read back writes from a previous run and score correct for the wrong reason.

## Appendix A — raw artifacts

| Path | Present | What it is |
|---|:---:|---|
| `SUMMARY.json` | yes | run-level aggregation, write-integrity ledger |
| `SUMMARY.md` | yes | the adapter's own short summary |
| `tasks/` | yes | 100 per-task records with `diagnostics` |
| `REPORT.md` | yes | this report |
| `report.json` | yes | machine-readable form of this report |

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

---

_MedAgentBench, NEJM AI 2025. Research measurement only._

<!-- generated by tau2.reporting from medagentbench/brain-parity_mab_100; schema tau2.reporting.run_report/v1 -->
