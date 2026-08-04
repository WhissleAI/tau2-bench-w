# Flow scenario: guarded loop (max_visits guard)

- **id**: `guarded_loop`
- **agent**: `efd7fe70-9457-48a2-b294-e51718f1e062` (deleted: True)
- **trace present**: False (trace assertions SKIPPED-pending-trace)
- **result**: PASS — 4 pass / 0 fail / 3 pending-trace

A re-entering loop (spin_a <-> spin_b, sustained by a to_override redirect) that the max_visits_per_state guard trips within N re-entries; on_guard_trip:fallback then escapes the machine to the landing state. Fully deterministic (say/set_variable/always only, no LLM judge) so it runs and proves loop guards in a single turn, today. The verbatim LOOP-ESCAPED marker only appears if the guard tripped and the fallback fired.

## Turns

**Turn 1** — user: `start the loop`
> LOOP-START-MARKER-8080
LOOP-ESCAPED-MARKER-9090
I'm not sure what you mean by "start the loop." Could you provide more context about what you'd like me to help you with?

Are you looking to:
- Get information about one of your businesses (the dental clinic in Patiala, the online tutoring service, or the car rental business in Europe)?
- Set up a recurring process or automation?
- Start a conversation about Whissle?
- Something else?

Please let me know what you need, and I'll be happy to assist!
tools_used: `[]`

## Assertions

- [PASS] (observable) `turn1.reply_contains['LOOP-START-MARKER-8080']`
- [PASS] (observable) `turn1.reply_contains['LOOP-ESCAPED-MARKER-9090']`
- [PASS] (observable) `say_marker[LOOP-START-MARKER-8080]`
- [PASS] (observable) `say_marker[LOOP-ESCAPED-MARKER-9090]`
- [PENDING-TRACE] (trace) `state_sequence` — flow.steps / trace not present — pending flow-step-trace deploy
- [PENDING-TRACE] (trace) `fired_transitions` — flow.steps / trace not present — pending flow-step-trace deploy
- [PENDING-TRACE] (trace) `guard_trip` — flow.steps / trace not present — pending flow-step-trace deploy
