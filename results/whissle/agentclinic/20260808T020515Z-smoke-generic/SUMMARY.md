# AgentClinic — Whissle as the doctor (MedQA)

- **run**: 20260808T020515Z  •  **mode**: text  •  **protocol**: markers  •  **vision**: off
- **doctor**: agent `135a8daf…` (type `n/a`)  •  **patient/measurement/moderator**: whissle
- **cases**: 5 scored of 5 selected (limit=5, sample=head, seed=0); 0 excluded as infra_fail
- **max inferences/case**: 20

## Scores

| metric | value | note |
|---|---|---|
| **accuracy** (upstream formula) | **100.0%** (5/5) | the number comparable to the paper |
| accuracy when committed | 100.0% (5/5) | refusals removed, not scored as wrong |
| declined to diagnose | 0.0% (0/5) | deliberate product boundary |
| no commitment (out of turns) | 0.0% (0/5) | |
| accuracy w/ tolerant moderator | 100.0% | upstream requires the grader to reply exactly `yes` |

Outcomes: correct 5 • incorrect 0 • declined 0 • no_commit 0 • infra_fail 0 (excluded)

Average inferences/case: 8.0 • average tests ordered: 1.6 • cases where only a case-insensitive marker matched: 0
