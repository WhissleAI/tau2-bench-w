# Flow-sim summary — `customer_support`

- **run**: 20260807T014215Z
- **sessions**: 11  •  **ended cleanly**: 10/11  •  **task success**: 7/11
- **sessions with HIGH-severity findings**: 1/11
- **infra failures (excluded from flow metrics)**: 0/11

## Findings by type

| type | count |
|------|-------|
| `premature_termination` | 3 |
| `agent_no_close` | 1 |
| `coverage` | 1 |

## Findings by severity

| severity | count |
|----------|-------|
| high | 1 |
| medium | 3 |
| info | 1 |

## State / transition coverage

- **states visited**: 10/10
- **transitions fired**: 12/13
- **transitions never fired**: `['howto_done']`

## Sessions

| task | scenario | turns | ended | task_success | final_state | high | finding types |
|------|----------|-------|-------|--------------|-------------|------|---------------|
| `cs_resolvable_login` | resolvable | 9 | no | False | `None` | 1 | agent_no_close×1 |
| `cs_resolvable_billing` | resolvable | 8 | yes | True | `None` | 0 |  |
| `cs_needs_escalation` | needs-escalation | 7 | yes | True | `None` | 0 |  |
| `cs_angry_refund` | angry | 6 | yes | True | `None` | 0 |  |
| `cs_account_lookup` | account-lookup | 9 | yes | True | `None` | 0 |  |
| `cs_cancel_service` | resolvable | 24 | yes | False | `None` | 0 | premature_termination×1 |
| `cs_angry_escalate` | angry | 6 | yes | True | `None` | 0 |  |
| `cs_password_reset_fail` | needs-escalation | 12 | yes | False | `None` | 0 | premature_termination×1 |
| `cs_feature_question` | resolvable | 7 | yes | False | `None` | 0 | premature_termination×1 |
| `cs_wrong_account` | account-lookup | 7 | yes | True | `None` | 0 |  |
| `cs_data_breach_worry` | needs-escalation | 7 | yes | True | `None` | 0 |  |
