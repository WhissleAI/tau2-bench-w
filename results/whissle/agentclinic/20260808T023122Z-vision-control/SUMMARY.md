# AgentClinic — Whissle as the doctor (NEJM)

- **run**: 20260808T023122Z  •  **mode**: text  •  **protocol**: markers  •  **vision**: off
- **doctor**: agent `135a8daf…` (type `n/a`)  •  **patient/measurement/moderator**: whissle
- **cases**: 3 scored of 3 selected (limit=3, sample=head, seed=0); 0 excluded as infra_fail
- **max inferences/case**: 20

## Scores

| metric | value | note |
|---|---|---|
| **accuracy** (upstream formula) | **66.7%** (2/3) | the number comparable to the paper |
| accuracy when committed | 66.7% (2/3) | refusals removed, not scored as wrong |
| declined to diagnose | 0.0% (0/3) | deliberate product boundary (0 by phrasing, 0 by classifier) |
| + refusals that quoted the marker | 0 | scored as commitments by upstream's substring rule; really refusals |
| declined incl. those | 0.0% (0/3) | |
| no commitment (out of turns) | 0.0% (0/3) | |
| accuracy w/ tolerant moderator | 66.7% | upstream requires the grader to reply exactly `yes` |

Outcomes: correct 2 • incorrect 1 • declined 0 • no_commit 0 • infra_fail 0 (excluded)

Average inferences/case: 8.67 • average tests ordered: 2.0 • cases where only a case-insensitive marker matched: 0
