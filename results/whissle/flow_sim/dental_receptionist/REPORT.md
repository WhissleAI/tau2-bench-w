# Conversation-flow suite — `dental_receptionist` (real-audio voice)

> **PRELIMINARY** — N = 9 is below the 30-unit threshold for a settled number. Treat every figure below as directional.

## Abstract

Whissle was evaluated on **Whissle conversation-flow suite** in `voice` mode. The headline result is **55.6%** (N = 9 · 2/11 excluded (18.2%) · PRELIMINARY) for task success, 95% CI [26.7%, 81.1%].

Whether a deployed voice agent actually completes its job on a phone call: does it collect what the flow says it must collect, does it handle a caller who answers out of order or refuses to engage, and does it end the call cleanly rather than trailing off. Real audio, real speech recognition, real turn-taking — not a text transcript stand-in.

**2 of 11 units (18.2%) were excluded** before scoring — see §7. Had every excluded unit been scored at the floor of the scale the figure would be 45.49; at the ceiling, 63.67. The true all-11 value lies in that interval, and the headline is not it.

## At a glance

| Field | Value |
|---|---|
| **Task success** | **55.6%** (N = 9 · 2/11 excluded (18.2%) · PRELIMINARY) |
<!-- honesty:allow-context -->
| 95% CI | [26.7%, 81.1%] |
| Attempted / scored / excluded | 11 / 9 / 2 (18.2%) |
| Judge | rule analyzer + LLM grader |
| Mode | `voice` |
| Date | 2026-08-07 |
| Run id | `flow_sim/dental_receptionist` |
| Status | **PRELIMINARY** |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
- **Reached a clean close:** 44.4% [18.9%, 73.3%], N = 9 — taken from the authoritative `flow_end` trace event
- **Flow states visited:** 12, N = 14 — of 14 declared
- **Flow transitions fired:** 15, N = 24 — of 24 declared
<!-- /honesty:allow-context -->

## 1. What was measured, and why

Whether a deployed voice agent actually completes its job on a phone call: does it collect what the flow says it must collect, does it handle a caller who answers out of order or refuses to engage, and does it end the call cleanly rather than trailing off. Real audio, real speech recognition, real turn-taking — not a text transcript stand-in.

**Why this benchmark.** Every text benchmark in this repository removes the two things that break voice products: recognition error and turn-taking. This suite exists to measure what those two things cost, on the flows we actually ship.

## 2. Methodology

| Field | Value |
|---|---|
| Agent under test | the deployed `dental_receptionist` agent, with its real flow definition, prompts and tools |
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
| Agent id | `49d89fc7-fa03-452f-ab64-7386705e9437` |
| Transport endpoint | `LiveKit voice room (POST /api/bench/voice/start)` |
| Mode | `voice` |
| Dataset | scripted caller personas for `dental_receptionist` |
| Dataset size | 11 |
| Upstream | internal — no published equivalent |
| Repo commit at report time | `86b4475` |
| Captured at | 2026-08-07 |
| Run directory | `results/whissle/flow_sim/dental_receptionist` |
| Agent type | dental_receptionist |

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
| Scored | 9 |

Every scenario in the set was run. There is no sampling error here — but there is selection: the set is what we thought to write down, and the transition-coverage table is the honest measure of what it misses.

## 4. Results

**Task success: 55.6%** (N = 9 · 2/11 excluded (18.2%) · PRELIMINARY), 95% CI [26.7%, 81.1%].

| Metric | Value | 95% CI | N | Qualifiers |
|---|---:|---|---:|---|
| **Task success** | **55.6%** | [26.7%, 81.1%] | 9 | N = 9 · 2/11 excluded (18.2%) · PRELIMINARY |
<!-- honesty:allow-context -->
| Reached a clean close | 44.4% | [18.9%, 73.3%] | 9 | taken from the authoritative `flow_end` trace event |
| Flow states visited | 12 | — | 14 | of 14 declared |
| Flow transitions fired | 15 | — | 24 | of 24 declared |
<!-- /honesty:allow-context -->

_The headline row is the claim and carries its qualifiers; the rest are components of it and inherit them._

<!-- honesty:allow-context -->
**Per-scenario outcomes**

| Scenario | Turns | Closed | Goal met | Final state | High-severity findings |
|---|---|---|---|---|---|
| `dental_happy_book` (book) | 0 | **no** | **no** | `None` | 1 |
| `dental_book_specific_time` (book) | 8 | yes | yes | `None` | 0 |
| `dental_reschedule` (reschedule) | 0 | **no** | **no** | `None` | 1 |
| `dental_reschedule_then_cancel` (reschedule) | 8 | **no** | yes | `None` | 0 |
| `dental_cancel` (cancel) | 15 | yes | **no** | `None` | 0 |
| `dental_cancel_no_reason` (cancel) | 8 | **no** | **no** | `None` | 0 |
| `dental_no_slot` (no-slot) | 9 | **no** | yes | `None` | 0 |
| `dental_hours_only` (just-asking) | 2 | yes | yes | `None` | 0 |
| `dental_wrong_info` (wrong info) | 10 | **no** | **no** | `None` | 1 |
| `dental_emergency` (book) | 7 | **no** | **no** | `None` | 1 |
| `dental_message_for_staff` (just-asking) | 4 | yes | yes | `None` | 0 |

Each row is one scripted caller persona driven over real audio. 'Closed' and 'goal met' are independent: an agent can satisfy the caller and never hang up, or hang up having satisfied nobody.
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
**Flow coverage**

| Measure | Covered | Declared | Uncovered |
|---|---|---|---|
| States | 12 | 14 | `reschedule_confirm`, `cancel_confirm` |
| Transitions | 15 | 24 | `t7`, `t11`, `t13b`, `t13c`, `t13d`, `t13e`, `t14`, `t15c`, `t15e` |

An unfired transition is an untested branch. It is not a failure — it is the part of the flow this scenario set never reached, and therefore the part no result here speaks to.
<!-- /honesty:allow-context -->

## 5. Comparison to published baselines

There is no external comparator and there cannot be one: this suite tests our own flow definitions on our own agents. Its value is longitudinal — the same scenario set re-run after a change — and the regression view in the cross-run index is where that comparison lives, not a leaderboard.

_An empty comparison section is a result. Printing a number next to a differently-measured one would not be._

## 6. Failure analysis

| Category | Count | Rate | Severity |
|---|---:|---:|---|
<!-- honesty:allow-context -->
| `stuck_termination` | 3 | 27.3% | medium |
| `infra_fail` | 2 | 18.2% | medium |
| `agent_no_close` | 2 | 18.2% | high |
| `coverage` | 2 | 18.2% | info |
| `stuck_loop` | 1 | 9.1% | medium |
| `premature_termination` | 1 | 9.1% | medium |
| Caller's goal not met | 6 | 54.5% | high |
<!-- /honesty:allow-context -->

### 6.1 `stuck_termination` — 3 of 11

<!-- honesty:allow-context -->
the session stalled without a classified cause
<!-- /honesty:allow-context -->

- **`dental_cancel_no_reason`** — state `cancel` · 8 turns
  > session never reached an end state (cap/stuck); final state 'cancel' with 3 outgoing edge(s) un-fired.
  _artifact:_ `dental_cancel_no_reason_20260807T003801Z.session.json`
- **`dental_no_slot`** — state `book_schedule` · 9 turns
  > session never reached an end state (cap/stuck); final state 'book_schedule' with 2 outgoing edge(s) un-fired.
  _artifact:_ `dental_no_slot_20260807T004551Z.session.json`

### 6.2 `infra_fail` — 2 of 11

- **`dental_happy_book`** — state `—`
  > infrastructure failure — the session could not be measured (attempt 1): VoiceInfraError: bot audio is flowing but no transcript events arrived (data channel dead) — after one ready-handshake retry
  _artifact:_ `dental_happy_book_20260807T000534Z.session.json`
- **`dental_reschedule`** — state `—`
  > infrastructure failure — the session could not be measured (attempt 1): VoiceInfraError: bot audio is flowing but no transcript events arrived (data channel dead) — after one ready-handshake retry
  _artifact:_ `dental_reschedule_20260807T001435Z.session.json`

### 6.3 `agent_no_close` — 2 of 11

<!-- honesty:allow-context -->
the caller's goal was met and the agent never hung up; the call ends because the harness stops driving it, which on a real line is a caller waiting in silence
<!-- /honesty:allow-context -->

- **`dental_emergency`** — state `book_schedule` · 7 turns
  > goal met and the simulated user stayed cooperative for 4 post-goal turn(s), but the agent never delivered its closing / reached flow_end; final state 'book_schedule' with 2 outgoing edge(s) un-fired.
  _artifact:_ `dental_emergency_20260807T010659Z.session.json`
- **`dental_wrong_info`** — state `reschedule_do` · 10 turns
  > goal met and the simulated user stayed cooperative for 4 post-goal turn(s), but the agent never delivered its closing / reached flow_end; final state 'reschedule_do' with 2 outgoing edge(s) un-fired.
  _artifact:_ `dental_wrong_info_20260807T005632Z.session.json`

### 6.4 `coverage` — 2 of 11

<!-- honesty:allow-context -->
branches the scenario set never exercised
<!-- /honesty:allow-context -->

- **`run-level`** — state `—`
  > 2/14 states never entered across the session set: ['reschedule_confirm', 'cancel_confirm'].
  _artifact:_ `SUMMARY.json`
- **`run-level`** — state `—`
  > 9/24 transitions never fired across the session set: ['t7', 't11', 't13b', 't13c', 't13d', 't13e', 't14', 't15c', 't15e'].
  _artifact:_ `SUMMARY.json`

### 6.5 `stuck_loop` — 1 of 11

- **`dental_cancel`** — state `cancel` · 15 turns
  > state 'cancel' was re-entered 3 times (>= 3).
  _artifact:_ `dental_cancel_20260807T002323Z.session.json`

### 6.6 `premature_termination` — 1 of 11

<!-- honesty:allow-context -->
the flow closed the call before the intake it declares was complete — the caller was served politely and the record is short
<!-- /honesty:allow-context -->

- **`dental_cancel`** — state `done` · 15 turns
  > flow reached an end state but the simulated user's goal was NOT met (per the task-success judge).
  _artifact:_ `dental_cancel_20260807T002323Z.session.json`

### 6.7 Caller's goal not met — 6 of 11

<!-- honesty:allow-context -->
The grader judged the caller left without what they came for. This is the headline's complement, and the reason quoted below is the grader's own words.
<!-- /honesty:allow-context -->

- **`dental_cancel`** — cancel · 15 turns · final state `—`
  > The customer's appointment was never actually cancelled in the system; instead, the agent could only pass information to the clinic for a callback, and the call ended with the customer frustrated and uncertain whether their cancellation request would be properly handled.
  _artifact:_ `dental_cancel_20260807T002323Z.session.json`
- **`dental_cancel_no_reason`** — cancel · 8 turns · final state `—`
  > The appointment was not cancelled; the agent determined it was at a different facility (Riverside Medical Center) and directed the customer to contact them directly instead.
  _artifact:_ `dental_cancel_no_reason_20260807T003801Z.session.json`
- **`dental_emergency`** — book · 7 turns · final state `—`
  > The agent never explicitly confirmed the appointment was booked; the call ends with the agent still verifying details rather than providing final confirmation of the 2 PM emergency appointment.
  _artifact:_ `dental_emergency_20260807T010659Z.session.json`

## 7. Exclusions and what they do to the number

<!-- honesty:allow-context -->
| Attempted | Scored | Excluded | Exclusion rate |
|---:|---:|---:|---:|
| 11 | 9 | 2 | **18.2%** |
<!-- /honesty:allow-context -->

**Why each unit was excluded**

| Reason | Count | Share of attempted |
|---|---:|---:|
<!-- honesty:allow-context -->
| `infra_fail` | 2 | 18.2% |
<!-- /honesty:allow-context -->

Verbatim, from the artifacts:

> `VoiceInfraError: bot audio is flowing but no transcript events arrived (data channel dead) — after one ready-handshake retry`

**Effect on interpretation.**

An exclusion rate of 18.2% is not a rounding detail. The headline describes 9 units; it is silent about 2.

Bounding it: if every excluded unit had scored at the floor of the scale, the all-11 figure would be **45.49**; at the ceiling, **63.67**. That interval is wider than the sampling confidence interval, which means the exclusions — not the sample size — are the dominant uncertainty in this run. These are bounds, not estimates: nobody knows how the excluded units would have scored.

The excluded set is also unlikely to be random with respect to difficulty. Transport failures accumulate over turns, so longer and harder units are more exposed to them, and the scored set is plausibly the easier half of what was drawn.

## 8. Limitations and threats to validity

- **tiny n** (high) — N = 9. At this size a single scenario flipping moves the headline by ten points. Every figure here is directional and is labelled PRELIMINARY for that reason.
- **not comparable** (high) — The scenario set is ours, the flows are ours, and the grader is ours. Nothing here can be compared to any published number, and it should never be presented alongside one as if it could.
- **simulated caller** (high) — The caller is a language model speaking through text-to-speech. It has cleaner prosody, no background noise and more patience than a person on a mobile in a car — so recognition error here is a floor, not an estimate.
- **coverage** (medium) — 9 declared transitions never fired across the whole set. Those branches are untested, and a green result says nothing about them.
- **run to run** (medium) — Speech recognition, generation and turn-taking are all stochastic. Two runs of the same scenario set differ; a one-scenario change between runs is noise until it repeats.

- **sample size** (high) — N = 9 is below the 30-unit threshold this reporting layer uses to call a figure settled. The report is labelled PRELIMINARY throughout.

## 9. Reproduction

```bash
uv sync --extra dev --extra voice
./run_flow_sim.sh dental_receptionist
python -m tau2.reporting.cli build results/whissle/flow_sim/dental_receptionist
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
| `R1_headline_requires_n` | pass | headline carries N = 9 everywhere it is stated |
| `R2_judge_independence_disclosed` | pass | not applicable — judge is independent or deterministic |
| `R3_exclusion_rate_adjacent` | pass | 2/11 exclusion rate shown beside the score |
| `R4_preliminary_labelled` | pass | labelled PRELIMINARY |
| `R5_no_provider_names` | pass | no LLM vendor named outside the published-baseline table |
| `R6_comparability_stated` | pass | not applicable — no published baseline is registered |

<!-- generated by tau2.reporting from flow_sim/dental_receptionist; schema tau2.reporting.run_report/v1 -->
