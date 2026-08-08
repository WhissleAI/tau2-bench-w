# PatientAgentBench — Whissle

**Mode:** `harness_tools` — PatientAgentBench's own ReAct harness, system prompt and 15 sandbox tools, with only the model swapped for the Whissle agent brain. Directly comparable to the paper's published baselines.

**N = 6 scored** (of 6 attempted; 0 excluded)

## Results (paper Table 4 shape)

| Agent | Aggregate | Task Completion | Clinical Safety | Workflow Accuracy | Triage Quality | Clinical Helpfulness | Conversational Quality |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Whissle | 3.59 | 4.00 [83%] | 3.17 [83%] | 3.33 [83%] | 3.50 [100%] | 3.67 [100%] | 4.50 [100%] |

Each cell is `mean [pass%]`, pass = score >= 3, scale 1-5.

## Per-dimension detail

| Dimension | Weight | N | Mean | 95% CI | Pass rate | 95% CI (Wilson) |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Task Completion | 1.0 | 6 | 4.00 | [3.12, 4.88] | 83.3% | [43.6%, 97.0%] |
| Clinical Safety | 2.0 | 6 | 3.17 | [2.56, 3.77] | 83.3% | [43.6%, 97.0%] |
| Workflow Accuracy | 1.6 | 6 | 3.33 | [2.68, 3.99] | 83.3% | [43.6%, 97.0%] |
| Triage Quality | 1.4 | 6 | 3.50 | [2.83, 4.17] | 100.0% | [61.0%, 100.0%] |
| Clinical Helpfulness | 1.4 | 6 | 3.67 | [3.25, 4.08] | 100.0% | [61.0%, 100.0%] |
| Conversational Quality | 0.9 | 6 | 4.50 | [4.06, 4.94] | 100.0% | [61.0%, 100.0%] |

**Weighted aggregate: 3.59** (95% CI [3.26, 3.92], N = 6); weights sum to 8.3.

## Excluded sessions

- `infra_fail`: **0** — transport/pipeline faults (5xx, timeout, dead voice channel). These measured our uptime, not clinical quality, and are excluded from every mean above.
- `agent_error`: **0** — the conversation ran but produced no usable evaluation.

## Sampling

Seeded stratified sample: **6 of 20** cases, seed `42`, strata `task_type x severity_level`.

**Task Type** (population % vs sample %)

| Value | Population | Sample | Sample N |
|---|:---:|:---:|:---:|
| health_concern_emergency_symptoms | 15.0% | 16.7% | 1 |
| health_concern_lifestyle_management | 5.0% | 16.7% | 1 |
| health_concern_mental_health_crisis | 20.0% | 33.3% | 2 |
| health_concern_pain_management | 15.0% | 16.7% | 1 |
| health_concern_preventive_care | 5.0% | 0.0% | 0 |
| health_concern_symptom_assessment | 10.0% | 0.0% | 0 |
| health_concern_treatment_options | 5.0% | 0.0% | 0 |
| medication_dosage_question | 5.0% | 0.0% | 0 |
| medication_medication_interaction | 10.0% | 16.7% | 1 |
| provider_access_referral_request | 5.0% | 0.0% | 0 |
| provider_access_schedule | 5.0% | 0.0% | 0 |

**Severity Level** (population % vs sample %)

| Value | Population | Sample | Sample N |
|---|:---:|:---:|:---:|
| mild | 40.0% | 33.3% | 2 |
| moderate | 35.0% | 33.3% | 2 |
| severe | 25.0% | 33.3% | 2 |

## Provenance

- **run_dir**: `/tmp/pab_smoke/smoke6/sampled_cases_20260807_193122`
- **generated_at**: `2026-08-08T02:35:01+00:00`
- **whissle_base**: `https://aws-gateway-backend.whissle.ai/bot`
- **whissle_agent_id**: `f12a459e…`

---

PatientAgentBench is CC-BY-NC-4.0 and its authors state it is "not a clinical certification or a deployment-readiness assessment". These numbers are a research measurement, not a safety claim.
