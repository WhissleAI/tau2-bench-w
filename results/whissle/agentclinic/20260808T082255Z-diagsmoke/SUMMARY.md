# AgentClinic — Whissle as the doctor (MedQA)

- **run**: 20260808T082255Z  •  **mode**: text  •  **protocol**: markers  •  **vision**: off
- **doctor**: agent `b7b5863b…` (type `clinical_intake_triage`)  •  **patient/measurement/moderator**: whissle
- **judge provider**: `whissle` (model `default`) — NOT independent (same vendor as the agent under test)
- **cases**: 2 scored of 2 selected (limit=2, sample=head, seed=0); 0 excluded as infra_fail
- **max inferences/case**: 6
- **judge spend**: 11 calls, $0.0005 total (5.5 calls/case, $0.0003/case)

## Scores

| metric | value | note |
|---|---|---|
| **accuracy** (upstream formula) | **100.0%** (2/2) | the number comparable to the paper |
| accuracy when committed | 100.0% (2/2) | refusals removed, not scored as wrong |
| declined to diagnose | 0.0% (0/2) | deliberate product boundary (0 by phrasing, 0 by classifier) |
| + refusals that quoted the marker | 0 | scored as commitments by upstream's substring rule; really refusals |
| declined incl. those | 0.0% (0/2) | |
| no commitment (out of turns) | 0.0% (0/2) | |
| accuracy w/ tolerant moderator | 100.0% | upstream requires the grader to reply exactly `yes` |

Outcomes: correct 2 • incorrect 0 • declined 0 • no_commit 0 • infra_fail 0 (excluded)

Average inferences/case: 5.5 • average tests ordered: 1.5 • cases where only a case-insensitive marker matched: 0

Moderator decode: 0 case(s) needed a retry to produce a bare `yes`/`no`, 0 had a decorated reply normalized, 0 never conformed (those were scored by upstream's strict rule).

## Judge

> Judge independence: this run's simulators and graders were routed through Whissle's own model API (`POST /api/models/chat`). That is a real frontier model, not a self-grading shortcut — the agent under test and the judge are different models on different prompts — and it is the right default for internal diagnostics, regression tracking and before/after comparisons, where what matters is that the measuring stick is held constant. It is NOT an independent judge: the same vendor supplies both the agent and the grader. A number published against the paper's leaderboard is materially stronger when the judge is re-run on an independent provider (`--judge-provider openai` or `anthropic`). Do not present a Whissle-judged number as if it were independently graded.
