# Flow-sim summary — `car_rental`

- **run**: 20260806T153416Z
- **sessions**: 11  •  **ended cleanly**: 0/11  •  **task success**: 0/11
- **sessions with HIGH-severity findings**: 11/11

## Findings by type

| type | count |
|------|-------|
| `stuck_termination` | 11 |
| `coverage` | 2 |

## Findings by severity

| severity | count |
|----------|-------|
| high | 11 |
| info | 2 |

## State / transition coverage

- **states visited**: 0/11
- **transitions fired**: 0/12
- **states never entered**: `['greet', 'capture_details', 'search_fleet', 'quote', 'mark_selected', 'collect_contact', 'book', 'no_availability', 'confirm', 'close', 'end']`
- **transitions never fired**: `['greet_to_capture', 'capture_to_search', 'search_to_quote', 'quote_selected', 'quote_no_match', 'selected_to_contact', 'contact_to_book', 'book_to_confirm', 'noavail_to_capture', 'noavail_to_end', 'confirm_to_end', 'close_to_end']`

## Sessions

| task | scenario | turns | ended | task_success | final_state | high | finding types |
|------|----------|-------|-------|--------------|-------------|------|---------------|
| `car_happy_suv` | book | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `car_happy_economy` | book | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `car_no_availability` | no-availability | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `car_change_vehicle` | change vehicle | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `car_just_asking` | just-asking | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `car_long_term` | book | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `car_one_way` | book | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `car_add_driver` | change vehicle | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `car_price_too_high` | just-asking | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `car_wrong_dates` | no-availability | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `car_luxury_upgrade` | book | 0 | no | None | `None` | 1 | stuck_termination×1 |
