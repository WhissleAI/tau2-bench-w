# Default-flow coverage: `appointment_scheduling`

- **agent**: `8ce592ac-c74c-421e-bfbb-38d0771b849d` (deleted: True)
- **attached**: True  •  **driving**: True
- **start_state**: `greet` (conversation)
- **attach attempts**: 1
- **result**: PASS — 8 pass / 0 fail

## Turns

**Turn 1** — user: `Hi, I'd like to schedule something.`  (current_state: `capture_need`)
> Perfect! We're open **Monday to Friday, from 9 AM to 6 PM**.

Now, to help you best, could you tell me:

1. **What type of appointment** would you like to schedule? (For example, are you looking to book a dental appointment, tutoring session, or car rental service?)
2. **Are you looking to book a brand new appointment**, or would you like to **modify or reschedule an existing appointment**?

Once I understand your needs, I'll be happy to get you all set up! 😊

**Turn 2** — user: `A check-up, please.`  (current_state: `capture_need`)
> Great! So you'd like to schedule a **dental check-up** at our clinic in Patiala. Just to confirm, that's correct, right?

Now, to complete your appointment booking, I'll need a couple of details from you:

1. **Your full name**
2. **A callback phone number** where we can reach you

Could you please provide those for me?

**Turn 3** — user: `Weekday mornings are best.`  (current_state: `capture_need`)
> Perfect! I've noted that **weekday mornings** work best for you. We'll keep that in mind for your dental check-up appointment.

Now, to finalize your booking, I still need:

1. **Your full name**
2. **A callback phone number** where we can reach you

Could you please provide those details?

**Turn 4** — user: `Yes, that time works.`  (current_state: `capture_need`)
> Wonderful! So to confirm, you'd like to schedule your **dental check-up on a weekday morning** at our Patiala clinic.

To complete your appointment booking, I still need:

1. **Your full name**
2. **A callback phone number** where we can reach you

Could you please share those with me?

## Assertions

- [PASS] (attach) `attach.flow_present`
- [PASS] (attach) `attach.flow_enabled`
- [PASS] (attach) `attach.states_non_empty`
- [PASS] (attach) `attach.start_state_real`
- [PASS] (drive) `drive.turn1_flow_active`
- [PASS] (drive) `drive.enters_start_state_first`
- [PASS] (drive) `drive.state_enter_start`
- [PASS] (drive) `drive.transition_check_present`
