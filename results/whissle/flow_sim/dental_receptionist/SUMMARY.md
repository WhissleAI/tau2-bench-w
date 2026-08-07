# Flow-sim summary — `dental_receptionist`

- **run**: 20260807T011833Z
- **sessions**: 11  •  **ended cleanly**: 4/9  •  **task success**: 5/9
- **sessions with HIGH-severity findings**: 2/9
- **infra failures (excluded from flow metrics)**: 2/11

## Findings by type

| type | count |
|------|-------|
| `stuck_termination` | 3 |
| `infra_fail` | 2 |
| `agent_no_close` | 2 |
| `coverage` | 2 |
| `stuck_loop` | 1 |
| `premature_termination` | 1 |

## Findings by severity

| severity | count |
|----------|-------|
| high | 4 |
| medium | 5 |
| info | 2 |

## State / transition coverage

- **states visited**: 12/14
- **transitions fired**: 15/24
- **states never entered**: `['reschedule_confirm', 'cancel_confirm']`
- **transitions never fired**: `['t7', 't11', 't13b', 't13c', 't13d', 't13e', 't14', 't15c', 't15e']`

## Sessions

| task | scenario | turns | ended | task_success | final_state | high | finding types |
|------|----------|-------|-------|--------------|-------------|------|---------------|
| `dental_happy_book` | book | 0 | no | None | `None` | 1 | infra_fail×1 |
| `dental_book_specific_time` | book | 8 | yes | True | `None` | 0 |  |
| `dental_reschedule` | reschedule | 0 | no | None | `None` | 1 | infra_fail×1 |
| `dental_reschedule_then_cancel` | reschedule | 8 | no | True | `None` | 0 | stuck_termination×1 |
| `dental_cancel` | cancel | 15 | yes | False | `None` | 0 | stuck_loop×1, premature_termination×1 |
| `dental_cancel_no_reason` | cancel | 8 | no | False | `None` | 0 | stuck_termination×1 |
| `dental_no_slot` | no-slot | 9 | no | True | `None` | 0 | stuck_termination×1 |
| `dental_hours_only` | just-asking | 2 | yes | True | `None` | 0 |  |
| `dental_wrong_info` | wrong info | 10 | no | False | `None` | 1 | agent_no_close×1 |
| `dental_emergency` | book | 7 | no | False | `None` | 1 | agent_no_close×1 |
| `dental_message_for_staff` | just-asking | 4 | yes | True | `None` | 0 |  |
