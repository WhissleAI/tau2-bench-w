# Default-flow coverage: `dental_receptionist`

- **agent**: `320d2627-6c95-46cf-a103-34ca2347301f` (deleted: True)
- **attached**: True  •  **driving**: True
- **start_state**: `greet` (say)
- **attach attempts**: 1
- **result**: PASS — 9 pass / 0 fail

## Turns

**Turn 1** — user: `Hi there.`  (current_state: `triage`)
> Thanks for calling. I can book, move or cancel an appointment, or tell you our opening hours — what can I do for you?
Hello! Welcome to Whissle Dental Clinic in Patiala. 👋

How can I help you today? You can:
- **Book a new appointment**
- **Reschedule or cancel** an existing appointment
- **Hear our opening hours**

What would you like to do?

**Turn 2** — user: `I'd like to book an appointment.`  (current_state: `book_collect`)
> Great! I'd be happy to help you book an appointment.

To get started, I'll need a few details:

1. **Your name** — what should I have on file?
2. **Your phone number** — the best way to reach you
3. **What brings you in?** — are you looking for a cleaning, checkup, treatment for a specific issue, or something else?

Once I have those details, I can find you an available time slot.

**Turn 3** — user: `Next Tuesday morning works.`  (current_state: `book_collect`)
> Perfect! Next Tuesday morning works for you.

Before I can confirm that slot, I need to gather a bit more information:

1. **What's your full name?**
2. **Your date of birth?** (I'll need this to set up your record)
3. **Are you a new patient or an existing patient** with us?
4. **What type of appointment are you looking for?** For example:
   - Exam & cleaning
   - Consultation
   - Filling, crown, or follow-up treatment
   - Emergency care

5. **A callback phone number** — the best way to reach you to confirm the appointment

Once I have these details, I can lock in your Tuesday morning slot!

**Turn 4** — user: `Yes, please go ahead.`  (current_state: `book_collect`)
> I appreciate that, but I still need some key details to book your appointment. Let me ask them one by one to make it easy:

**First — what's your full name?**

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
