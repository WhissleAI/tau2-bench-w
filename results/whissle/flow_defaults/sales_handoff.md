# Default-flow coverage: `sales_handoff`

- **agent**: `552eba34-b3d0-4736-bc4f-bf4f9ab3010b` (deleted: True)
- **attached**: True  •  **driving**: True
- **start_state**: `greet` (conversation)
- **attach attempts**: 1
- **result**: PASS — 8 pass / 0 fail

## Turns

**Turn 1** — user: `Hi, I'm interested in your product.`  (current_state: `greet`)
> # Hello! 👋

Thanks for reaching out! I'd be happy to help you learn more about Whissle.

Before we dive in, could you tell me your name so I can make sure we're on the same page? And just to confirm — are you reaching out about one of your businesses (your tutoring service, dental clinic in Patiala, or car rental business in Europe), or is this for something else entirely?

**Turn 2** — user: `We're a mid-sized company.`  (current_state: `greet`)
> Got it! Thanks for letting me know you're a mid-sized company.

Just to clarify — are you inquiring about Whissle for your **tutoring service**, your **dental clinic in Patiala**, your **car rental business in Europe**, or are you exploring it for a completely different venture?

This'll help me point you in the right direction and show you how Whissle can best support your operations. 😊

**Turn 3** — user: `Pricing is my main question.`  (current_state: `greet`)
> Great — pricing is definitely an important piece of the puzzle!

Before I walk you through our pricing options, I want to make sure I'm showing you the right plan for your needs. Since you mentioned you're a mid-sized company, could you help me understand:

**Which of your businesses are you looking to use Whissle for** — your tutoring service, dental clinic, or car rental business? (Or is this for a different business altogether?)

That way, I can give you accurate pricing information tailored to what you actually need. 👍

**Turn 4** — user: `Yes, please connect me.`  (current_state: `greet`)
> I appreciate the enthusiasm! Just to clarify — I'm here to help answer your questions directly about Whissle and our pricing.

Are you asking me to:
1. **Connect you with a sales representative** who can dive deeper into pricing and custom plans?
2. **Get more information from me right now** about how our pricing works?

Also, just to make sure I point you to the right person — **which of your businesses would this be for?** (Your tutoring service, dental clinic in Patiala, or car rental business in Europe?)

That way, if you do need to speak with someone on our team, they'll be fully prepped to help! 😊

## Assertions

- [PASS] (attach) `attach.flow_present`
- [PASS] (attach) `attach.flow_enabled`
- [PASS] (attach) `attach.states_non_empty`
- [PASS] (attach) `attach.start_state_real`
- [PASS] (drive) `drive.turn1_flow_active`
- [PASS] (drive) `drive.enters_start_state_first`
- [PASS] (drive) `drive.state_enter_start`
- [PASS] (drive) `drive.transition_check_present`
