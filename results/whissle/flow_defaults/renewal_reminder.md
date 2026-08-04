# Default-flow coverage: `renewal_reminder`

- **agent**: `b1ddca51-495b-428c-bde7-5850cee346f1` (deleted: True)
- **attached**: True  •  **driving**: True
- **start_state**: `greet_identify` (conversation)
- **attach attempts**: 1
- **result**: PASS — 8 pass / 0 fail

## Turns

**Turn 1** — user: `Hello?`  (current_state: `greet_identify`)
> Namaste! 🙏 

Aap kaisa/kaisi ho? Whissle mein aapka swagat hai. Main aapki kya madad kar sakta hoon?

(Hi! How are you? Welcome to Whissle. How can I help you?)

Aap apna naam bataaiye, please?

**Turn 2** — user: `Yes, speaking.`  (current_state: `greet_identify`)
> Bilkul! Shukriya aapka jawab dene ke liye. 

Aap Whissle se kya chahte ho aaj? Kya aap apne car rental business, dental clinic, ya tutoring service ke baare mein kuch jaanna chahte ho?

(Sure! Thank you for responding. What would you like from Whissle today? Do you want to know something about your car rental business, dental clinic, or tutoring service?)

Mujhe bataaiye, main aapki kaise madad kar sakta hoon? 😊

**Turn 3** — user: `Tell me more about the renewal.`  (current_state: `greet_identify`)
> Aap bilkul sahi keh rahe ho! Lekin mujhe aapके baare mein thoda aur jaankari chahiye renewal ke liye.

Kya aap bata sakte ho:

1. **Konsa business renewal** - Car rental (Europe), dental clinic (Patiala), ya tutoring service (online)?
2. **Kaunsa policy ya subscription** renewal ho raha hai?
3. **Aapka order ID ya phone number** kya hai?

Iske baad main aapka renewal details dekh lunga aur sab kuch samjha dunga. 🙏

**Turn 4** — user: `Okay, I'll renew.`  (current_state: `state_renewal`)
> Bilkul! Bahut acha! 😊

Lekin renewal complete karne se pehle, mujhe kuch details chahiye:

1. **Aap kaun sa business renew karna chahte ho?**
   - Car rental (Europe)?
   - Dental clinic (Patiala)?
   - Tutoring service (online)?

2. **Aapka contact detail** - Phone number ya email address?

3. **Order ID ya subscription ID** (agar ho to)?

Ye details de do, phir main aapka renewal process shuru kar dunga aur sab kuch handle kar lunga. 🙌

## Assertions

- [PASS] (attach) `attach.flow_present`
- [PASS] (attach) `attach.flow_enabled`
- [PASS] (attach) `attach.states_non_empty`
- [PASS] (attach) `attach.start_state_real`
- [PASS] (drive) `drive.turn1_flow_active`
- [PASS] (drive) `drive.enters_start_state_first`
- [PASS] (drive) `drive.state_enter_start`
- [PASS] (drive) `drive.transition_check_present`
