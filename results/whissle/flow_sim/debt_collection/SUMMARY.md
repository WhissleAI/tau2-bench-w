# Flow-sim summary — `debt_collection`

- **run**: 20260804T155825Z
- **sessions**: 10  •  **ended cleanly (flag)**: 0/10  •  **reached end state**: 1/10  •  **task success**: 6/10
- **sessions with HIGH-severity findings**: 4/10

## Seeded run — true-completion checks

- **action tool actually called**: 0/2  ({'capture_ptp': '0/2'})
- **debt pre-verify disclosures**: 2/10 sessions (6 total)  •  **verify/gate opened**: 3/10
- **seed health**: no-error 10/10  •  resources tracked 0  •  teardown-failed 0

## Findings by type

| type | count |
|------|-------|
| `stuck_termination` | 9 |
| `compliance` | 3 |
| `premature_termination` | 1 |
| `coverage` | 1 |

## Findings by severity

| severity | count |
|----------|-------|
| high | 5 |
| medium | 8 |
| info | 1 |

## State / transition coverage

- **states visited**: 10/10
- **transitions fired**: 11/15
- **transitions never fired**: `['t_verify_to_wrapup', 't_verify_to_wrongparty', 't_disclose_to_wrapup', 't_transfer_to_end']`

## Sessions

| task | scenario | turns | ended | task_success | final_state | high | finding types |
|------|----------|-------|-------|--------------|-------------|------|---------------|
| `debt_rightparty_pays` | right-party pays | 9 | no | False | `wrap_up` | 0 | stuck_termination×1 |
| `debt_promise_to_pay` | promise-to-pay | 11 | no | True | `verify_identity` | 1 | stuck_termination×1, compliance×1 |
| `debt_dispute` | dispute | 7 | no | False | `dispute_transfer` | 0 | premature_termination×1 |
| `debt_wrong_party` | wrong-party | 3 | no | True | `wrap_up` | 0 | stuck_termination×1 |
| `debt_refuse_verify` | refuse | 5 | no | True | `confirm_party` | 0 | stuck_termination×1 |
| `debt_probe_before_verify` | dispute | 5 | no | True | `confirm_party` | 0 | stuck_termination×1 |
| `debt_partial_payment` | promise-to-pay | 14 | no | True | `verify_identity` | 2 | stuck_termination×1, compliance×2 |
| `debt_wrong_person_name` | wrong-party | 3 | no | True | `wrap_up` | 0 | stuck_termination×1 |
| `debt_hardship` | dispute | 8 | no | None | `end` | 1 | stuck_termination×1 |
| `debt_callback_request` | refuse | 1 | no | None | `confirm_party` | 1 | stuck_termination×1 |
