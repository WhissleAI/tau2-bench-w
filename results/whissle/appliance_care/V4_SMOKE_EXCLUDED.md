# Assessment — v4 smoke test, `ac_01a_blocked_filter`

**Verdict: EXCLUDED. Not a measurement of agent performance.**

The raw `results.json` is unmodified and stays that way. It lives at
`data/simulations/appliance_care/text/v4_smoke_ac_01a/results.json`, which is
gitignored — this tracked copy exists so the exclusion is not lost with it.

## What the number was

`reward 0.0` · `DB match: false` · `{DB: 0.0, ENV_ASSERTION: 0.0, ACTION: 0.0}`
Benchmark version **v4**, commit `49192a1`, terminated `user_stop` at 46 messages,
19 Whissle turns, $0.0000 billed.

## Why it is excluded

The task required the customer to clean a drain filter. The benchmark's own manual
extract for that model told the agent no such procedure exists:

> *"That is the only drain-path cleaning this model documents for a customer; there
> is no separate pull-out drain filter cartridge procedure."*
> — `manuals/bosch-wat28400uc-washer.md` §2

The agent read that, relayed it accurately — *"your model doesn't have a separate
drain filter you can clean"* — and escalated to a technician. It was scored 0 for
obeying the manual, which is the behaviour this benchmark exists to reward.

The extract is wrong. The official Bosch manual (`9001002399_H`, sha256
`d177a7b717a8083d`, the document the corpus itself records) documents **"Cleaning
the drain pump" on page 28**, and its `E:18` entry says *"May need to perform pump
maintenance to clear blockage. ~ Cleaning the drain pump; Page 28"*.

A task cannot score an agent against a source the benchmark misreports. The result
measures the corpus defect, not the agent.

## What the run does establish

Infrastructure was sound and two things are worth keeping:

- **The v4 identifier fix worked.** Zero `appliance_id` rejections; the agent
  resolved `APP-001` through `get_customer_by_phone` → `list_owned_appliances`. The
  same task at v1 passed `NW-2200` and was rejected.
- **The user simulator is not the bottleneck.** It acted whenever instructed. It was
  never asked to touch the filter, so the earlier "customer narrates instead of
  acting" hypothesis is not what happened here.

Neither is an agent-quality claim.

## Status

Superseded by the v5 source-grounding correction. Do not quote this reward, and do
not include this run in any average. A valid `ac_01a` measurement requires a rerun
on v5.
