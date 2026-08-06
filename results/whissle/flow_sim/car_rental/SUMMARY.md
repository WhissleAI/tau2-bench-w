# Flow-sim summary — `car_rental`

- **run**: 20260805T173902Z
- **sessions**: 5  •  **ended cleanly**: 1/5  •  **task success**: 3/5
- **sessions with HIGH-severity findings**: 0/5

## Findings by type

| type | count |
|------|-------|
| `stuck_termination` | 4 |
| `coverage` | 2 |

## Findings by severity

| severity | count |
|----------|-------|
| medium | 4 |
| info | 2 |

## State / transition coverage

- **states visited**: 8/10
- **transitions fired**: 7/11
- **states never entered**: `['book', 'confirm']`
- **transitions never fired**: `['contact_to_book', 'book_to_confirm', 'noavail_to_capture', 'confirm_to_end']`

## Sessions

| task | scenario | turns | ended | task_success | final_state | high | finding types |
|------|----------|-------|-------|--------------|-------------|------|---------------|
| `car_happy_suv` | book | 11 | no | True | `None` | 0 | stuck_termination×1 |
| `car_no_availability` | no-availability | 4 | yes | True | `None` | 0 |  |
| `car_change_vehicle` | change vehicle | 9 | no | True | `None` | 0 | stuck_termination×1 |
| `car_price_too_high` | just-asking | 10 | no | False | `None` | 0 | stuck_termination×1 |
| `car_one_way` | book | 9 | no | False | `None` | 0 | stuck_termination×1 |
