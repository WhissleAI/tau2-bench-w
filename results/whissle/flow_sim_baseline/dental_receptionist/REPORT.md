# Conversation-flow suite — `dental_receptionist` (real-audio voice)

> **PRELIMINARY** — N = 5 is below the 30-unit threshold for a settled number. Treat every figure below as directional.

## Abstract

Whissle was evaluated on **Whissle conversation-flow suite** in `voice` mode. The headline result is **40.0%** (N = 5 · PRELIMINARY) for task success, 95% CI [11.8%, 76.9%].

Whether a deployed voice agent actually completes its job on a phone call: does it collect what the flow says it must collect, does it handle a caller who answers out of order or refuses to engage, and does it end the call cleanly rather than trailing off. Real audio, real speech recognition, real turn-taking — not a text transcript stand-in.

## At a glance

| Field | Value |
|---|---|
| **Task success** | **40.0%** (N = 5 · PRELIMINARY) |
<!-- honesty:allow-context -->
| 95% CI | [11.8%, 76.9%] |
| Attempted / scored / excluded | 5 / 5 / 0 (0.0%) |
| Judge | rule analyzer + LLM grader |
| Mode | `voice` |
| Date | 2026-08-05 |
| Run id | `flow_sim_baseline/dental_receptionist` |
| Status | **PRELIMINARY** |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
- **Reached a clean close:** 0.0% [0.0%, 43.4%], N = 5 — taken from the authoritative `flow_end` trace event
- **Flow states visited:** 6, N = 14 — of 14 declared
- **Flow transitions fired:** 5, N = 24 — of 24 declared
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
| Agent id | `2aa0d091-6205-4490-b5c4-258956868c04` |
| Transport endpoint | `LiveKit voice room (POST /api/bench/voice/start)` |
| Mode | `voice` |
| Dataset | scripted caller personas for `dental_receptionist` |
| Dataset size | 5 |
| Upstream | internal — no published equivalent |
| Repo commit at report time | `89f2e02` |
| Captured at | 2026-08-05 |
| Run directory | `results/whissle/flow_sim_baseline/dental_receptionist` |
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
| Population | 5 |
| Requested | 5 |
| Selected | 5 |
| Scored | 5 |

Every scenario in the set was run. There is no sampling error here — but there is selection: the set is what we thought to write down, and the transition-coverage table is the honest measure of what it misses.

## 4. Results

**Task success: 40.0%** (N = 5 · PRELIMINARY), 95% CI [11.8%, 76.9%].

| Metric | Value | 95% CI | N | Qualifiers |
|---|---:|---|---:|---|
| **Task success** | **40.0%** | [11.8%, 76.9%] | 5 | N = 5 · PRELIMINARY |
<!-- honesty:allow-context -->
| Reached a clean close | 0.0% | [0.0%, 43.4%] | 5 | taken from the authoritative `flow_end` trace event |
| Flow states visited | 6 | — | 14 | of 14 declared |
| Flow transitions fired | 5 | — | 24 | of 24 declared |
<!-- /honesty:allow-context -->

_The headline row is the claim and carries its qualifiers; the rest are components of it and inherit them._

<!-- honesty:allow-context -->
**Per-scenario outcomes**

| Scenario | Turns | Closed | Goal met | Final state | Findings | Session |
|---|---|---|---|---|---|---|
| `dental_cancel` (cancel) | 4 | **no** | yes | `—` | 1 | `20260805T163223Z` |
| `dental_emergency` (book) | 10 | **no** | **no** | `—` | 1 | `20260805T164109Z` |
| `dental_happy_book` (book) | 9 | **no** | yes | `—` | 1 | `20260805T161743Z` |
| `dental_no_slot` (no-slot) | 3 | **no** | **no** | `—` | 1 | `20260805T163704Z` |
| `dental_reschedule` (reschedule) | 4 | **no** | **no** | `—` | 1 | `20260805T162710Z` |

Each row is one scripted caller persona driven over real audio, taken from the most recent session recorded for that scenario. 'Closed' and 'goal met' are independent: an agent can satisfy the caller and never hang up, or hang up having satisfied nobody.
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
**Per-scenario outcomes, as the harness summarised them**

| Scenario | Turns | Closed | Goal met | Final state | High-severity findings |
|---|---|---|---|---|---|
| `dental_happy_book` (book) | 9 | **no** | yes | `None` | 0 |
| `dental_reschedule` (reschedule) | 4 | **no** | **no** | `None` | 1 |
| `dental_cancel` (cancel) | 4 | **no** | yes | `None` | 0 |
| `dental_no_slot` (no-slot) | 3 | **no** | **no** | `None` | 1 |
| `dental_emergency` (book) | 10 | **no** | **no** | `None` | 1 |

Each row is one scripted caller persona driven over real audio. 'Closed' and 'goal met' are independent: an agent can satisfy the caller and never hang up, or hang up having satisfied nobody.
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
**Flow coverage**

| Measure | Covered | Declared | Uncovered |
|---|---|---|---|
| States | 6 | 14 | `send_confirmation`, `reschedule_do`, `reschedule_confirm`, `cancel`, `cancel_do`, `cancel_confirm`, `close`, `done` |
| Transitions | 5 | 24 | `t4`, `t5`, `t7`, `t9`, `t10`, `t11`, `t12`, `t13`, `t13b`, `t13c`, `t13d`, `t13e`, `t14`, `t15`, `t15b`, `t15c`, `t15d`, `t15e`, `t16` |

An unfired transition is an untested branch. It is not a failure — it is the part of the flow this scenario set never reached, and therefore the part no result here speaks to.
<!-- /honesty:allow-context -->

## 5. Comparison to published baselines

There is no external comparator and there cannot be one: this suite tests our own flow definitions on our own agents. Its value is longitudinal — the same scenario set re-run after a change — and the regression view in the cross-run index is where that comparison lives, not a leaderboard.

_An empty comparison section is a result. Printing a number next to a differently-measured one would not be._

## 6. Failure analysis

| Category | Count | Rate | Severity |
|---|---:|---:|---|
<!-- honesty:allow-context -->
| `stuck_termination` | 5 | 100.0% | medium |
| `coverage` | 2 | 40.0% | info |
| Caller's goal not met | 3 | 60.0% | high |
<!-- /honesty:allow-context -->

### 6.1 `stuck_termination` — 5 of 5

<!-- honesty:allow-context -->
the session stalled without a classified cause
<!-- /honesty:allow-context -->

- **`dental_cancel`** — state `reschedule` · 4 turns
  > session never reached an end state (cap/stuck); final state 'reschedule' with 2 outgoing edge(s) un-fired.
  _artifact:_ `dental_cancel_20260805T163223Z.session.json`
- **`dental_emergency`** — state `—` · 10 turns
  > session failed to run: ModelError: models/chat failed after 6 attempts: HTTP 502: {"detail":"LLM call failed: all LLM providers failed; last error: [model provider] returned an empty completion"}
  _artifact:_ `dental_emergency_20260805T164109Z.session.json`

### 6.2 `coverage` — 2 of 5

<!-- honesty:allow-context -->
branches the scenario set never exercised
<!-- /honesty:allow-context -->

- **`run-level`** — state `—`
  > 8/14 states never entered across the session set: ['send_confirmation', 'reschedule_do', 'reschedule_confirm', 'cancel', 'cancel_do', 'cancel_confirm', 'close', 'done'].
  _artifact:_ `SUMMARY.json`
- **`run-level`** — state `—`
  > 19/24 transitions never fired across the session set: ['t4', 't5', 't7', 't9', 't10', 't11', 't12', 't13', 't13b', 't13c', 't13d', 't13e', 't14', 't15', 't15b', 't15c', 't15d', 't15e', 't16'].
  _artifact:_ `SUMMARY.json`

### 6.3 Caller's goal not met — 3 of 5

<!-- honesty:allow-context -->
The grader judged the caller left without what they came for. This is the headline's complement, and the reason quoted below is the grader's own words.
<!-- /honesty:allow-context -->

- **`dental_emergency`** — book · 10 turns · final state `—`
  > not run
  _artifact:_ `dental_emergency_20260805T164109Z.session.json`
- **`dental_no_slot`** — no-slot · 3 turns · final state `—`
  > not run
  _artifact:_ `dental_no_slot_20260805T163704Z.session.json`
- **`dental_reschedule`** — reschedule · 4 turns · final state `—`
  > not run
  _artifact:_ `dental_reschedule_20260805T162710Z.session.json`

### Sample cases

Picked deterministically from this run's own artifacts — the best and worst by the benchmark's own score — so re-generating the report shows the same cases rather than reshuffling them.

<!-- honesty:allow-context -->
**✓ `dental_cancel`** — goal met
  - _task:_ persona `cancel` · 4 caller turns · final state `—`
  > AGENT: Thanks for calling. I can book, move or cancel an appointment, or tell you our opening hours — what can I do for you? Hello! How can I help you today? USER: Hi, I'm calling to cancel my dental appointment next week because I'm going to be out of town for work. AGENT: Got it, so you need to reschedule your appointment for next week. Can you tell me your name and the date of the appointment you want to move? Would you like to book an appointment, or is there something else I can help with?
  - _why this one:_ The customer successfully cancelled their dental appointment for Tuesday, August 14th at 2 PM, provided their name (Meera Iyer) and phone number (408-555-0127), and received confirmation of the cancellation.
  - _artifact:_ `dental_cancel_20260805T163223Z.mix.wav`

**✓ `dental_happy_book`** — goal met
  - _task:_ persona `book` · 9 caller turns · final state `—`
  > AGENT: Hello! How can I help you today? USER: Hi, yes, I'm calling because I need to schedule a dental cleaning. I'm a new patient and I'm looking for something in the morning if you have anything available this week. AGENT: Hi there — no worries, take your time. What do you need help with today? Great, I can book you in for a cleaning. Can I get your name please? Perfect, I'll check what we have this week in the morning. But first, what 's your full name? Would you like to book an appointment,
  - _why this one:_ The customer successfully booked a routine dental cleaning for a weekday morning (Monday, August 10th at 9 AM) as a new patient, provided their name (Priya Nair) and preferred day, and confirmed the first suitable slot offered.
  - _artifact:_ `dental_happy_book_20260805T161743Z.mix.wav`

**✗ `dental_emergency`** — goal not met
  - _task:_ persona `book` · 10 caller turns · final state `—`
  > AGENT: Hello! How can I help you today? USER: Hi, yes, I'm calling because I have really bad tooth pain—like I think my tooth might be broken or something—and I need to see someone today if possible, like as soon as you can get me in. AGENT: Oh, I'm sorry to hear that. What's both ering you — is it pain, or something else you've noticed with the tooth? I understand — let me help you get in today if we can. Before I check what's available, are you a new patient or have you been to our clinic befo
  - _why this one:_ not run
  - _artifact:_ `dental_emergency_20260805T164109Z.mix.wav`

**✗ `dental_no_slot`** — goal not met
  - _task:_ persona `no-slot` · 3 caller turns · final state `—`
  > AGENT: Hello! How can I help you today? USER: Hi, I'm calling to book an appointment and I can only do this Saturday at 8 in the morning, so I need to see if that works for you guys. AGENT: Great! Let me get you scheduled. What day and time work best for you? Saturday at 8 AM — let me check if we have that available. But first, what kind of appointment do you need, and are you a new patient or existing? Would you like to book an appointment, or is there something else I can help with? USER: Yes,
  - _why this one:_ not run
  - _artifact:_ `dental_no_slot_20260805T163704Z.mix.wav`

<!-- /honesty:allow-context -->

## 7. Exclusions and what they do to the number

Nothing was excluded: all 5 attempted units produced a gradable result. The headline denominator is the full attempted set.

## 8. Limitations and threats to validity

- **tiny n** (high) — N = 5. At this size a single scenario flipping moves the headline by ten points. Every figure here is directional and is labelled PRELIMINARY for that reason.
- **not comparable** (high) — The scenario set is ours, the flows are ours, and the grader is ours. Nothing here can be compared to any published number, and it should never be presented alongside one as if it could.
- **simulated caller** (high) — The caller is a language model speaking through text-to-speech. It has cleaner prosody, no background noise and more patience than a person on a mobile in a car — so recognition error here is a floor, not an estimate.
- **coverage** (medium) — 19 declared transitions never fired across the whole set. Those branches are untested, and a green result says nothing about them.
- **run to run** (medium) — Speech recognition, generation and turn-taking are all stochastic. Two runs of the same scenario set differ; a one-scenario change between runs is noise until it repeats.

- **sample size** (high) — N = 5 is below the 30-unit threshold this reporting layer uses to call a figure settled. The report is labelled PRELIMINARY throughout.

## 9. Reproduction

```bash
uv sync --extra dev --extra voice
./run_flow_sim.sh dental_receptionist
python -m tau2.reporting.cli build results/whissle/flow_sim/dental_receptionist
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

<!-- generated by tau2.reporting from flow_sim_baseline/dental_receptionist; schema tau2.reporting.run_report/v1 -->
