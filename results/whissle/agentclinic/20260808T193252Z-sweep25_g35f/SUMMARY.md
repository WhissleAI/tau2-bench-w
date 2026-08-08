# AgentClinic — Whissle as the doctor (MedQA)

- **run**: 20260808T193252Z  •  **mode**: text  •  **protocol**: markers  •  **vision**: off
- **doctor**: agent `c2761ec7…` (type `n/a`)  •  **patient/measurement/moderator**: anthropic
- **judge provider**: `anthropic` (model `claude-sonnet-4-5`) — INDEPENDENT of the agent vendor
- **cases**: 25 scored of 25 selected (limit=25, sample=head, seed=42); 0 excluded as infra_fail
- **max inferences/case**: 20
- **judge spend**: 217 calls, $0.6841 total (8.7 calls/case, $0.0274/case)

## Scores

| metric | value | note |
|---|---|---|
| **accuracy** (upstream formula) | **84.0%** (21/25) | the number comparable to the paper |
| accuracy when committed | 84.0% (21/25) | refusals removed, not scored as wrong |
| declined to diagnose | 0.0% (0/25) | deliberate product boundary (0 by phrasing, 0 by classifier) |
| + refusals that quoted the marker | 0 | scored as commitments by upstream's substring rule; really refusals |
| declined incl. those | 0.0% (0/25) | |
| no commitment (out of turns) | 0.0% (0/25) | |
| accuracy w/ tolerant moderator | 84.0% | upstream requires the grader to reply exactly `yes` |

Outcomes: correct 21 • incorrect 4 • declined 0 • no_commit 0 • infra_fail 0 (excluded)

Average inferences/case: 8.68 • average tests ordered: 2.2 • cases where only a case-insensitive marker matched: 0

Moderator decode: 0 case(s) needed a retry to produce a bare `yes`/`no`, 0 had a decorated reply normalized, 0 never conformed (those were scored by upstream's strict rule).

## Judge

> Judge independence: this run's simulators and graders were routed through an external provider, independent of the agent under test. This is the stronger footing for a published comparison against the paper's numbers.
