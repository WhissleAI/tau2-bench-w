# MedAgentBench — Whissle (brain-parity)

_2026-08-08T18:57:47.686968+00:00_

**N attempted 25 · scored 25 · infra_fail 0 (excluded)**

| run | |
|---|---|
| base | `https://aws-gateway-backend.whissle.ai/bot` |
| agent_id | `c2761ec7-d3f7-4bd7-b68c-40a87f1b1ab3` |
| model | `claude-opus-5` |
| system_mode | `neutral` |
| max_tokens | `1024` |
| endpoint | `/api/bench/agent-turn` |
| fhir_api_base | `http://localhost:8090/fhir/` |
| write_check | `execute` |
| max_round | `8` |
| grader | `builtin` |
| filters | `{'limit': 25, 'tasks': None, 'categories': None}` |

## Success rate

| slice | n | correct | SR % |
|---|---:|---:|---:|
| Overall | 25 | 18 | 72.0 |
| Query | 13 | 9 | 69.23 |
| Action | 12 | 9 | 75.0 |

### Per category

| category | kind | n | correct | SR % |
|---|---|---:|---:|---:|
| task1 | query | 3 | 0 | 0.0 |
| task2 | query | 3 | 3 | 100.0 |
| task3 | action | 3 | 3 | 100.0 |
| task4 | query | 3 | 3 | 100.0 |
| task5 | action | 3 | 2 | 66.67 |
| task6 | query | 2 | 2 | 100.0 |
| task7 | query | 2 | 1 | 50.0 |
| task8 | action | 2 | 2 | 100.0 |
| task9 | action | 2 | 0 | 0.0 |
| task10 | action | 2 | 2 | 100.0 |

## Write integrity — said vs. actually wrote

> MedAgentBench never sends the agent's POST to the EHR: it replies "POST request accepted and executed successfully" and grades the payload out of the transcript. The published Action SR therefore measures the *intent* to write. These rows separate the three events.

- write-check mode: `execute`
- action episodes: 12
- claimed an action: 0 · emitted a write: 6

| signal | n | rate % | tasks |
|---|---:|---:|---|
| said it ordered, never wrote | 0 | 0.0 | — |
| wrote, never said | 6 | 50.0 | task3_1, task8_1, task10_1, task3_2, task8_2, task3_3 |
| wrote a payload the EHR refused | 0 | 0.0 | — |
| wrote a payload that fails strict FHIR R4 validation | 3 | 25.0 | task8_1, task10_1, task8_2 |

- writes emitted 6 · accepted by EHR 6 · verified in chart 6 · non-conformant 3

> 'Refused' and 'fails validation' are different questions and they do disagree: HAPI's create endpoint is more lenient than its `$validate` operation, so a payload can be stored yet still be invalid FHIR R4.

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
