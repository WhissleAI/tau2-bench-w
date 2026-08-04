# Default-flow coverage: `medical_followup`

- **agent**: `6ad3c3b9-b616-4cb1-a8b4-e2cf4a53e1d1` (deleted: True)
- **attached**: True  •  **driving**: True
- **start_state**: `greet` (say)
- **attach attempts**: 1
- **result**: PASS — 9 pass / 0 fail

## Turns

**Turn 1** — user: `Hello.`  (current_state: `consent`)
> Hi, this is an automated follow-up call from your healthcare provider. I'm just checking in to see how you've been doing since your last visit. Is now a good time to talk?
Hello! Thanks for getting in touch. I hope this is a good time to talk? If you're busy right now, I'm happy to call back at a time that works better for you.

**Turn 2** — user: `Yes, now is a good time.`  (current_state: `progress_check`)
> Wonderful! I'm glad we caught you at a good time. 

Since you're running multiple businesses — your dental clinic in Patiala, your online tutoring service, and your car rental business in Europe — I wanted to reach out to see how things are going and if there's anything I can help you with today.

Is there something specific you'd like to discuss, or were you just checking in?

**Turn 3** — user: `I've been recovering well.`  (current_state: `progress_check`)
> That's great to hear that you've been recovering well! I'm really glad to hear that.

I'd like to get a bit more detail about how you've been feeling. Could you tell me:

**What specific improvements have you noticed since your last appointment?** For example, have any symptoms that were bothering you gotten better, or has your overall comfort level improved?

Also, **have there been any areas where you're still experiencing discomfort or where things haven't changed much?**

And finally, **on a scale from 0 to 10, where 0 is feeling terrible and 10 is feeling completely back to normal — how would you rate how you're feeling today?**

**Turn 4** — user: `No new symptoms.`  (current_state: `progress_check`)
> That's excellent — no new symptoms is definitely a positive sign. It sounds like things have been stable for you, which is good to hear.

To get a fuller picture of your recovery, I'd like to understand a bit more:

**Have the symptoms or issues you were experiencing before your last appointment improved at all, or have they stayed pretty much the same as they were?**

And **on that 0 to 10 scale I mentioned — where 0 is feeling your worst and 10 is feeling completely back to normal — where would you say you're at today?**

This will help me understand your overall recovery trajectory and whether everything is progressing as expected.

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
