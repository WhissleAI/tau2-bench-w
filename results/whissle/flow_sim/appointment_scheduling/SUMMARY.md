# Flow-sim summary — `appointment_scheduling`

- **run**: 20260806T224843Z
- **sessions**: 11  •  **ended cleanly**: 3/11  •  **task success**: 5/11
- **sessions with HIGH-severity findings**: 7/11
- **infra failures (excluded from flow metrics)**: 0/11

## Findings by type

| type | count |
|------|-------|
| `agent_no_close` | 7 |
| `premature_termination` | 3 |
| `coverage` | 2 |
| `stuck_termination` | 1 |

## Findings by severity

| severity | count |
|----------|-------|
| high | 7 |
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
| `appt_new` | new | 9 | no | True | `None` | 1 | agent_no_close×1 |
| `appt_new_specific` | new | 7 | no | True | `None` | 1 | agent_no_close×1 |
| `appt_reschedule` | reschedule | 6 | no | False | `None` | 1 | agent_no_close×1 |
| `appt_reschedule_earlier` | reschedule | 11 | yes | False | `None` | 0 | premature_termination×1 |
| `appt_cancel` | cancel | 12 | yes | False | `None` | 0 | premature_termination×1 |
| `appt_cancel_and_rebook` | reschedule | 11 | yes | False | `None` | 0 | premature_termination×1 |
| `appt_out_of_hours` | out-of-hours | 8 | no | True | `None` | 1 | agent_no_close×1 |
| `appt_just_hours` | out-of-hours | 3 | no | True | `None` | 1 | agent_no_close×1 |
| `appt_double_booking` | new | 4 | no | False | `None` | 0 | stuck_termination×1 |
| `appt_wrong_details` | reschedule | 8 | no | False | `None` | 1 | agent_no_close×1 |
| `appt_group` | new | 9 | no | True | `None` | 1 | agent_no_close×1 |
