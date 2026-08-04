# Default-flow coverage: `headache_enrollment`

- **agent**: `ed3daba6-c4c8-4ec0-986e-1b7a961d3b91` (deleted: True)
- **attached**: True  •  **driving**: True
- **start_state**: `greet` (say)
- **attach attempts**: 1
- **result**: PASS — 9 pass / 0 fail

## Turns

**Turn 1** — user: `Hi.`  (current_state: `about_you`)
> Hi, I'm Ember. I'd love to understand your headaches a little better. I'll ask you a few questions by voice — it takes about five minutes, and you can skip anything or pause and come back anytime. Ready to start?
Hello! Welcome to Whissle. I'm here to help you with an intake process that should take about five minutes. 

Before we get started, I just want to reassure you that you can skip any questions you're not comfortable answering, and we can take this at whatever pace works for you.

Are you ready to begin?

**Turn 2** — user: `Yes, I'm ready to start.`  (current_state: `about_you`)
> Wonderful! Let's begin.

First, I'd like to understand what's brought you in today. **In your own words, what's the main reason you're seeking care right now?**

**Turn 3** — user: `They started about two weeks ago.`  (current_state: `about_you`)
> Got it — so you've been experiencing something for about two weeks now. To make sure I'm understanding clearly, **what specifically have you been experiencing?** For example, are you dealing with headaches, tooth pain, or something else?

**Turn 4** — user: `Usually in the afternoon.`  (current_state: `about_you`)
> Thank you for sharing that. So it sounds like you're experiencing something that typically happens in the afternoon. I want to make sure I capture the full picture — **what exactly do you experience during those afternoons?** Is it headaches, pain, discomfort, or something different?

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
