# Default-flow coverage: `customer_support`

- **agent**: `e89a1b73-267d-491b-9f7b-47ea536aa70a` (deleted: True)
- **attached**: True  •  **driving**: True
- **start_state**: `greet` (conversation)
- **attach attempts**: 1
- **result**: PASS — 8 pass / 0 fail

## Turns

**Turn 1** — user: `Hi, I need some help.`  (current_state: `greet`)
> Hi there! I'm here to help. What can I do for you today?

**Turn 2** — user: `My account isn't working.`  (current_state: `understand_issue`)
> I'm sorry to hear that. Can you tell me a bit more about what's happening with your account? What issue are you experiencing?

**Turn 3** — user: `I can't log in.`  (current_state: `verify_identity`)
> I understand—that's frustrating. To help you troubleshoot this, I need a bit more information:

1. **Which of your services is this for?** Are you trying to log in to your tutoring service, your car rental business, or your dental clinic?

2. **What happens when you try to log in?** For example:
   - Do you get an error message?
   - Does it say your password is incorrect?
   - Does the page not load at all?
   - Something else?

3. **Have you recently changed your password**, or is this the first time you're trying to log in?

Once I understand a bit more about what's going on, I'll be able to help you get back into your account.

**Turn 4** — user: `Yes, I've tried resetting.`  (current_state: `escalate`)
> I apologize—it looks like we're not on a phone line where I can collect keypad input at the moment. 

Could you please provide your account number directly? I'll need it to verify your identity and help resolve your login issue.

## Assertions

- [PASS] (attach) `attach.flow_present`
- [PASS] (attach) `attach.flow_enabled`
- [PASS] (attach) `attach.states_non_empty`
- [PASS] (attach) `attach.start_state_real`
- [PASS] (drive) `drive.turn1_flow_active`
- [PASS] (drive) `drive.enters_start_state_first`
- [PASS] (drive) `drive.state_enter_start`
- [PASS] (drive) `drive.transition_check_present`
