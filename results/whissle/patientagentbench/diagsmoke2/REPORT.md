# PatientAgentBench — Whissle

**Mode:** `harness_tools` — PatientAgentBench's own ReAct harness, system prompt and 15 sandbox tools, with only the model swapped for the Whissle agent brain. Directly comparable to the paper's published baselines.

**N = 2 scored** (of 2 attempted; 0 excluded)

**Judge:** `whissle` — NOT independent of the agent's vendor; see the Judge section below.

## Results (paper Table 4 shape)

| Agent | Aggregate | Task Completion | Clinical Safety | Workflow Accuracy | Triage Quality | Clinical Helpfulness | Conversational Quality |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Whissle | 3.62 | 4.00 [100%] | 4.00 [100%] | 2.50 [50%] | 3.50 [100%] | 4.00 [100%] | 4.00 [100%] |

Each cell is `mean [pass%]`, pass = score >= 3, scale 1-5.

## Per-dimension detail

| Dimension | Weight | N | Mean | 95% CI | Pass rate | 95% CI (Wilson) |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Task Completion | 1.0 | 2 | 4.00 | [4.0, 4.0] | 100.0% | [34.2%, 100.0%] |
| Clinical Safety | 2.0 | 2 | 4.00 | [4.0, 4.0] | 100.0% | [34.2%, 100.0%] |
| Workflow Accuracy | 1.6 | 2 | 2.50 | [1.52, 3.48] | 50.0% | [9.5%, 90.5%] |
| Triage Quality | 1.4 | 2 | 3.50 | [2.52, 4.48] | 100.0% | [34.2%, 100.0%] |
| Clinical Helpfulness | 1.4 | 2 | 4.00 | [2.04, 5.96] | 100.0% | [34.2%, 100.0%] |
| Conversational Quality | 0.9 | 2 | 4.00 | [4.0, 4.0] | 100.0% | [34.2%, 100.0%] |

**Weighted aggregate: 3.62** (95% CI [3.32, 3.93], N = 2); weights sum to 8.3.

## Excluded sessions

- `infra_fail`: **0** — transport/pipeline faults (5xx, timeout, dead voice channel). These measured our uptime, not clinical quality, and are excluded from every mean above.
- `agent_error`: **0** — the conversation ran but produced no usable evaluation.

## Sampling

Seeded stratified sample: **2 of 20** cases, seed `42`, strata `task_type x severity_level`.

**Task Type** (population % vs sample %)

| Value | Population | Sample | Sample N |
|---|:---:|:---:|:---:|
| health_concern_emergency_symptoms | 15.0% | 50.0% | 1 |
| health_concern_lifestyle_management | 5.0% | 0.0% | 0 |
| health_concern_mental_health_crisis | 20.0% | 0.0% | 0 |
| health_concern_pain_management | 15.0% | 50.0% | 1 |
| health_concern_preventive_care | 5.0% | 0.0% | 0 |
| health_concern_symptom_assessment | 10.0% | 0.0% | 0 |
| health_concern_treatment_options | 5.0% | 0.0% | 0 |
| medication_dosage_question | 5.0% | 0.0% | 0 |
| medication_medication_interaction | 10.0% | 0.0% | 0 |
| provider_access_referral_request | 5.0% | 0.0% | 0 |
| provider_access_schedule | 5.0% | 0.0% | 0 |

**Severity Level** (population % vs sample %)

| Value | Population | Sample | Sample N |
|---|:---:|:---:|:---:|
| mild | 40.0% | 50.0% | 1 |
| moderate | 35.0% | 50.0% | 1 |
| severe | 25.0% | 0.0% | 0 |

## Judge

- **provider**: `whissle` (`whissle:/api/models/chat`)
- **evaluator model(s)**: `default`, K = 1 (the paper uses K=2)
- **patient simulator**: `whissle-patient`  •  **sandbox**: `whissle-sandbox`
- **independent of the agent's vendor**: **NO**
- **judge spend**: 21 calls, $0.0054 (10.5/case, $0.0027/case)

> Judge independence: this run's simulators and graders were routed through Whissle's own model API (`POST /api/models/chat`). That is a real frontier model, not a self-grading shortcut — the agent under test and the judge are different models on different prompts — and it is the right default for internal diagnostics, regression tracking and before/after comparisons, where what matters is that the measuring stick is held constant. It is NOT an independent judge: the same vendor supplies both the agent and the grader. A number published against the paper's leaderboard is materially stronger when the judge is re-run on an independent provider (`--judge-provider openai` or `anthropic`). Do not present a Whissle-judged number as if it were independently graded.

## Provenance

- **run_dir**: `output/diagsmoke2/sampled_cases_20260808_020815`
- **generated_at**: `2026-08-08T09:09:17+00:00`
- **whissle_base**: `https://aws-gateway-backend.whissle.ai/bot`
- **whissle_agent_id**: `c2761ec7…`

---

PatientAgentBench is CC-BY-NC-4.0 and its authors state it is "not a clinical certification or a deployment-readiness assessment". These numbers are a research measurement, not a safety claim.
