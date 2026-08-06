# Flow-sim summary — `headache_enrollment`

- **run**: 20260805T161738Z
- **sessions**: 10  •  **ended cleanly**: 3/10  •  **task success**: 5/10
- **sessions with HIGH-severity findings**: 2/10

## Findings by type

| type | count |
|------|-------|
| `stuck_termination` | 7 |
| `premature_termination` | 1 |
| `coverage` | 1 |

## Findings by severity

| severity | count |
|----------|-------|
| high | 2 |
| medium | 6 |
| info | 1 |

## State / transition coverage

- **states visited**: 10/10
- **transitions fired**: 10/14
- **transitions never fired**: `['t2', 't4', 't8', 't10']`

## Sessions

| task | scenario | turns | ended | task_success | final_state | high | finding types |
|------|----------|-------|-------|--------------|-------------|------|---------------|
| `hx_happy_full` | full_intake | 2 | no | None | `None` | 1 | stuck_termination×1 |
| `hx_red_flag_urgent` | urgent_escalation | 2 | yes | True | `None` | 0 |  |
| `hx_skips_topics` | skips | 7 | yes | True | `None` | 0 |  |
| `hx_unsure` | unsure | 5 | no | False | `None` | 0 | stuck_termination×1 |
| `hx_out_of_order` | out_of_order | 5 | no | True | `None` | 0 | stuck_termination×1 |
| `hx_migraine_classic` | migraine | 13 | yes | False | `None` | 0 | premature_termination×1 |
| `hx_cluster_signs` | autonomic | 9 | no | True | `None` | 0 | stuck_termination×1 |
| `hx_keep_it_quick` | time_pressured | 5 | no | False | `None` | 0 | stuck_termination×1 |
| `hx_hormonal` | hormonal | 9 | no | True | `None` | 0 | stuck_termination×1 |
| `hx_med_overuse` | medication | 8 | no | None | `None` | 1 | stuck_termination×1 |
