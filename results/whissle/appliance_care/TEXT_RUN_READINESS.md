# Standard text benchmark — readiness, and what the earlier run showed

**Date:** 2026-08-24 · **Benchmark version: v4**

This is an analysis of stored run artifacts plus code inspection. **No live Whissle
benchmark was run** — no task was executed against the Whissle backend, and no
Whissle object was created, modified or deleted.

One paid test did run, and it is not a Whissle one. `test_barge_in_baseline[openai]`
in the deterministic suite opens a live **OpenAI Realtime** WebSocket, and it
**failed** — the remote service returned no audio events across all seven ticks, so
the assertion `agent_audio_bytes > 0` ("Agent never started speaking") did not hold.
It executed several times while being diagnosed, each time billable against
`OPENAI_API_KEY`. It was then left alone deliberately rather than retried further.

Its relationship to this work is: none that could be found. The change set touches
one Pydantic field, one `Environment` method, and the `appliance_care` domain; the
test connects to OpenAI and asserts a remote model produced audio. It is recorded
here as an unresolved flake rather than dismissed.

## The architecture, verified against the code

The standard text benchmark does **not** use the saved Whissle flow or custom HTTP
tools. Confirmed by reading the source, not the comments:

| Step | Where | What happens |
|---|---|---|
| 1 | `agent/whissle_agent.py:_turn` | POSTs `{agent_id, messages, tools, system}` to `/api/bench/agent-turn` — tau2's own tool schemas and domain policy, every request |
| 2 | Whissle backend | Acts purely as the brain; returns `reply` or `tool_calls` |
| 3 | `orchestrator.py:_execute_tool_calls` | Runs each call via `environment.get_response` — **tau2 executes, not Whissle** |
| 4 | `evaluator_env.py` | Hashes the tau2 database and checks env assertions |

Whissle is the brain. Tau owns the tools, the database, and the score. The agent's
own prompt, flow, and attached tools are deliberately bypassed on this path.

**So the attached-custom-tool problem does not block this path.** It blocks the
separate saved-flow / product-integration path, which is what the bridge and the
`whissle_flow` adapter were built for. Documents that framed it more broadly have
been corrected.

## Benchmark versioning

`APPLIANCE_CARE_VERSION` in `domains/appliance_care/utils.py` is bumped whenever a
change could move a score — the manual corpus, the task set, the database, the
policy, or **the tool descriptions**.

Tool descriptions are versioned material because they are part of the prompt every
agent sees. Clarifying one changes what the agent is being asked, so a run before
and a run after are not measuring quite the same thing.

| Version | Date | Change |
|---|---|---|
| v1 | 2026-08-19 | invented brands (Northwind / Larkfield / Vantis) |
| v2 | 2026-08-20 | real Bosch, LG and Miele manual extracts replace v1 |
| v3 | 2026-08-21 | `ac_05b` corrected — the Miele WWB 020 does document a customer drain-filter clean |
| v4 | 2026-08-24 | `appliance_id` contract stated in tool descriptions and lookup errors |

**Two rules follow.** Scores are comparable only between runs carrying the same
version, and every agent in a comparison must run against the *same* version — the
same manuals, tasks, database, policy and tool descriptions. Re-running one agent on
v4 and setting it beside another agent's v1 number is not a comparison. Report the
version beside any number.

## The earlier 0/10 run is historical, and not comparable to the current benchmark

`data/simulations/appliance_care/text/{ac_01a_smoke,remaining_nine}` record
`git_commit e0d6798` — **v1**. The stored `environment_info.policy` names Northwind,
Larkfield and Vantis; the artifacts mention Bosch and Miele exactly **zero** times.

It was a real run and its 0/10 was a real result *for v1*. It is retained as
evidence. But it was measured against a different corpus, different tasks and
different tool descriptions, so it is not a baseline for v4 and should not be quoted
as one.

## What that run surfaced

Three things are visible in the artifacts. **How much each contributed to the 0/10 —
and how much was the agent making mistakes it would make anyway — cannot be
separated from these artifacts.** That needs a clean rerun on v4.

### 1. A 40-step budget was insufficient for five of the ten tasks

`ac_02a`, `ac_03a`, `ac_03b`, `ac_05a` and `ac_05b` all terminated `max_steps`, at
41–44 messages. The run config passed `max_steps: 40`; the framework default is
**200** (`config.py:DEFAULT_MAX_STEPS`).

In these five, the conversation was still progressing when it ran out — the agent
was mid-procedure, not looping. Whether 40 is enough in general is not something one
run of ten tasks can settle; what it shows is that 40 was too few for these five.

**Nothing was changed for this.** It is a run-configuration choice: use the default.

### 2. Twelve tool calls were rejected on the identifier contract — clarified in v4

Every rejected call passed a different kind of identifier as `appliance_id`:

| Passed | What it actually was | Tasks |
|---|---|---|
| `NW-2200` | a model number | 01a, 01b, 03a, 03b, 04a, 04b |
| `NW22-4471-8890`, `VT50-6604-1128` | serial numbers | 04b, 05b |
| `"unknown"` | a literal placeholder | 02b |

The agent used what the customer had read off the label. At v1 the error said only
`No appliance found with id NW-2200` — the symptom, with no way back — and the
parameter description read `"The machine."`

**This is a benchmark contract clarification, made after that run.** The domain
policy always required resolving the appliance through the customer record; what was
missing was any statement of it where the agent actually reads — the tool schema and
the error. v4 adds:

- `_appliance_id_error` identifies whether the value supplied was a **model number**
  or a **serial**, and names the path: `get_customer_by_phone` →
  `list_owned_appliances` → `APP-001`.
- All five raise sites route through it, including `check_warranty`, which at v1
  reported "No warranty record for appliance unknown" — a coverage problem the
  customer did not have.
- Every `appliance_id` description carries the example id and the tool that yields it.

Whether this changes scores is an open question, not a claim. It is a change to the
prompt surface, which is exactly why it forces a version bump: any agent measured on
v4 must be measured against these descriptions, and so must every agent it is
compared with.

### 3. The customer rarely reached the tool that clears the fault

Across the ten runs only `ac_02b` ever called `clean_drain_filter`. Elsewhere the
simulated customer read the label and the display, then described compliance in prose
— "I've opened the kick-panel flap and placed a tray underneath" — while the hidden
fault stayed set. No filter cleaned means `problem_still_present`, which fails the
assertions.

This overlaps with cause 1: some of those conversations were cut off before reaching
the step. How much is step budget, how much is the simulator's tool coverage, and how
much is the agent not driving the customer to act, cannot be told apart here.

**Deliberately not changed.** Adjusting the simulator before observing a valid v4 run
would be tuning against a result we have not seen.

## Before rerunning

- [x] v2 — corpus replaced with real Bosch/LG/Miele extracts
- [x] v3 — `ac_05b` corrected
- [x] v4 — identifier contract stated in the tool surface
- [x] 138 local tests pass
- [ ] Run at the default `max_steps` (200), not 40
- [ ] Record the benchmark version with the results
- [ ] Re-measure cause 3 against that run before touching the simulator

## Still open, and unaffected by any of this

Custom HTTP tools on the saved-flow path (Ticket B), and voice, which remains
untested on every path.
