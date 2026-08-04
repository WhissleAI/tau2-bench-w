# Flow scenario: marker (smoke canary)

- **id**: `marker`
- **agent**: `125e22cf-8f39-45cb-a637-e2df6667a320` (deleted: True)
- **trace present**: False (trace assertions SKIPPED-pending-trace)
- **result**: PASS — 4 pass / 0 fail / 2 pending-trace

mark(say)->ask(conversation)->wrap(say)->end. The proven canary: verbatim say-markers prove each say-state executed and the llm_condition transition fired. Fully observable today; no trace field required to pass.

## Turns

**Turn 1** — user: `hello there`
> FLOW-ACTIVE-MARKER-7788
Can you say the word BANANA for me?
tools_used: `[]`

**Turn 2** — user: `BANANA`
> Perfect! You said it. How can I help you today with Whissle or any of your businesses?
SECOND-MARKER-DONE-9911
tools_used: `[]`

## Assertions

- [PASS] (observable) `turn1.reply_contains['FLOW-ACTIVE-MARKER-7788']`
- [PASS] (observable) `turn2.reply_contains['SECOND-MARKER-DONE-9911']`
- [PASS] (observable) `say_marker[FLOW-ACTIVE-MARKER-7788]`
- [PASS] (observable) `say_marker[SECOND-MARKER-DONE-9911]`
- [PENDING-TRACE] (trace) `state_sequence` — flow.steps / trace not present — pending flow-step-trace deploy
- [PENDING-TRACE] (trace) `fired_transitions` — flow.steps / trace not present — pending flow-step-trace deploy
