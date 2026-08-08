# AgentClinic — Whissle as the doctor (MedQA)

- **run**: 20260808T022625Z  •  **mode**: text  •  **protocol**: markers  •  **vision**: off
- **doctor**: agent `b8cc1708…` (type `n/a`)  •  **patient/measurement/moderator**: whissle
- **cases**: 5 scored of 5 selected (limit=5, sample=head, seed=0); 0 excluded as infra_fail
- **max inferences/case**: 20

## Scores

| metric | value | note |
|---|---|---|
| **accuracy** (upstream formula) | **0.0%** (0/5) | the number comparable to the paper |
| accuracy when committed | n/a (0/0) | refusals removed, not scored as wrong |
| declined to diagnose | 100.0% (5/5) | deliberate product boundary (5 by phrasing, 0 by classifier) |
| + refusals that quoted the marker | 0 | scored as commitments by upstream's substring rule; really refusals |
| declined incl. those | 100.0% (5/5) | |
| no commitment (out of turns) | 0.0% (0/5) | |
| accuracy w/ tolerant moderator | 0.0% | upstream requires the grader to reply exactly `yes` |

Outcomes: correct 0 • incorrect 0 • declined 5 • no_commit 0 • infra_fail 0 (excluded)

Average inferences/case: 20.0 • average tests ordered: 0.0 • cases where only a case-insensitive marker matched: 0
