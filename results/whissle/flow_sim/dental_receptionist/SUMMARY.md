# Flow-sim summary — `dental_receptionist`

- **run**: 20260804T055114Z
- **sessions**: 2  •  **ended cleanly**: 0/2  •  **task success**: 0/2
- **sessions with HIGH-severity findings**: 0/2

## Findings by type

| type | count |
|------|-------|
| `coverage` | 2 |
| `stuck_termination` | 1 |
| `premature_termination` | 1 |

## Findings by severity

| severity | count |
|----------|-------|
| medium | 2 |
| info | 2 |

## State / transition coverage

- **states visited**: 6/10
- **transitions fired**: 5/16
- **states never entered**: `['book_schedule', 'book_confirm', 'send_confirmation', 'cancel']`
- **transitions never fired**: `['t4', 't5', 't6', 't7', 't8', 't9', 't10', 't11', 't12', 't14', 't15']`

## Sessions

| task | scenario | turns | ended | task_success | final_state | high | finding types |
|------|----------|-------|-------|--------------|-------------|------|---------------|
| `dental_happy_book` | book | 6 | no | False | `book_collect` | 0 | stuck_termination×1 |
| `dental_reschedule` | reschedule | 11 | no | False | `done` | 0 | premature_termination×1 |
