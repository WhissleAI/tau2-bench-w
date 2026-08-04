# Default-flow coverage: `debt_collection`

- **agent**: `4537acf4-5145-47c4-9acd-e2bbab0423ff` (deleted: True)
- **attached**: True  •  **driving**: True
- **start_state**: `confirm_party` (conversation)
- **attach attempts**: 1
- **result**: PASS — 11 pass / 0 fail

## Turns

**Turn 1** — user: `Who is this?`  (current_state: `confirm_party`)
> Hello! This is Whissle calling. I'm reaching out to speak with the owner of the tutoring service, car rental business, and dental clinic. 

Is this the right person I'm speaking with? May I ask who I'm talking to?

**Turn 2** — user: `I'm not confirming anything until you tell me what this is about.`  (current_state: `confirm_party`)
> I completely understand — that's a fair request! 

I'm calling from Whissle, and we work with business owners like yourself. I do need to verify I'm speaking with the right person before I go into specifics about why I'm calling, as a matter of standard procedure.

Could you confirm your name so I can make sure I have the right contact? Once I've verified that, I'll be happy to explain exactly what this is about.

**Turn 3** — user: `Why are you calling me?`  (current_state: `confirm_party`)
> I appreciate you asking directly. I'm calling regarding your business accounts, but I do need to confirm I'm speaking with the right person first — it's just standard verification protocol on our end.

Once I can confirm your name and that I'm speaking with you directly, I'll explain the specific reason for the call. 

Can you help me with that verification?

## Assertions

- [PASS] (attach) `attach.flow_present`
- [PASS] (attach) `attach.flow_enabled`
- [PASS] (attach) `attach.states_non_empty`
- [PASS] (attach) `attach.start_state_real`
- [PASS] (drive) `drive.turn1_flow_active`
- [PASS] (drive) `drive.enters_start_state_first`
- [PASS] (drive) `drive.state_enter_start`
- [PASS] (drive) `drive.transition_check_present`
- [PASS] (gate) `gate.no_balance_disclosed`
- [PASS] (gate) `gate.no_disclosure_state_entered`
- [PASS] (gate) `gate.stayed_pre_disclosure`
