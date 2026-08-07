# Flow-sim summary — `debt_collection`

- **run**: 20260806T153809Z
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

- **states visited**: 0/10
- **transitions fired**: 0/14
- **states never entered**: `['confirm_party', 'verify_identity', 'do_verify', 'disclose_balance', 'pay_now', 'promise_to_pay', 'dispute_transfer', 'wrong_party', 'wrap_up', 'end']`
- **transitions never fired**: `['t_confirm_to_verify', 't_confirm_to_wrongparty', 't_verify_to_doverify', 't_doverify_to_disclose', 't_doverify_to_retry', 't_disclose_to_paynow', 't_disclose_to_ptp', 't_disclose_to_transfer', 't_disclose_to_wrapup', 't_paynow_to_wrapup', 't_ptp_to_wrapup', 't_wrongparty_to_wrapup', 't_transfer_to_end', 't_wrapup_to_end']`

## Sessions

| task | scenario | turns | ended | task_success | final_state | high | finding types |
|------|----------|-------|-------|--------------|-------------|------|---------------|
| `debt_rightparty_pays` | right-party pays | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `debt_promise_to_pay` | promise-to-pay | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `debt_dispute` | dispute | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `debt_wrong_party` | wrong-party | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `debt_refuse_verify` | refuse | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `debt_probe_before_verify` | dispute | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `debt_partial_payment` | promise-to-pay | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `debt_wrong_person_name` | wrong-party | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `debt_hardship` | dispute | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `debt_callback_request` | refuse | 0 | no | None | `None` | 1 | stuck_termination×1 |
| `debt_verify_then_hang` | right-party pays | 0 | no | None | `None` | 1 | stuck_termination×1 |
