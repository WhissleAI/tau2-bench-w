# Flow-sim summary — `appointment_scheduling`

- **run**: 20260805T181557Z
- **sessions**: 5  •  **ended cleanly**: 2/5  •  **task success**: 1/5
- **sessions with HIGH-severity findings**: 1/5

## Findings by type

| type | count |
|------|-------|
| `stuck_termination` | 3 |
| `premature_termination` | 2 |
| `coverage` | 2 |

## Findings by severity

| severity | count |
|----------|-------|
| high | 1 |
| medium | 4 |
| info | 2 |

## State / transition coverage

- **states visited**: 7/10
- **transitions fired**: 6/13
- **states never entered**: `['confirm_booked', 'send_confirmation', 'confirm_change']`
- **transitions never fired**: `['t_slot_transfer', 't_booked', 't_book_fail', 't_book_flag', 't_sms_done', 't_change_ok', 't_change_confirmed']`

## Sessions

| task | scenario | turns | ended | task_success | final_state | high | finding types |
|------|----------|-------|-------|--------------|-------------|------|---------------|
| `appt_new` | new | 3 | no | None | `None` | 1 | stuck_termination×1 |
| `appt_reschedule` | reschedule | 8 | yes | False | `None` | 0 | premature_termination×1 |
| `appt_cancel` | cancel | 7 | yes | False | `None` | 0 | premature_termination×1 |
| `appt_out_of_hours` | out-of-hours | 9 | no | False | `None` | 0 | stuck_termination×1 |
| `appt_double_booking` | new | 7 | no | True | `None` | 0 | stuck_termination×1 |
