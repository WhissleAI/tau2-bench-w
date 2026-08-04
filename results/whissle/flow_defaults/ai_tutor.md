# Default-flow coverage: `ai_tutor`

- **agent**: `eb343cae-e475-453f-8981-f8cff6e9dc2e` (deleted: True)
- **attached**: True  •  **driving**: True
- **start_state**: `greet` (conversation)
- **attach attempts**: 1
- **result**: PASS — 8 pass / 0 fail

## Turns

**Turn 1** — user: `Hi.`  (current_state: `understand_goal`)
> I appreciate you reaching out! I can see from what we have on file that you're involved in several ventures—you run an online tutoring service, a car rental business in Europe, and a dental clinic in Patiala.

What brings you in today? What would you like to work on or discuss in our session?

**Turn 2** — user: `I'd like help with algebra.`  (current_state: `understand_goal`)
> Great! I'd like to help you get better at algebra. Before we dive in, let me understand where you're at and what would be most useful for you.

**Where do you feel like you're getting stuck with algebra right now?** Is it a specific type of problem (like equations, graphing, word problems), or is there a broader concept that's giving you trouble?

**Turn 3** — user: `I don't understand how to solve for x.`  (current_state: `understand_goal`)
> Okay, let me ask you this to get a clearer picture:

**When you're looking at an equation like 2x + 5 = 13, what part confuses you the most?** Is it:
- Not knowing where to start?
- Understanding what "solving for x" actually means?
- Knowing which steps to take to isolate x?
- Something else entirely?

This will help me pitch the explanation at exactly the right level for you.

**Turn 4** — user: `Okay, walk me through it.`  (current_state: `understand_goal`)
> Perfect! Let me walk you through solving **2x + 5 = 13** step by step.

**The goal:** Get x by itself on one side of the equation.

**Step 1: Subtract 5 from both sides**
- Why? Because 5 is being added to 2x, so we do the opposite (subtract) to remove it.
- 2x + 5 - 5 = 13 - 5
- 2x = 8

**Step 2: Divide both sides by 2**
- Why? Because x is being multiplied by 2, so we do the opposite (divide) to isolate x.
- 2x ÷ 2 = 8 ÷ 2
- **x = 4**

**Check your answer:** Plug x = 4 back into the original equation:
- 2(4) + 5 = 8 + 5 = 13 ✓

---

**The key principle:** Whatever you do to one side of the equation, do it to the other side. Think of it like a balanced scale—you have to keep both sides equal.

Does this make sense? Would you like to try one on your own, or do you want me to explain a different type of equation?

## Assertions

- [PASS] (attach) `attach.flow_present`
- [PASS] (attach) `attach.flow_enabled`
- [PASS] (attach) `attach.states_non_empty`
- [PASS] (attach) `attach.start_state_real`
- [PASS] (drive) `drive.turn1_flow_active`
- [PASS] (drive) `drive.enters_start_state_first`
- [PASS] (drive) `drive.state_enter_start`
- [PASS] (drive) `drive.transition_check_present`
