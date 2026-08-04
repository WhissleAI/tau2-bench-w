# Default-flow coverage: `car_rental`

- **agent**: `0a2d43ec-7274-4bba-a224-d39bbf13cf38` (deleted: True)
- **attached**: True  •  **driving**: True
- **start_state**: `greet` (conversation)
- **attach attempts**: 1
- **result**: PASS — 8 pass / 0 fail

## Turns

**Turn 1** — user: `Hi, I'd like to rent a car.`  (current_state: `capture_details`)
> Hello! Welcome to Whissle – I'm happy to help you rent a car! 

To get you the best options, I need a few details:

1. **When** would you like to pick up the car? (date and time)
2. **When** would you like to return it? (date and time)
3. **Where** in Europe would you like to pick it up?
4. **What type of car** are you looking for? (economy, sedan, SUV, etc.)

Once I have these details, I can either give you a quote or proceed with a booking – whichever you prefer!

**Turn 2** — user: `An SUV, please.`  (current_state: `capture_details`)
> Great! An SUV it is. Now let me gather the other details:

1. **Pickup location** – Which city or office in Europe would you like to pick up from?
2. **Pickup date and time** – When do you need the car?
3. **Return date and time** – When will you return it?
4. **Driver age** – How old are you? (SUVs typically require drivers to be 25 or older)

Once I have these, we can move forward!

**Turn 3** — user: `Picking up this Friday.`  (current_state: `capture_details`)
> Perfect! This Friday for pickup works. Now I need a few more details:

1. **Pickup time** – What time on Friday would you like to pick up the SUV?
2. **Return date and time** – When will you be returning the car?
3. **Pickup location** – Which city or office in Europe?
4. **Driver age** – How old are you? (SUVs require drivers to be 25+)

What time on Friday are you thinking?

**Turn 4** — user: `For about a week.`  (current_state: `capture_details`)
> Got it – so you'll be returning it about a week later. Let me confirm:

**Pickup:** This Friday  
**Return:** The following Friday (about a week later)

Now I still need:

1. **Pickup time** – What time on Friday would you like to pick up the SUV?
2. **Return time** – What time the following Friday for the return?
3. **Pickup location** – Which city or office in Europe?
4. **Driver age** – How old are you? (SUVs require drivers to be 25+)

What time would you prefer to pick up on Friday?

## Assertions

- [PASS] (attach) `attach.flow_present`
- [PASS] (attach) `attach.flow_enabled`
- [PASS] (attach) `attach.states_non_empty`
- [PASS] (attach) `attach.start_state_real`
- [PASS] (drive) `drive.turn1_flow_active`
- [PASS] (drive) `drive.enters_start_state_first`
- [PASS] (drive) `drive.state_enter_start`
- [PASS] (drive) `drive.transition_check_present`
