# Flow-sim summary — `dental_receptionist`

- **run**: 20260805T165223Z
- **sessions**: 5  •  **ended cleanly**: 0/5  •  **task success**: 2/5
- **sessions with HIGH-severity findings**: 3/5

## Findings by type

| type | count |
|------|-------|
| `stuck_termination` | 5 |
| `coverage` | 2 |

## Findings by severity

| severity | count |
|----------|-------|
| high | 3 |
| medium | 2 |
| info | 2 |

## State / transition coverage

- **states visited**: 6/14
- **transitions fired**: 5/24
- **states never entered**: `['send_confirmation', 'reschedule_do', 'reschedule_confirm', 'cancel', 'cancel_do', 'cancel_confirm', 'close', 'done']`
- **transitions never fired**: `['t4', 't5', 't7', 't9', 't10', 't11', 't12', 't13', 't13b', 't13c', 't13d', 't13e', 't14', 't15', 't15b', 't15c', 't15d', 't15e', 't16']`

## Sessions

| task | scenario | turns | ended | task_success | final_state | high | finding types |
|------|----------|-------|-------|--------------|-------------|------|---------------|
| `dental_happy_book` | book | 9 | no | True | `None` | 0 | stuck_termination×1 |
| `dental_reschedule` | reschedule | 4 | no | None | `None` | 1 | stuck_termination×1 |
| `dental_cancel` | cancel | 4 | no | True | `None` | 0 | stuck_termination×1 |
| `dental_no_slot` | no-slot | 3 | no | None | `None` | 1 | stuck_termination×1 |
| `dental_emergency` | book | 10 | no | None | `None` | 1 | stuck_termination×1 |
