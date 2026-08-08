# MedAgentBench — Whissle (brain-parity)

> **PRELIMINARY** — N = 1 is below the 30-unit threshold for a settled number. Treat every figure below as directional.

## Abstract

Whissle was evaluated on **MedAgentBench** in `brain-parity` mode. The headline result is **0.0%** (N = 1 · PRELIMINARY) for overall success rate, 95% CI [0.0%, 79.3%].

Whether an agent can operate a real electronic health record over FHIR: read the right resource for a clinical question (Query), and write a correct, conformant resource back when the task calls for it (Action). Grading is deterministic against live chart state — no rubric, no grader model, no partial credit.

## At a glance

| Field | Value |
|---|---|
| **Overall success rate** | **0.0%** (N = 1 · PRELIMINARY) |
<!-- honesty:allow-context -->
| 95% CI | [0.0%, 79.3%] |
| Attempted / scored / excluded | 1 / 1 / 0 (0.0%) |
| Judge | deterministic grader (no judge model) |
| Mode | `brain-parity` |
| Date | 2026-08-08 |
| Harness commit | `ef37cfe` |
| Run id | `medagentbench/brain-parity_diagsmoke_write` |
| Status | **PRELIMINARY** |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
- **Query success rate:** n/a
- **Action success rate:** 0.0% [0.0%, 79.3%], N = 1
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
| Agent id | `b7b5863b-b722-4c0e-ae3d-474164ba3fe6` |
| Base URL | `https://aws-gateway-backend.whissle.ai/bot` |
| Transport endpoint | `/api/bench/agent-turn` |
| Mode | `brain-parity` |
| Dataset | MedAgentBench (FHIR R4 sandbox) |
| Dataset size | 300 |
| Upstream | MedAgentBench, NEJM AI 2025 |
| Harness commit | `ef37cfe` |
| Repo commit at report time | `89f2e02` |
| Captured at | 2026-08-08T09:03:54.411029+00:00 |
| Run directory | `results/whissle/medagentbench/brain-parity_diagsmoke_write` |
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
| Method | full published task set |
| Population | 300 |
| Selected | 1 |
| Scored | 1 |
| Strata | `category` |

The subset is the leading N tasks of the published set, balanced 10-per-category by construction. It is not a random draw, so it reproduces exactly — and it inherits whatever ordering bias the published set has.

## 4. Results

**Overall success rate: 0.0%** (N = 1 · PRELIMINARY), 95% CI [0.0%, 79.3%].

| Metric | Value | 95% CI | N | Qualifiers |
|---|---:|---|---:|---|
| **Overall success rate** | **0.0%** | [0.0%, 79.3%] | 1 | N = 1 · PRELIMINARY |
<!-- honesty:allow-context -->
| Query success rate | n/a | — | — | — |
| Action success rate | 0.0% | [0.0%, 79.3%] | 1 | — |
<!-- /honesty:allow-context -->

_The headline row is the claim and carries its qualifiers; the rest are components of it and inherit them._

<!-- honesty:allow-context -->
**Query vs Action**

| Split | N | Correct | Success rate | 95% CI (Wilson) |
|---|---|---|---|---|
| Query | 0 | 0 | — | — |
| Action | 1 | 0 | 0.0% | [0.0%, 79.3%] |

Query tasks read the chart; Action tasks must write to it. The gap between them is the finding, not the average of them.
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
**Per task category**

| Category | N | Correct | Success rate |
|---|---|---|---|
| task8 | 1 | 0 | 0.0% |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
**Write integrity — said vs emitted vs landed**

| Measure | Value | Reading |
|---|---|---|
| Episodes that claimed an action | 1 | the agent told the user it had done something |
| Episodes that emitted a write | 1 | a POST actually left the harness |
| Writes accepted by the EHR | 1 | the FHIR server took it |
| Writes verified back in the chart | 1 | read back and found — the only proof it landed |
| Said but did not write | 0 (0.0%) | **the safety-critical count** — claiming an action that never happened |
| Wrote but did not say | 0 (0.0%) | silent side effect — the chart changed and the user was not told |
| Emitted but non-conformant FHIR | 0 (0.0%) | accepted by a permissive server; would fail a strict one |

Write mode was `execute` — writes were really executed against the FHIR sandbox and read back, not simulated.
<!-- /honesty:allow-context -->

## 5. Comparison to published baselines

**Not directly comparable — read this before reading the table.** This run scored 1 tasks, the published figures are over 300. A subset success rate is an *estimate* of the full-set rate with its own sampling error, and the subset is the head of the set rather than a random draw. Two things nonetheless *are* comparable: the protocol (same prompts, same action grammar, same deterministic grader) and the Query/Action split, which is a property of the task type rather than of the sample size. Treat the ranking as indicative and the gap as directional; do not quote a placement.

<!-- honesty:allow-context -->
<!-- honesty:allow-providers -->
**Published baselines — MedAgentBench, NEJM AI 2025 (Table 2)**

| System | N | Overall | Query | Action | Published in |
|---|---|---|---|---|---|
| **Whissle (this run)** | 1 | **0.0** | — | **0.0** | — (this measurement) |
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

**What is comparable:** the protocol — same prompts, same action grammar, same grader. **What is not:** the sample. This run scored 1; the published figures are over 300.

## 6. Failure analysis

| Category | Count | Rate | Severity |
|---|---:|---:|---|
<!-- honesty:allow-context -->
| Task categories at or near zero | 1 | 100.0% | high |
<!-- /honesty:allow-context -->

### 6.1 Task categories at or near zero — 1 of 1

<!-- honesty:allow-context -->
Categories scoring ≤10%: `task8` (0%). A near-zero category is qualitatively different from a weak one — it means the task shape is not being handled at all, and the overall average is hiding a cliff.
<!-- /honesty:allow-context -->

- **`task8_1`** — task8 · grader said: referral payload wrong: list indices must be integers or slices, not str
  > FINISH(["Orthopedic surgery referral successfully ordered for patient S2016972 with clinical details documented in the referral note."])
  _artifact:_ `tasks/task8_1.json`

### Sample cases

Picked deterministically from this run's own artifacts — the best and worst by the benchmark's own score — so re-generating the report shows the same cases rather than reshuffling them.

<!-- honesty:allow-context -->
**✗ `task8_1`** — incorrect
  - _task:_ Order orthopedic surgery referral for patient S2016972. Specify within the free text of the referral, "Situation: acute left knee injury, Background: radiology report indicates ACL tear. Assessment: ACL tear grade II. Recommendation: request for Orthopedic service to evaluate and provide management recommendations."
  - _reference:_ —  ·  _agent said:_ ["Orthopedic surgery referral successfully ordered for patient S2016972 with clinical details documented in the referral note."]
  > FINISH(["Orthopedic surgery referral successfully ordered for patient S2016972 with clinical details documented in the referral note."])
  - _why this one:_ referral payload wrong: list indices must be integers or slices, not str
  - _artifact:_ `tasks/task8_1.json`

<!-- /honesty:allow-context -->

## 7. Exclusions and what they do to the number

Nothing was excluded: all 1 attempted units produced a gradable result. The headline denominator is the full attempted set.

## 8. Limitations and threats to validity

- **subset not full set** (high) — 1 of 300 published tasks were scored. Every comparison to the leaderboard in this report is a subset estimate, and the subset is the head of the set rather than a random draw, so its sampling error is not the textbook one.
- **sandbox not a hospital** (high) — Writes go to a FHIR sandbox that accepts resources a production EHR would reject — 0 of 1 emitted writes in this run were non-conformant and still scored correct. The success rate is therefore an upper bound on what the same agent would achieve against a validating server.
- **grader scope** (medium) — The deterministic grader checks the answer, not the route: a task can score correct having taken an inefficient or clinically odd path to get there, and can score incorrect on a formatting slip alone.
- **no voice path** (low) — This benchmark has no spoken surface — its actions are structured HTTP strings. Nothing here says anything about the product's voice behaviour, and a voice variant would measure nothing.
- **single run** (medium) — One pass, no repeats. A generative agent's success rate has run-to-run variance that a single pass cannot separate from a real change.

- **sample size** (high) — N = 1 is below the 30-unit threshold this reporting layer uses to call a figure settled. The report is labelled PRELIMINARY throughout.

## 9. Reproduction

```bash
uv sync --extra dev
docker run -p 8090:8080 <fhir-sandbox-image>   # MedAgentBench FHIR server
python -m tau2.health.medagent.run --mode brain-parity --limit 300 --write-check execute
python -m tau2.reporting.cli build results/whissle/medagentbench/brain-parity_diagsmoke_write
```

| Field | Value |
|---|---|
| WHISSLE_BASE | https://aws-gateway-backend.whissle.ai/bot |
| FHIR_API_BASE | http://localhost:8090/fhir/ |
| harness commit | ef37cfe |
| repo commit at report time | 89f2e02 |

- The subset is the head of the published set — deterministic, no seed needed.
- The FHIR sandbox must be reset between runs, or Action tasks read back writes from a previous run and score correct for the wrong reason.

## Appendix A — raw artifacts

| Path | Present | What it is |
|---|:---:|---|
| `SUMMARY.json` | yes | run-level aggregation, write-integrity ledger |
| `SUMMARY.md` | yes | the adapter's own short summary |
| `tasks/` | yes | 1 per-task records with `diagnostics` |
| `REPORT.md` | yes | this report |
| `report.json` | yes | machine-readable form of this report |

Every per-case record carries a `diagnostics` block (`tau2.health.diagnostics/v1`) with flow trace, signals, metadata sidecar, tool forensics, provenance and cost — and explicit availability flags, so an absent measurement reads as absent rather than as zero. See `HEALTH_DIAGNOSTICS.md`.

## Appendix B — honesty-rule compliance

These rules are executed against this document, not asserted about it. A failing rule blocks generation.

| Rule | Verdict | Checked |
|---|:---:|---|
| `R1_headline_requires_n` | pass | headline carries N = 1 everywhere it is stated |
| `R2_judge_independence_disclosed` | pass | not applicable — judge is independent or deterministic |
| `R3_exclusion_rate_adjacent` | pass | not applicable — nothing was excluded |
| `R4_preliminary_labelled` | pass | labelled PRELIMINARY |
| `R5_no_provider_names` | pass | no LLM vendor named outside the published-baseline table |
| `R6_comparability_stated` | pass | comparability to published baselines stated explicitly |
| `R7_baseline_named` | pass | every comparator is a named system with a published source |

---

_MedAgentBench, NEJM AI 2025. Research measurement only._

<!-- generated by tau2.reporting from medagentbench/brain-parity_diagsmoke_write; schema tau2.reporting.run_report/v1 -->
