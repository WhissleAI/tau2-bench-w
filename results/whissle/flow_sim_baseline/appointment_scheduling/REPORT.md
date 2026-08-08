# Conversation-flow suite — `appointment_scheduling` (real-audio voice)

> **PRELIMINARY** — N = 5 is below the 30-unit threshold for a settled number. Treat every figure below as directional.

## Abstract

Whissle was evaluated on **Whissle conversation-flow suite** in `voice` mode. The headline result is **20.0%** (N = 5 · PRELIMINARY) for task success, 95% CI [3.6%, 62.4%].

Whether a deployed voice agent actually completes its job on a phone call: does it collect what the flow says it must collect, does it handle a caller who answers out of order or refuses to engage, and does it end the call cleanly rather than trailing off. Real audio, real speech recognition, real turn-taking — not a text transcript stand-in.

## At a glance

| Field | Value |
|---|---|
| **Task success** | **20.0%** (N = 5 · PRELIMINARY) |
<!-- honesty:allow-context -->
| 95% CI | [3.6%, 62.4%] |
| Attempted / scored / excluded | 5 / 5 / 0 (0.0%) |
| Judge | rule analyzer + LLM grader |
| Mode | `voice` |
| Date | 2026-08-05 |
| Run id | `flow_sim_baseline/appointment_scheduling` |
| Status | **PRELIMINARY** |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
- **Reached a clean close:** 40.0% [11.8%, 76.9%], N = 5 — taken from the authoritative `flow_end` trace event
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
| Agent id | `8d88abb4-c792-4d0b-9f38-b98529bce19f` |
| Transport endpoint | `LiveKit voice room (POST /api/bench/voice/start)` |
| Mode | `voice` |
| Dataset | scripted caller personas for `appointment_scheduling` |
| Dataset size | 5 |
| Upstream | internal — no published equivalent |
| Repo commit at report time | `89f2e02` |
| Captured at | 2026-08-05 |
| Run directory | `results/whissle/flow_sim_baseline/appointment_scheduling` |
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
| Population | 5 |
| Requested | 5 |
| Selected | 5 |
| Scored | 5 |

Every scenario in the set was run. There is no sampling error here — but there is selection: the set is what we thought to write down, and the transition-coverage table is the honest measure of what it misses.

## 4. Results

**Task success: 20.0%** (N = 5 · PRELIMINARY), 95% CI [3.6%, 62.4%].

| Metric | Value | 95% CI | N | Qualifiers |
|---|---:|---|---:|---|
| **Task success** | **20.0%** | [3.6%, 62.4%] | 5 | N = 5 · PRELIMINARY |
<!-- honesty:allow-context -->
| Reached a clean close | 40.0% | [11.8%, 76.9%] | 5 | taken from the authoritative `flow_end` trace event |
| Flow states visited | 7 | — | 10 | of 10 declared |
| Flow transitions fired | 6 | — | 13 | of 13 declared |
<!-- /honesty:allow-context -->

_The headline row is the claim and carries its qualifiers; the rest are components of it and inherit them._

<!-- honesty:allow-context -->
**Per-scenario outcomes**

| Scenario | Turns | Closed | Goal met | Final state | Findings | Session |
|---|---|---|---|---|---|---|
| `appt_cancel` (cancel) | 7 | yes | **no** | `—` | 1 | `20260805T175103Z` |
| `appt_double_booking` (new) | 7 | **no** | yes | `—` | 1 | `20260805T180858Z` |
| `appt_new` (new) | 3 | **no** | **no** | `—` | 1 | `20260805T173908Z` |
| `appt_out_of_hours` (out-of-hours) | 9 | **no** | **no** | `—` | 1 | `20260805T175846Z` |
| `appt_reschedule` (reschedule) | 8 | yes | **no** | `—` | 1 | `20260805T174254Z` |

Each row is one scripted caller persona driven over real audio, taken from the most recent session recorded for that scenario. 'Closed' and 'goal met' are independent: an agent can satisfy the caller and never hang up, or hang up having satisfied nobody.
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
**Per-scenario outcomes, as the harness summarised them**

| Scenario | Turns | Closed | Goal met | Final state | High-severity findings |
|---|---|---|---|---|---|
| `appt_new` (new) | 3 | **no** | **no** | `None` | 1 |
| `appt_reschedule` (reschedule) | 8 | yes | **no** | `None` | 0 |
| `appt_cancel` (cancel) | 7 | yes | **no** | `None` | 0 |
| `appt_out_of_hours` (out-of-hours) | 9 | **no** | **no** | `None` | 0 |
| `appt_double_booking` (new) | 7 | **no** | yes | `None` | 0 |

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
| `stuck_termination` | 3 | 60.0% | medium |
| `premature_termination` | 2 | 40.0% | medium |
| `coverage` | 2 | 40.0% | info |
| Caller's goal not met | 4 | 80.0% | high |
<!-- /honesty:allow-context -->

### 6.1 `stuck_termination` — 3 of 5

<!-- honesty:allow-context -->
the session stalled without a classified cause
<!-- /honesty:allow-context -->

- **`appt_double_booking`** — state `capture_need` · 7 turns
  > session never reached an end state (cap/stuck); final state 'capture_need' with 1 outgoing edge(s) un-fired.
  _artifact:_ `appt_double_booking_20260805T180858Z.session.json`
- **`appt_new`** — state `—` · 3 turns
  > session failed to run: ModelError: models/chat failed after 6 attempts: HTTP 502: {"detail":"LLM call failed: all LLM providers failed; last error: [model provider] returned an empty completion"}
  _artifact:_ `appt_new_20260805T173908Z.session.json`

### 6.2 `premature_termination` — 2 of 5

<!-- honesty:allow-context -->
the flow closed the call before the intake it declares was complete — the caller was served politely and the record is short
<!-- /honesty:allow-context -->

- **`appt_cancel`** — state `done` · 7 turns
  > flow reached an end state but the simulated user's goal was NOT met (per the task-success judge).
  _artifact:_ `appt_cancel_20260805T175103Z.session.json`
- **`appt_reschedule`** — state `done` · 8 turns
  > flow reached an end state but the simulated user's goal was NOT met (per the task-success judge).
  _artifact:_ `appt_reschedule_20260805T174254Z.session.json`

### 6.3 `coverage` — 2 of 5

<!-- honesty:allow-context -->
branches the scenario set never exercised
<!-- /honesty:allow-context -->

- **`run-level`** — state `—`
  > 3/10 states never entered across the session set: ['confirm_booked', 'send_confirmation', 'confirm_change'].
  _artifact:_ `SUMMARY.json`
- **`run-level`** — state `—`
  > 7/13 transitions never fired across the session set: ['t_slot_transfer', 't_booked', 't_book_fail', 't_book_flag', 't_sms_done', 't_change_ok', 't_change_confirmed'].
  _artifact:_ `SUMMARY.json`

### 6.4 Caller's goal not met — 4 of 5

<!-- honesty:allow-context -->
The grader judged the caller left without what they came for. This is the headline's complement, and the reason quoted below is the grader's own words.
<!-- /honesty:allow-context -->

- **`appt_cancel`** — cancel · 7 turns · final state `—`
  > The appointment was not cancelled; instead, the customer was transferred to another team without confirmation that the cancellation would be completed.
  _artifact:_ `appt_cancel_20260805T175103Z.session.json`
- **`appt_new`** — new · 3 turns · final state `—`
  > not run
  _artifact:_ `appt_new_20260805T173908Z.session.json`
- **`appt_out_of_hours`** — out-of-hours · 9 turns · final state `—`
  > The customer initially requested late evening or Sunday appointments, was told those weren't available, asked for the nearest in-hours option (5 PM), and accepted it—fulfilling the conditional goal; however, 5 PM is still daytime/early evening rather than the late evening (7-8 PM) or Sunday the customer originally soug
  _artifact:_ `appt_out_of_hours_20260805T175846Z.session.json`

### Sample cases

Picked deterministically from this run's own artifacts — the best and worst by the benchmark's own score — so re-generating the report shows the same cases rather than reshuffling them.

<!-- honesty:allow-context -->
**✓ `appt_double_booking`** — goal met
  - _task:_ persona `new` · 7 caller turns · final state `—`
  > AGENT: Hello! How can I help you today? USER: Hi, I'm trying to book an appointment and I heard you have some slots available next week? AGENT: Hi there! I'm listening — go ahead, what can I help you with? Hi there! I'm here to help you book an appointment. What service would you like to schedule? That's great! What type of appointment are you looking to book — is it for the dental clinic, tut oring, or car rental? That's right, we do have availability next week. What type of appointment would y
  - _why this one:_ The customer asked for a popular slot (2 PM Wednesday afternoon), the agent confirmed it was available, and the customer accepted and confirmed the booking for Wednesday, August 13th at 2 PM.
  - _artifact:_ `appt_double_booking_20260805T180858Z.mix.wav`

**✗ `appt_cancel`** — goal not met
  - _task:_ persona `cancel` · 7 caller turns · final state `—`
  > AGENT: Hello! How can I help you today? USER: Hi, yeah, I need to cancel an appointment I have coming up. Can you help me with that? AGENT: I'm here to help — what do you need? I can help you with that. Can you tell me the date or time of the appointment you'd like to cancel? Of course, I can help you cancel it. What's the phone number the appointment was booked under? Would you like to book an appointment, or is there something else I can help with? USER: It's booked under 555-0147, and the app
  - _why this one:_ The appointment was not cancelled; instead, the customer was transferred to another team without confirmation that the cancellation would be completed.
  - _artifact:_ `appt_cancel_20260805T175103Z.mix.wav`

**✗ `appt_new`** — goal not met
  - _task:_ persona `new` · 3 caller turns · final state `—`
  > AGENT: Hello! How can I help you today? USER: Hi, yeah, I'm calling because I'd like to schedule a consultation appointment for next week if you have anything available. AGENT: Great! I'd be happy to help you book a consultation. Which service would you like to schedule — is it for the dental clinic, the tutoring service, or the car rental business? Got it, next week works. But I still need to know which service you'd like to cons ult about — is it the dental clinic in Patiala, the online tutori
  - _why this one:_ not run
  - _artifact:_ `appt_new_20260805T173908Z.mix.wav`

<!-- /honesty:allow-context -->

## 7. Exclusions and what they do to the number

Nothing was excluded: all 5 attempted units produced a gradable result. The headline denominator is the full attempted set.

## 8. Limitations and threats to validity

- **tiny n** (high) — N = 5. At this size a single scenario flipping moves the headline by ten points. Every figure here is directional and is labelled PRELIMINARY for that reason.
- **not comparable** (high) — The scenario set is ours, the flows are ours, and the grader is ours. Nothing here can be compared to any published number, and it should never be presented alongside one as if it could.
- **simulated caller** (high) — The caller is a language model speaking through text-to-speech. It has cleaner prosody, no background noise and more patience than a person on a mobile in a car — so recognition error here is a floor, not an estimate.
- **coverage** (medium) — 7 declared transitions never fired across the whole set. Those branches are untested, and a green result says nothing about them.
- **run to run** (medium) — Speech recognition, generation and turn-taking are all stochastic. Two runs of the same scenario set differ; a one-scenario change between runs is noise until it repeats.

- **sample size** (high) — N = 5 is below the 30-unit threshold this reporting layer uses to call a figure settled. The report is labelled PRELIMINARY throughout.

## 9. Reproduction

```bash
uv sync --extra dev --extra voice
./run_flow_sim.sh appointment_scheduling
python -m tau2.reporting.cli build results/whissle/flow_sim/appointment_scheduling
```

| Field | Value |
|---|---|
| repo commit at report time | 89f2e02 |
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
| `REPORT.md` | yes | this report |
| `report.json` | yes | machine-readable form of this report |

Every per-case record carries a `diagnostics` block (`tau2.health.diagnostics/v1`) with flow trace, signals, metadata sidecar, tool forensics, provenance and cost — and explicit availability flags, so an absent measurement reads as absent rather than as zero. See `HEALTH_DIAGNOSTICS.md`.

## Appendix B — honesty-rule compliance

These rules are executed against this document, not asserted about it. A failing rule blocks generation.

| Rule | Verdict | Checked |
|---|:---:|---|
| `R1_headline_requires_n` | pass | headline carries N = 5 everywhere it is stated |
| `R2_judge_independence_disclosed` | pass | not applicable — judge is independent or deterministic |
| `R3_exclusion_rate_adjacent` | pass | not applicable — nothing was excluded |
| `R4_preliminary_labelled` | pass | labelled PRELIMINARY |
| `R5_no_provider_names` | pass | no LLM vendor named outside the published-baseline table |
| `R6_comparability_stated` | pass | not applicable — no published baseline is registered |
| `R7_baseline_named` | pass | not applicable — no published baseline is registered |

<!-- generated by tau2.reporting from flow_sim_baseline/appointment_scheduling; schema tau2.reporting.run_report/v1 -->
