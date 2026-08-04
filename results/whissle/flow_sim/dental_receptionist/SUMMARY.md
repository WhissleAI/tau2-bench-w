# Flow-sim summary — `dental_receptionist`

- **run**: 20260804T155940Z
- **sessions**: 10  •  **ended cleanly (flag)**: 0/10  •  **reached end state**: 3/10  •  **task success**: 1/10
- **sessions with HIGH-severity findings**: 2/10

## Seeded run — true-completion checks

- **action tool actually called**: 2/7  ({'book_appointment': '1/3', 'reschedule_appointment': '1/2', 'cancel_appointment': '0/2'})
- **seed health**: no-error 10/10  •  resources tracked 0  •  teardown-failed 0

## Findings by type

| type | count |
|------|-------|
| `stuck_termination` | 8 |
| `premature_termination` | 2 |
| `coverage` | 2 |

## Findings by severity

| severity | count |
|----------|-------|
| high | 2 |
| medium | 8 |
| info | 2 |

## State / transition coverage

- **states visited**: 9/14
- **transitions fired**: 11/24
- **states never entered**: `['send_confirmation', 'reschedule_confirm', 'cancel', 'cancel_do', 'cancel_confirm']`
- **transitions never fired**: `['t4', 't7', 't10', 't12', 't13b', 't13c', 't13e', 't14', 't15', 't15b', 't15c', 't15d', 't15e']`

## Sessions

| task | scenario | turns | ended | task_success | final_state | high | finding types |
|------|----------|-------|-------|--------------|-------------|------|---------------|
| `dental_happy_book` | book | 4 | no | True | `book_confirm` | 0 | stuck_termination×1 |
| `dental_book_specific_time` | book | 4 | no | False | `book_schedule` | 0 | stuck_termination×1 |
| `dental_reschedule` | reschedule | 4 | no | False | `reschedule` | 0 | stuck_termination×1 |
| `dental_reschedule_then_cancel` | reschedule | 12 | no | False | `done` | 0 | premature_termination×1 |
| `dental_cancel` | cancel | 14 | no | False | `book_schedule` | 0 | stuck_termination×1 |
| `dental_cancel_no_reason` | cancel | 12 | no | False | `book_schedule` | 0 | stuck_termination×1 |
| `dental_no_slot` | no-slot | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `dental_hours_only` | just-asking | 1 | no | None | `done` | 1 | stuck_termination×1 |
| `dental_wrong_info` | wrong info | 5 | no | False | `reschedule` | 0 | stuck_termination×1 |
| `dental_emergency` | book | 9 | no | False | `done` | 0 | premature_termination×1 |
