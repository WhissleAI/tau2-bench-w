# Flow-edit sensitivity report — `headache_enrollment` (text mode)

- **run**: 20260806T213437Z  •  **base**: studio API path (`PATCH ?target=draft` → `POST /publish`)  •  **probe transport**: text
- **mutations**: 8  •  **picked up by the live conversation**: 8/8

A FAIL row is a product bug: an edit made through the same API the flow-designer UI uses that did **not** manifest in the live conversation (or a draft that leaked before publish).

| mutation | kind | target | draft staged | live inert (draft) | published | behavior | voice | verdict |
|----------|------|--------|--------------|--------------------|-----------|----------|-------|---------|
| `say_sentinel_greet` | say | `greet` | PASS | PASS | PASS | PASS | — | PASS |
| `conversation_goal_consent` | conversation | `consent` | PASS | PASS | PASS | PASS | — | PASS |
| `transition_condition_t3` | transition | `t3` | PASS | PASS | PASS | PASS | — | PASS |
| `transition_retarget_t3` | transition | `t3` | PASS | PASS | PASS | PASS | — | PASS |
| `tool_gate_remove_about_you` | tool_gate | `about_you` | PASS | PASS | PASS | PASS | — | PASS |
| `tool_gate_add_consent` | tool_gate | `consent` | PASS | PASS | PASS | PASS | — | PASS |
| `state_remove_about_you` | state_remove | `about_you` | PASS | PASS | PASS | PASS | — | PASS |
| `set_variable_expression` | variable | `mut_set_probe` | PASS | PASS | PASS | PASS | — | PASS |

## Expected vs observed

### `say_sentinel_greet` — PASS

- **edit**: replace say text of state 'greet' with a sentinel phrase
- **expected signal**: sentinel phrase appears in the agent's opening reply (and in voice, in the bot audio re-ASR)
- `baseline_flow_attached` [ok]: flow with 10 states
- `validate_ok` [ok]: valid=True errors=[] warnings=0
- `draft_staged` [ok]: has_draft=True draft_matches_mutation=True
- `live_unchanged_while_draft` [ok]: live flow unchanged
- `draft_behavior_inert` [ok]: sentinel absent (draft inert)
- `published` [ok]: live flow matches the mutation
- `behavior` [ok]: sentinel found in: transcript, say_emitted trace

### `conversation_goal_consent` — PASS

- **edit**: replace the goal of conversation state 'consent' with a sentinel datum request (favorite color)
- **expected signal**: the agent asks for the caller's favorite color
- `baseline_flow_attached` [ok]: flow with 10 states
- `validate_ok` [ok]: valid=True errors=[] warnings=0
- `draft_staged` [ok]: has_draft=True draft_matches_mutation=True
- `live_unchanged_while_draft` [ok]: live flow unchanged
- `published` [ok]: live flow matches the mutation
- `behavior` [ok]: agent asked for the favorite color

### `transition_condition_t3` — PASS

- **edit**: tighten llm_condition 't3' (consent→about_you) to fire only on the magic word 'pineapple'
- **expected signal**: routing holds on a normal 'ready' and advances only on the magic word
- `baseline_flow_attached` [ok]: flow with 10 states
- `validate_ok` [ok]: valid=True errors=[] warnings=0
- `draft_staged` [ok]: has_draft=True draft_matches_mutation=True
- `live_unchanged_while_draft` [ok]: live flow unchanged
- `published` [ok]: live flow matches the mutation
- `behavior` [ok]: held_on_ready=True (state after ready turn: 'consent'), fired_after_magic_word=True (checks: 2 not_satisfied, 1 fired)

### `transition_retarget_t3` — PASS

- **edit**: retarget edge 't3' from 'about_you' to 'close'
- **expected signal**: after the ready turn the flow enters 'close' and never enters 'about_you'
- `baseline_flow_attached` [ok]: flow with 10 states
- `validate_ok` [ok]: valid=True errors=[] warnings=1
- `draft_staged` [ok]: has_draft=True draft_matches_mutation=True
- `live_unchanged_while_draft` [ok]: live flow unchanged
- `published` [ok]: live flow matches the mutation
- `behavior` [ok]: entered close=True, skipped about_you=True, edge fired to=['close'], ended=True

### `tool_gate_remove_about_you` — PASS

- **edit**: empty allowed_tools of state 'about_you' (was allowing 'save_contact_field')
- **expected signal**: the 'about_you' gate excludes 'save_contact_field' and the tool is never invoked there
- `baseline_flow_attached` [ok]: flow with 10 states
- `validate_ok` [ok]: valid=True errors=[] warnings=0
- `draft_staged` [ok]: has_draft=True draft_matches_mutation=True
- `live_unchanged_while_draft` [ok]: live flow unchanged
- `published` [ok]: live flow matches the mutation
- `behavior` [ok]: gate_excludes_tool=True (gates: [[]]), never_invoked=True (tools_used: [])

### `tool_gate_add_consent` — PASS

- **edit**: add tool 'save_contact_field' to previously tool-less state 'consent'
- **expected signal**: the 'consent' gate now admits 'save_contact_field'
- `baseline_flow_attached` [ok]: flow with 10 states
- `validate_ok` [ok]: valid=True errors=[] warnings=0
- `draft_staged` [ok]: has_draft=True draft_matches_mutation=True
- `live_unchanged_while_draft` [ok]: live flow unchanged
- `published` [ok]: live flow matches the mutation
- `behavior` [ok]: gates seen for 'consent': [['save_contact_field']]

### `state_remove_about_you` — PASS

- **edit**: remove mid-flow state 'about_you', rewiring its inbound edges to 'headache_profile'
- **expected signal**: the session skips 'about_you' and reaches 'headache_profile' directly
- `baseline_flow_attached` [ok]: flow with 10 states
- `validate_ok` [ok]: valid=True errors=[] warnings=0
- `draft_staged` [ok]: has_draft=True draft_matches_mutation=True
- `live_unchanged_while_draft` [ok]: live flow unchanged
- `published` [ok]: live flow matches the mutation
- `behavior` [ok]: states entered: ['greet', 'consent', 'headache_profile'] (skipped=True, reached_forward=True)

### `set_variable_expression` — PASS

- **edit**: insert a set_variable state (mutation_probe='armed') on the entry path plus an expression edge 'mut_t_expr' keyed on it
- **expected signal**: var_set appears in the trace and the expression edge routes the very next turn to the closing state
- `baseline_flow_attached` [ok]: flow with 10 states
- `validate_ok` [ok]: valid=True errors=[] warnings=0
- `draft_staged` [ok]: has_draft=True draft_matches_mutation=True
- `live_unchanged_while_draft` [ok]: live flow unchanged
- `published` [ok]: live flow matches the mutation
- `behavior` [ok]: var_set_seen=True (steps: 2), expr_edge_fired=True, routed_to_close=True, ended=True

