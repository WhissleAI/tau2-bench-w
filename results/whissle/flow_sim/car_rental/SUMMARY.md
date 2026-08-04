# Flow-sim summary — `car_rental`

- **run**: 20260804T155816Z
- **sessions**: 10  •  **ended cleanly (flag)**: 0/10  •  **reached end state**: 1/10  •  **task success**: 2/10
- **sessions with HIGH-severity findings**: 0/10

## Seeded run — true-completion checks

- **action tool actually called**: 1/6  ({'reserve': '1/6'})
- **seed health**: no-error 10/10  •  resources tracked 0  •  teardown-failed 0

## Findings by type

| type | count |
|------|-------|
| `stuck_termination` | 9 |
| `premature_termination` | 1 |
| `coverage` | 1 |

## Findings by severity

| severity | count |
|----------|-------|
| medium | 10 |
| info | 1 |

## State / transition coverage

- **states visited**: 10/10
- **transitions fired**: 10/11
- **transitions never fired**: `['confirm_to_end']`

## Sessions

| task | scenario | turns | ended | task_success | final_state | high | finding types |
|------|----------|-------|-------|--------------|-------------|------|---------------|
| `car_happy_suv` | book | 7 | no | True | `book` | 0 | stuck_termination×1 |
| `car_happy_economy` | book | 3 | no | False | `quote` | 0 | stuck_termination×1 |
| `car_no_availability` | no-availability | 2 | no | False | `capture_details` | 0 | stuck_termination×1 |
| `car_change_vehicle` | change vehicle | 8 | no | True | `confirm` | 0 | stuck_termination×1 |
| `car_just_asking` | just-asking | 3 | no | False | `quote` | 0 | stuck_termination×1 |
| `car_long_term` | book | 3 | no | False | `capture_details` | 0 | stuck_termination×1 |
| `car_one_way` | book | 2 | no | False | `greet` | 0 | stuck_termination×1 |
| `car_add_driver` | change vehicle | 8 | no | False | `collect_contact` | 0 | stuck_termination×1 |
| `car_price_too_high` | just-asking | 5 | no | False | `end` | 0 | premature_termination×1 |
| `car_wrong_dates` | no-availability | 7 | no | False | `quote` | 0 | stuck_termination×1 |
