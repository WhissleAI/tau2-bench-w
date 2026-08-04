# Default-flow coverage: `patient_checkin`

- **agent**: `0c73472c-7bc1-4230-8205-771b20b1cd70` (deleted: True)
- **attached**: True  •  **driving**: True
- **start_state**: `greet` (say)
- **attach attempts**: 1
- **result**: PASS — 9 pass / 0 fail

## Turns

**Turn 1** — user: `Hello.`  (current_state: `verify`)
> Thanks for calling the post-discharge check-in line. I'm an automated care assistant and I can help you complete your recovery check-in. To pull up your record, could I start with your full name and date of birth?
Hello! Welcome to Whissle. I'm here to help you today.

To get started, could you please provide me with your full name and date of birth?

**Turn 2** — user: `Yes, this is the patient.`  (current_state: `verify`)
> Great! To help you, I'll need a bit more information. Could you please provide me with:

1. Your full name
2. Your date of birth

Once I have those details, I can pull up your record and we can proceed.

**Turn 3** — user: `I'm feeling okay, thanks.`  (current_state: `verify`)
> That's good to hear! I'm glad you're feeling okay. 

To make sure I have your information and can assist you properly, I'll still need to verify your identity. Could you please provide me with:

1. Your full name
2. Your date of birth

This will help me access your record so I can help with any questions or concerns you might have.

**Turn 4** — user: `Go ahead.`  (current_state: `verify`)
> I appreciate your patience. To pull up your record, I need you to tell me:

1. **Your full name** – what should I call you?
2. **Your date of birth** – in the format month, day, and year

Once you provide those, I'll be able to access your information and we can proceed.

## Assertions

- [PASS] (attach) `attach.flow_present`
- [PASS] (attach) `attach.flow_enabled`
- [PASS] (attach) `attach.states_non_empty`
- [PASS] (attach) `attach.start_state_real`
- [PASS] (drive) `drive.turn1_flow_active`
- [PASS] (drive) `drive.enters_start_state_first`
- [PASS] (drive) `drive.state_enter_start`
- [PASS] (drive) `drive.transition_check_present`
- [PASS] (drive) `drive.start_say_verbatim`
