# Flow-sim summary — `appointment_scheduling`

- **run**: 20260804T155944Z
- **sessions**: 10  •  **ended cleanly (flag)**: 0/10  •  **reached end state**: 7/10  •  **task success**: 1/10
- **sessions with HIGH-severity findings**: 2/10

## Seeded run — true-completion checks

- **action tool actually called**: 2/8  ({'book_appointment': '0/3', 'reschedule_appointment': '1/4', 'cancel_appointment': '1/1'})
- **seed health**: no-error 10/10  •  resources tracked 0  •  teardown-failed 0

## Findings by type

| type | count |
|------|-------|
| `premature_termination` | 6 |
| `stuck_termination` | 4 |
| `coverage` | 2 |

## Findings by severity

| severity | count |
|----------|-------|
| high | 2 |
| medium | 8 |
| info | 2 |

## State / transition coverage

- **states visited**: 6/10
- **transitions fired**: 6/13
- **states never entered**: `['book', 'confirm_booked', 'send_confirmation', 'confirm_change']`
- **transitions never fired**: `['t_slot_ok', 't_booked', 't_book_fail', 't_book_flag', 't_sms_done', 't_change_ok', 't_change_confirmed']`

## Sessions

| task | scenario | turns | ended | task_success | final_state | high | finding types |
|------|----------|-------|-------|--------------|-------------|------|---------------|
| `appt_new` | new | 9 | no | False | `done` | 0 | premature_termination×1 |
| `appt_new_specific` | new | 4 | no | False | `done` | 0 | premature_termination×1 |
| `appt_reschedule` | reschedule | 7 | no | False | `done` | 0 | premature_termination×1 |
| `appt_reschedule_earlier` | reschedule | 7 | no | False | `done` | 0 | premature_termination×1 |
| `appt_cancel` | cancel | 7 | no | False | `done` | 0 | premature_termination×1 |
| `appt_cancel_and_rebook` | reschedule | 14 | no | False | `done` | 0 | premature_termination×1 |
| `appt_out_of_hours` | out-of-hours | 1 | no | None | `capture_need` | 1 | stuck_termination×1 |
| `appt_just_hours` | out-of-hours | 4 | no | True | `capture_need` | 0 | stuck_termination×1 |
| `appt_double_booking` | new | 3 | no | False | `capture_need` | 0 | stuck_termination×1 |
| `appt_wrong_details` | reschedule | 6 | no | None | `done` | 1 | stuck_termination×1 |
