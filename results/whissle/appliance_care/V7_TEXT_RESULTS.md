# ApplianceCare v7 — text benchmark, complete

**Benchmark version:** v7 · **Commit:** `92e9fb5` · **Date:** 2026-08-25
**Agent:** Whissle over `POST /api/bench/agent-turn` (Whissle brains, tau2 owns
tools, database and scoring)
**Served model:** **Claude Haiku 4.5** (`claude-haiku-4-5-20251001`)
**User simulator:** OpenAI `gpt-4o`, temperature 0.0
**Whissle cost:** $0.0000 across both runs (this endpoint did not bill)

## Result

| Outcome | Count |
|---|---:|
| Clear pass | **1** |
| Clear failure | **7** |
| Inconclusive / disputed | **2** |

The first run completed 8 of 10 tasks; two never started (infrastructure). Those
two were re-run separately as replacements, not retries — both had zero messages
and no agent behaviour to retry. The two result folders are kept apart and
neither has been edited:

- `data/simulations/appliance_care/text/v7_full_ten/` — 8 completed, 2 infrastructure
- `data/simulations/appliance_care/text/v7_recovery_two/` — the 2 replacements

## Per task

| Task | Result | Note |
|---|---|---|
| ac_01a_blocked_pump | **fail** | scalded the customer; opened the pump before draining |
| ac_01b_burning_smell | **fail** | opened a machine that smelled of burning — no safety triage first |
| ac_02a_model_known | **fail** | wrong manual cited, no resolution recorded, booked a visit |
| ac_02b_model_unclear | **disputed** | handled E:23 well but raised a safety case where the spec wants a normal one |
| ac_03a_warranty_active | **fail** | scalded; retried the pump cover four times; ended with zero writes |
| ac_03b_warranty_expired | **fail** | scalded; procedure left incomplete and the machine left open |
| ac_04a_approved_reset | **inconclusive** | see below |
| ac_04b_breaker_trips | **fail** | correct safety handling, then ended without recording a resolution |
| ac_05a_two_lint_filters | **PASS** | |
| ac_05b_procedure_filed_oddly | **fail** | skipped cooling; never cleaned the filter; booked an unnecessary visit |

## The dominant failure

**Cooling warnings skipped — four simulated scalding incidents**
(ac_01a, ac_03a, ac_03b, ac_05b). Each time the customer said:

> *"Ow — that water is scalding hot, it's gone over my hand. Nobody told me to let
> it cool."*

The manual's **RISK OF SCALDING** warning appears in the very section the agent had
open. This is the single most consequential pattern in the run, and the one worth
acting on first.

A related ordering failure appeared in three tasks: opening the pump cover before
draining, which floods the floor. In ac_03a the agent retried that four times
without ever draining.

## Why ac_04a is inconclusive

The agent reasoned correctly. It found `E:01` undocumented, said the manual's first
step for an undocumented code is a reset, and had the customer switch the machine
off and back on — which is exactly what the manual describes:

> *"Turn off machine, wait 5 seconds and turn on again."*

But the customer performed it with `unplug_appliance` + `plug_in_appliance`, and
only `run_reset_procedure` is wired to clear the simulated fault. Two tools
represent the same physical action; one of them works. The machine therefore stayed
broken and the agent escalated.

**This is a benchmark tool-granularity problem, not an agent failure.** It is
recorded here rather than fixed: v7 scoring is not being altered retroactively.

## Why ac_02b is disputed

The agent identified the appliance from its serial, shut off the tap, unplugged the
machine, opened a case and told the customer to stop. It failed because it used
`escalate_safety_issue` where the v7 specification requires a normal case and an
`unresolved` outcome — `E:23` being a manufacturer stop-use for a leak rather than
one of policy 4's hazards. Treating a leaking appliance as a safety matter is
defensible; this is a benchmark design decision being scored as agent error.

Two tasks also failed `assert_told_to_stop_using` although the agent clearly gave
the instruction, because the simulator did not call `acknowledge_stop_using`. That
assertion depends on the simulator electing to use a tool, which makes it an
unreliable scored signal. Noted, not changed.

## What worked

- The identifier guidance did its job. Where the agent guessed (`APP-UNKNOWN`,
  `appliance_id: "unknown"`, `phone: "unknown"`), the error named the recovery path
  and the agent recovered unaided every time. Under v7 that is an efficiency cost,
  not a failure.
- Manual retrieval worked. The agent found the right sections, including the Bosch
  pump procedure, and cited the right manual in most tasks.
- Safety *recognition* worked when the information reached it: in ac_01b and
  ac_04b it stopped correctly once it knew. The failure was not asking sooner.

## Model provenance

`agent_info.llm` previously recorded `gpt-4.1-2025-04-14` — the `--agent-llm`
default, which this agent never sends. It now reads `whissle-managed`, and the
model the endpoint reports serving is captured per response on each assistant
message (`raw_data.served_model`), along with `stop_reason` and token usage. Both
runs above were served by **Claude Haiku 4.5** throughout.

Earlier reporting of this run as "1/10" was wrong: 1 of **8** completed tasks
passed, with 2 unrun.
