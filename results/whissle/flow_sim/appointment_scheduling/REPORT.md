# Conversation-flow suite — `appointment_scheduling` (real-audio voice)

> **PRELIMINARY** — N = 11 is below the 30-unit threshold for a settled number. Treat every figure below as directional.

## Abstract

Whissle was evaluated on **Whissle conversation-flow suite** in `voice` mode. The headline result is **45.5%** (N = 11 · PRELIMINARY) for task success, 95% CI [21.3%, 72.0%].

Whether a deployed voice agent actually completes its job on a phone call: does it collect what the flow says it must collect, does it handle a caller who answers out of order or refuses to engage, and does it end the call cleanly rather than trailing off. Real audio, real speech recognition, real turn-taking — not a text transcript stand-in.

## At a glance

| Field | Value |
|---|---|
| **Task success** | **45.5%** (N = 11 · PRELIMINARY) |
<!-- honesty:allow-context -->
| 95% CI | [21.3%, 72.0%] |
| Attempted / scored / excluded | 11 / 11 / 0 (0.0%) |
| Judge | rule analyzer + LLM grader |
| Mode | `voice` |
| Date | 2026-08-06 |
| Run id | `flow_sim/appointment_scheduling` |
| Status | **PRELIMINARY** |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
- **Reached a clean close:** 27.3% [9.7%, 56.6%], N = 11 — taken from the authoritative `flow_end` trace event
- **Flow states visited:** 7, N = 10 — of 10 declared
- **Flow transitions fired:** 6, N = 13 — of 13 declared
<!-- /honesty:allow-context -->

## 1. What was measured, and why

Whether a deployed voice agent actually completes its job on a phone call: does it collect what the flow says it must collect, does it handle a caller who answers out of order or refuses to engage, and does it end the call cleanly rather than trailing off. Real audio, real speech recognition, real turn-taking — not a text transcript stand-in.

**Why this benchmark.** Every text benchmark in this repository removes the two things that break voice products: recognition error and turn-taking. This suite exists to measure what those two things cost, on the flows we actually ship.

## 2. Methodology

| Field | Value |
|---|---|
| Agent under test | the deployed `appointment_scheduling` agent, with its real flow definition, prompts and tools |
| Mode | real-audio voice over a LiveKit room |
| Endpoint | `POST /api/bench/voice/start` → LiveKit room |
| Prompt handling | none — this is the shipped configuration, unmodified; that is the point of the suite and the reason its numbers are not comparable to a paper's |
| Caller | an LLM user-simulator driving a persona and a goal, speaking through text-to-speech into the room |
| Turn limit | a per-scenario turn budget plus a post-goal allowance; hitting the cap is recorded as `turn_cap_exceeded`, not silently truncated |
| Tools bound | the agent's own production tools, gated by its own flow state machine |
| Scoring rule | task success is judged per scenario against the caller's goal; clean close is read from the engine's `flow_end` trace event, not inferred from the transcript |

**Scoring rule.** task success = graded goal-met / executed sessions; clean close = sessions emitting `flow_end` / executed sessions

## 3. Setup and provenance

| Field | Value |
|---|---|
| Agent id | `9c290a31-beb3-4688-a491-b18e4c617f1b` |
| Transport endpoint | `LiveKit voice room (POST /api/bench/voice/start)` |
| Mode | `voice` |
| Dataset | scripted caller personas for `appointment_scheduling` |
| Dataset size | 11 |
| Upstream | internal — no published equivalent |
| Repo commit at report time | `86b4475` |
| Captured at | 2026-08-06 |
| Run directory | `results/whissle/flow_sim/appointment_scheduling` |
| Agent type | appointment_scheduling |

### 3.1 Judge and its independence

| Field | Value |
|---|---|
| Grading kind | rule analyzer |
| Independent of the agent's vendor | n/a — no judge model is called |

<!-- honesty:allow-providers -->
> Two graders, deliberately different in kind: a rule analyzer reads the engine's own flow trace (deterministic — it cannot be talked into a verdict), and an LLM grader judges goal satisfaction from the transcript. Where they disagree, the trace wins on questions of what happened and the grader wins on questions of whether the caller was served.
<!-- /honesty:allow-providers -->

### 3.2 Sampling and population

| Field | Value |
|---|---|
| Method | hand-authored scenario set, exhaustive (not sampled) |
| Population | 11 |
| Requested | 11 |
| Selected | 11 |
| Scored | 11 |

Every scenario in the set was run. There is no sampling error here — but there is selection: the set is what we thought to write down, and the transition-coverage table is the honest measure of what it misses.

## 4. Results

**Task success: 45.5%** (N = 11 · PRELIMINARY), 95% CI [21.3%, 72.0%].

| Metric | Value | 95% CI | N | Qualifiers |
|---|---:|---|---:|---|
| **Task success** | **45.5%** | [21.3%, 72.0%] | 11 | N = 11 · PRELIMINARY |
<!-- honesty:allow-context -->
| Reached a clean close | 27.3% | [9.7%, 56.6%] | 11 | taken from the authoritative `flow_end` trace event |
| Flow states visited | 7 | — | 10 | of 10 declared |
| Flow transitions fired | 6 | — | 13 | of 13 declared |
<!-- /honesty:allow-context -->

_The headline row is the claim and carries its qualifiers; the rest are components of it and inherit them._

<!-- honesty:allow-context -->
**Per-scenario outcomes**

| Scenario | Turns | Closed | Goal met | Final state | High-severity findings |
|---|---|---|---|---|---|
| `appt_new` (new) | 9 | **no** | yes | `None` | 1 |
| `appt_new_specific` (new) | 7 | **no** | yes | `None` | 1 |
| `appt_reschedule` (reschedule) | 6 | **no** | **no** | `None` | 1 |
| `appt_reschedule_earlier` (reschedule) | 11 | yes | **no** | `None` | 0 |
| `appt_cancel` (cancel) | 12 | yes | **no** | `None` | 0 |
| `appt_cancel_and_rebook` (reschedule) | 11 | yes | **no** | `None` | 0 |
| `appt_out_of_hours` (out-of-hours) | 8 | **no** | yes | `None` | 1 |
| `appt_just_hours` (out-of-hours) | 3 | **no** | yes | `None` | 1 |
| `appt_double_booking` (new) | 4 | **no** | **no** | `None` | 0 |
| `appt_wrong_details` (reschedule) | 8 | **no** | **no** | `None` | 1 |
| `appt_group` (new) | 9 | **no** | yes | `None` | 1 |

Each row is one scripted caller persona driven over real audio. 'Closed' and 'goal met' are independent: an agent can satisfy the caller and never hang up, or hang up having satisfied nobody.
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
**Flow coverage**

| Measure | Covered | Declared | Uncovered |
|---|---|---|---|
| States | 7 | 10 | `confirm_booked`, `send_confirmation`, `confirm_change` |
| Transitions | 6 | 13 | `t_slot_transfer`, `t_booked`, `t_book_fail`, `t_book_flag`, `t_sms_done`, `t_change_ok`, `t_change_confirmed` |

An unfired transition is an untested branch. It is not a failure — it is the part of the flow this scenario set never reached, and therefore the part no result here speaks to.
<!-- /honesty:allow-context -->

## 5. Comparison to published baselines

There is no external comparator and there cannot be one: this suite tests our own flow definitions on our own agents. Its value is longitudinal — the same scenario set re-run after a change — and the regression view in the cross-run index is where that comparison lives, not a leaderboard.

_An empty comparison section is a result. Printing a number next to a differently-measured one would not be._

## 6. Failure analysis

| Category | Count | Rate | Severity |
|---|---:|---:|---|
<!-- honesty:allow-context -->
| `agent_no_close` | 7 | 63.6% | high |
| `premature_termination` | 3 | 27.3% | medium |
| `coverage` | 2 | 18.2% | info |
| `stuck_termination` | 1 | 9.1% | medium |
| Caller's goal not met | 6 | 54.5% | high |
<!-- /honesty:allow-context -->

### 6.1 `agent_no_close` — 7 of 11

<!-- honesty:allow-context -->
the caller's goal was met and the agent never hung up; the call ends because the harness stops driving it, which on a real line is a caller waiting in silence
<!-- /honesty:allow-context -->

- **`appt_group`** — state `book` · 9 turns
  > goal met and the simulated user stayed cooperative for 4 post-goal turn(s), but the agent never delivered its closing / reached flow_end; the agent replied EMPTY on turn(s) [8]; final state 'book' with 2 outgoing edge(s) un-fired.
  _artifact:_ `appt_group_20260806T224006Z.session.json`
- **`appt_just_hours`** — state `capture_need` · 3 turns
  > goal met and the simulated user stayed cooperative for 2 post-goal turn(s), but the agent never delivered its closing / reached flow_end; final state 'capture_need' with 1 outgoing edge(s) un-fired.
  _artifact:_ `appt_just_hours_20260806T222510Z.session.json`

### 6.2 `premature_termination` — 3 of 11

<!-- honesty:allow-context -->
the flow closed the call before the intake it declares was complete — the caller was served politely and the record is short
<!-- /honesty:allow-context -->

- **`appt_cancel`** — state `done` · 12 turns
  > flow reached an end state but the simulated user's goal was NOT met (per the task-success judge).
  _artifact:_ `appt_cancel_20260806T215650Z.session.json`
- **`appt_cancel_and_rebook`** — state `done` · 11 turns
  > flow reached an end state but the simulated user's goal was NOT met (per the task-success judge).
  _artifact:_ `appt_cancel_and_rebook_20260806T220743Z.session.json`

### 6.3 `coverage` — 2 of 11

<!-- honesty:allow-context -->
branches the scenario set never exercised
<!-- /honesty:allow-context -->

- **`run-level`** — state `—`
  > 3/10 states never entered across the session set: ['confirm_booked', 'send_confirmation', 'confirm_change'].
  _artifact:_ `SUMMARY.json`
- **`run-level`** — state `—`
  > 7/13 transitions never fired across the session set: ['t_slot_transfer', 't_booked', 't_book_fail', 't_book_flag', 't_sms_done', 't_change_ok', 't_change_confirmed'].
  _artifact:_ `SUMMARY.json`

### 6.4 `stuck_termination` — 1 of 11

<!-- honesty:allow-context -->
the session stalled without a classified cause
<!-- /honesty:allow-context -->

- **`appt_double_booking`** — state `capture_need` · 4 turns
  > session never reached an end state (cap/stuck); final state 'capture_need' with 1 outgoing edge(s) un-fired.
  _artifact:_ `appt_double_booking_20260806T222751Z.session.json`

### 6.5 Caller's goal not met — 6 of 11

<!-- honesty:allow-context -->
The grader judged the caller left without what they came for. This is the headline's complement, and the reason quoted below is the grader's own words.
<!-- /honesty:allow-context -->

- **`appt_cancel`** — cancel · 12 turns · final state `—`
  > The appointment was never actually canceled; the agent failed to locate it under either phone number provided and then transferred the call without confirming the cancellation or even acknowledging the customer's name.
  _artifact:_ `appt_cancel_20260806T215650Z.session.json`
- **`appt_cancel_and_rebook`** — reschedule · 11 turns · final state `—`
  > The customer did not complete the goal because the appointment was never cancelled and the new booking was never completed; the call ended with the customer hanging up while on hold during a transfer.
  _artifact:_ `appt_cancel_and_rebook_20260806T220743Z.session.json`
- **`appt_double_booking`** — new · 4 turns · final state `—`
  > The customer called the wrong business (a salon instead of a service that doesn't offer haircuts) and was unable to book any appointment, let alone accept an alternative slot option.
  _artifact:_ `appt_double_booking_20260806T222751Z.session.json`

## 7. Exclusions and what they do to the number

Nothing was excluded: all 11 attempted units produced a gradable result. The headline denominator is the full attempted set.

## 8. Limitations and threats to validity

- **tiny n** (high) — N = 11. At this size a single scenario flipping moves the headline by ten points. Every figure here is directional and is labelled PRELIMINARY for that reason.
- **not comparable** (high) — The scenario set is ours, the flows are ours, and the grader is ours. Nothing here can be compared to any published number, and it should never be presented alongside one as if it could.
- **simulated caller** (high) — The caller is a language model speaking through text-to-speech. It has cleaner prosody, no background noise and more patience than a person on a mobile in a car — so recognition error here is a floor, not an estimate.
- **coverage** (medium) — 7 declared transitions never fired across the whole set. Those branches are untested, and a green result says nothing about them.
- **run to run** (medium) — Speech recognition, generation and turn-taking are all stochastic. Two runs of the same scenario set differ; a one-scenario change between runs is noise until it repeats.

- **sample size** (high) — N = 11 is below the 30-unit threshold this reporting layer uses to call a figure settled. The report is labelled PRELIMINARY throughout.

## 9. Reproduction

```bash
uv sync --extra dev --extra voice
./run_flow_sim.sh appointment_scheduling
python -m tau2.reporting.cli build results/whissle/flow_sim/appointment_scheduling
```

| Field | Value |
|---|---|
| repo commit at report time | 86b4475 |
| extras required | voice (LiveKit, audio codecs) |

- Audio is captured per session (`*.caller.wav`, `*.bot.wav`, `*.mix.wav`) — a disputed grader verdict can be settled by listening.
- The directory accumulates every historical run of the same scenario; this report covers the latest session per task id.

## Appendix A — raw artifacts

| Path | Present | What it is |
|---|:---:|---|
| `SUMMARY.json` | yes | run-level aggregation and coverage |
| `SUMMARY.md` | yes | the harness's own short summary |
| `*.session.json` | **missing** | per-session sidecar: turns, flow trace, findings |
| `*.mix.wav` | **missing** | the recorded call |
| `REPORT.md` | **missing** | this report |
| `report.json` | **missing** | machine-readable form of this report |

Every per-case record carries a `diagnostics` block (`tau2.health.diagnostics/v1`) with flow trace, signals, metadata sidecar, tool forensics, provenance and cost — and explicit availability flags, so an absent measurement reads as absent rather than as zero. See `HEALTH_DIAGNOSTICS.md`.

## Appendix B — honesty-rule compliance

These rules are executed against this document, not asserted about it. A failing rule blocks generation.

| Rule | Verdict | Checked |
|---|:---:|---|
| `R1_headline_requires_n` | pass | headline carries N = 11 everywhere it is stated |
| `R2_judge_independence_disclosed` | pass | not applicable — judge is independent or deterministic |
| `R3_exclusion_rate_adjacent` | pass | not applicable — nothing was excluded |
| `R4_preliminary_labelled` | pass | labelled PRELIMINARY |
| `R5_no_provider_names` | pass | no LLM vendor named outside the published-baseline table |
| `R6_comparability_stated` | pass | not applicable — no published baseline is registered |

<!-- generated by tau2.reporting from flow_sim/appointment_scheduling; schema tau2.reporting.run_report/v1 -->
