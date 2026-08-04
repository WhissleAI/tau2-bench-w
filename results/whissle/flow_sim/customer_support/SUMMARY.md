# Flow-sim summary — `customer_support`

- **run**: 20260804T155822Z
- **sessions**: 10  •  **ended cleanly (flag)**: 0/10  •  **reached end state**: 2/10  •  **task success**: 6/10
- **sessions with HIGH-severity findings**: 0/10

## Seeded run — true-completion checks

- **CS resolve_done**: 2/10  •  **escalated**: 4/10
- **seed health**: no-error 10/10  •  resources tracked 10  •  teardown-failed 0

## Findings by type

| type | count |
|------|-------|
| `stuck_termination` | 7 |
| `premature_termination` | 1 |
| `coverage` | 1 |

## Findings by severity

| severity | count |
|----------|-------|
| medium | 8 |
| info | 1 |

## State / transition coverage

- **states visited**: 10/10
- **transitions fired**: 12/13
- **transitions never fired**: `['howto_done']`

## Sessions

| task | scenario | turns | ended | task_success | final_state | high | finding types |
|------|----------|-------|-------|--------------|-------------|------|---------------|
| `cs_resolvable_login` | resolvable | 4 | no | True | `verify_identity` | 0 | stuck_termination×1 |
| `cs_resolvable_billing` | resolvable | 4 | no | True | `end` | 0 |  |
| `cs_needs_escalation` | needs-escalation | 6 | no | False | `escalate` | 0 | stuck_termination×1 |
| `cs_angry_refund` | angry | 4 | no | True | `escalate` | 0 | stuck_termination×1 |
| `cs_account_lookup` | account-lookup | 4 | no | False | `verify_identity` | 0 | stuck_termination×1 |
| `cs_cancel_service` | resolvable | 3 | no | False | `understand_issue` | 0 | stuck_termination×1 |
| `cs_angry_escalate` | angry | 4 | no | True | `verify_identity` | 0 | stuck_termination×1 |
| `cs_password_reset_fail` | needs-escalation | 6 | no | True | `end` | 0 |  |
| `cs_feature_question` | resolvable | 5 | no | False | `transfer_human` | 0 | premature_termination×1 |
| `cs_wrong_account` | account-lookup | 5 | no | True | `verify_identity` | 0 | stuck_termination×1 |
