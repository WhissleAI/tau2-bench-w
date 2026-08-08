# MedAgentBench — Whissle (brain-parity)

_2026-08-08T09:36:34.307151+00:00_

**N attempted 100 · scored 100 · infra_fail 0 (excluded)**

| run | |
|---|---|
| base | `https://aws-gateway-backend.whissle.ai/bot` |
| agent_id | `f52dfc18-7bde-4f10-8674-f646b358acf3` |
| model | `(agent default)` |
| system_mode | `neutral` |
| max_tokens | `1024` |
| endpoint | `/api/bench/agent-turn` |
| fhir_api_base | `http://localhost:8090/fhir/` |
| write_check | `execute` |
| max_round | `8` |
| grader | `builtin` |
| filters | `{'limit': 100, 'tasks': None, 'categories': None}` |

## Success rate

| slice | n | correct | SR % |
|---|---:|---:|---:|
| Overall | 100 | 54 | 54.0 |
| Query | 50 | 34 | 68.0 |
| Action | 50 | 20 | 40.0 |

### Per category

| category | kind | n | correct | SR % |
|---|---|---:|---:|---:|
| task1 | query | 10 | 10 | 100.0 |
| task2 | query | 10 | 9 | 90.0 |
| task3 | action | 10 | 6 | 60.0 |
| task4 | query | 10 | 9 | 90.0 |
| task5 | action | 10 | 6 | 60.0 |
| task6 | query | 10 | 6 | 60.0 |
| task7 | query | 10 | 0 | 0.0 |
| task8 | action | 10 | 7 | 70.0 |
| task9 | action | 10 | 0 | 0.0 |
| task10 | action | 10 | 1 | 10.0 |

## Write integrity — said vs. actually wrote

> MedAgentBench never sends the agent's POST to the EHR: it replies "POST request accepted and executed successfully" and grades the payload out of the transcript. The published Action SR therefore measures the *intent* to write. These rows separate the three events.

- write-check mode: `execute`
- action episodes: 50
- claimed an action: 19 · emitted a write: 29

| signal | n | rate % | tasks |
|---|---:|---:|---|
| said it ordered, never wrote | 0 | 0.0 | — |
| wrote, never said | 10 | 20.0 | task9_2, task9_3, task10_3, task10_4, task10_6, task9_7, task9_8, task10_8, task10_9, task10_10 |
| wrote a payload the EHR refused | 0 | 0.0 | — |
| wrote a payload that fails strict FHIR R4 validation | 7 | 14.0 | task8_1, task8_2, task8_4, task8_5, task8_6, task8_7, task8_8 |

- writes emitted 35 · accepted by EHR 35 · verified in chart 35 · non-conformant 7

> 'Refused' and 'fails validation' are different questions and they do disagree: HAPI's create endpoint is more lenient than its `$validate` operation, so a payload can be stored yet still be invalid FHIR R4.

## Findings

| type | n |
|---|---:|
| `say_fidelity` | 9 |

## Published baselines (full 300-task set)

| model | overall | query | action |
|---|---:|---:|---:|
| Claude 3.5 Sonnet v2 | 69.67 | 85.33 | 54.0 |
| GPT-4o | 64.0 | — | — |
| DeepSeek-V3 | 62.67 | — | — |
| Gemini-1.5 Pro | 62.0 | — | — |
| GPT-4o-mini | 56.33 | — | — |
| o3-mini | 51.67 | — | — |
| Qwen2.5 | 51.33 | — | — |
| Llama 3.3 | 46.33 | — | — |
| Gemini 2.0 Flash | 38.33 | — | — |
| Gemma2 | 19.33 | — | — |
| Mistral v0.3 | 4.0 | — | — |

> Comparable to the published table only when n_scored == 300 and mode == 'brain-parity'. A --limit run is a subset estimate; always quote N.
