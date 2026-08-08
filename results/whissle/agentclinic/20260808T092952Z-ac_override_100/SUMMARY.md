# AgentClinic — Whissle as the doctor (MedQA)

- **run**: 20260808T092952Z  •  **mode**: text  •  **protocol**: markers  •  **vision**: off
- **doctor**: agent `c8aa2355…` (type `n/a`)  •  **patient/measurement/moderator**: whissle
- **judge provider**: `whissle` (model `default`) — NOT independent (same vendor as the agent under test)
- **cases**: 100 scored of 100 selected (limit=100, sample=head, seed=42); 0 excluded as infra_fail
- **max inferences/case**: 20
- **judge spend**: 1183 calls, $0.0643 total (11.8 calls/case, $0.0006/case)

## Scores

| metric | value | note |
|---|---|---|
| **accuracy** (upstream formula) | **75.0%** (75/100) | the number comparable to the paper |
| accuracy when committed | 83.3% (75/90) | refusals removed, not scored as wrong |
| declined to diagnose | 0.0% (0/100) | deliberate product boundary (0 by phrasing, 0 by classifier) |
| + refusals that quoted the marker | 0 | scored as commitments by upstream's substring rule; really refusals |
| declined incl. those | 0.0% (0/100) | |
| no commitment (out of turns) | 10.0% (10/100) | |
| accuracy w/ tolerant moderator | 75.0% | upstream requires the grader to reply exactly `yes` |

Outcomes: correct 75 • incorrect 15 • declined 0 • no_commit 10 • infra_fail 0 (excluded)

Average inferences/case: 11.73 • average tests ordered: 2.82 • cases where only a case-insensitive marker matched: 0

Moderator decode: 0 case(s) needed a retry to produce a bare `yes`/`no`, 0 had a decorated reply normalized, 0 never conformed (those were scored by upstream's strict rule).

## Judge

> Judge independence: this run's simulators and graders were routed through Whissle's own model API (`POST /api/models/chat`). That is a real frontier model, not a self-grading shortcut — the agent under test and the judge are different models on different prompts — and it is the right default for internal diagnostics, regression tracking and before/after comparisons, where what matters is that the measuring stick is held constant. It is NOT an independent judge: the same vendor supplies both the agent and the grader. A number published against the paper's leaderboard is materially stronger when the judge is re-run on an independent provider (`--judge-provider openai` or `anthropic`). Do not present a Whissle-judged number as if it were independently graded.
