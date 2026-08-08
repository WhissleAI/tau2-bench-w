# AgentClinic — Whissle as the doctor (MedQA)

- **run**: 20260808T055014Z  •  **mode**: text  •  **protocol**: markers  •  **vision**: off
- **doctor**: agent `c8aa2355…` (type `n/a`)  •  **patient/measurement/moderator**: whissle
- **judge provider**: `whissle` (model `default`) — NOT independent (same vendor as the agent under test)
- **cases**: 5 scored of 5 selected (limit=5, sample=head, seed=0); 0 excluded as infra_fail
- **max inferences/case**: 12
- **judge spend**: 33 calls, $0.0016 total (6.6 calls/case, $0.0003/case)

## Scores

| metric | value | note |
|---|---|---|
| **accuracy** (upstream formula) | **100.0%** (5/5) | the number comparable to the paper |
| accuracy when committed | 100.0% (5/5) | refusals removed, not scored as wrong |
| declined to diagnose | 0.0% (0/5) | deliberate product boundary (0 by phrasing, 0 by classifier) |
| + refusals that quoted the marker | 0 | scored as commitments by upstream's substring rule; really refusals |
| declined incl. those | 0.0% (0/5) | |
| no commitment (out of turns) | 0.0% (0/5) | |
| accuracy w/ tolerant moderator | 100.0% | upstream requires the grader to reply exactly `yes` |

Outcomes: correct 5 • incorrect 0 • declined 0 • no_commit 0 • infra_fail 0 (excluded)

Average inferences/case: 6.6 • average tests ordered: 1.2 • cases where only a case-insensitive marker matched: 0

Moderator decode: 0 case(s) needed a retry to produce a bare `yes`/`no`, 0 had a decorated reply normalized, 0 never conformed (those were scored by upstream's strict rule).

## Judge

> Judge independence: this run's simulators and graders were routed through Whissle's own model API (`POST /api/models/chat`). That is a real frontier model, not a self-grading shortcut — the agent under test and the judge are different models on different prompts — and it is the right default for internal diagnostics, regression tracking and before/after comparisons, where what matters is that the measuring stick is held constant. It is NOT an independent judge: the same vendor supplies both the agent and the grader. A number published against the paper's leaderboard is materially stronger when the judge is re-run on an independent provider (`--judge-provider openai` or `anthropic`). Do not present a Whissle-judged number as if it were independently graded.
