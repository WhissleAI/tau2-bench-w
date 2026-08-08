# Conversation-flow suite — `car_rental` (real-audio voice)

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
| Date | 2026-08-07 |
| Run id | `flow_sim/car_rental` |
| Status | **PRELIMINARY** |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
- **Reached a clean close:** 18.2% [5.1%, 47.7%], N = 11 — taken from the authoritative `flow_end` trace event
- **Flow states visited:** 0, N = 11 — of 11 declared
- **Flow transitions fired:** 0, N = 12 — of 12 declared
<!-- /honesty:allow-context -->

## 1. What was measured, and why

Whether a deployed voice agent actually completes its job on a phone call: does it collect what the flow says it must collect, does it handle a caller who answers out of order or refuses to engage, and does it end the call cleanly rather than trailing off. Real audio, real speech recognition, real turn-taking — not a text transcript stand-in.

**Why this benchmark.** Every text benchmark in this repository removes the two things that break voice products: recognition error and turn-taking. This suite exists to measure what those two things cost, on the flows we actually ship.

## 2. Methodology

| Field | Value |
|---|---|
| Agent under test | the deployed `car_rental` agent, with its real flow definition, prompts and tools |
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
| Agent id | `09619055-bea1-442a-b929-fa0db4e37b5f` |
| Transport endpoint | `LiveKit voice room (POST /api/bench/voice/start)` |
| Mode | `voice` |
| Dataset | scripted caller personas for `car_rental` |
| Dataset size | 11 |
| Upstream | internal — no published equivalent |
| Repo commit at report time | `89f2e02` |
| Captured at | 2026-08-07 |
| Run directory | `results/whissle/flow_sim/car_rental` |
| Agent type | car_rental |

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
| Reached a clean close | 18.2% | [5.1%, 47.7%] | 11 | taken from the authoritative `flow_end` trace event |
| Flow states visited | 0 | — | 11 | of 11 declared |
| Flow transitions fired | 0 | — | 12 | of 12 declared |
<!-- /honesty:allow-context -->

_The headline row is the claim and carries its qualifiers; the rest are components of it and inherit them._

<!-- honesty:allow-context -->
**Per-scenario outcomes**

| Scenario | Turns | Closed | Goal met | Final state | Findings | Session |
|---|---|---|---|---|---|---|
| `car_add_driver` (change vehicle) | 0 | **no** | **no** | `—` | 1 | `20260806T153253Z` |
| `car_change_vehicle` (change vehicle) | 8 | yes | yes | `—` | 0 | `20260807T032126Z` |
| `car_happy_economy` (book) | 8 | **no** | yes | `—` | 1 | `20260807T031038Z` |
| `car_happy_suv` (book) | 11 | yes | yes | `—` | 0 | `20260807T030037Z` |
| `car_just_asking` (just-asking) | 7 | **no** | yes | `—` | 1 | `20260807T032908Z` |
| `car_long_term` (book) | 0 | **no** | **no** | `—` | 1 | `20260806T153212Z` |
| `car_luxury_upgrade` (book) | 0 | **no** | **no** | `—` | 1 | `20260806T153355Z` |
| `car_no_availability` (no-availability) | 3 | **no** | yes | `—` | 1 | `20260807T031852Z` |
| `car_one_way` (book) | 0 | **no** | **no** | `—` | 1 | `20260806T153232Z` |
| `car_price_too_high` (just-asking) | 0 | **no** | **no** | `—` | 1 | `20260806T153313Z` |
| `car_wrong_dates` (no-availability) | 0 | **no** | **no** | `—` | 1 | `20260806T153334Z` |

Each row is one scripted caller persona driven over real audio, taken from the most recent session recorded for that scenario. 'Closed' and 'goal met' are independent: an agent can satisfy the caller and never hang up, or hang up having satisfied nobody.
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
**Per-scenario outcomes, as the harness summarised them**

| Scenario | Turns | Closed | Goal met | Final state | High-severity findings |
|---|---|---|---|---|---|
| `car_happy_suv` (book) | 0 | **no** | **no** | `None` | 1 |
| `car_happy_economy` (book) | 0 | **no** | **no** | `None` | 1 |
| `car_no_availability` (no-availability) | 0 | **no** | **no** | `None` | 1 |
| `car_change_vehicle` (change vehicle) | 0 | **no** | **no** | `None` | 1 |
| `car_just_asking` (just-asking) | 0 | **no** | **no** | `None` | 1 |
| `car_long_term` (book) | 0 | **no** | **no** | `None` | 1 |
| `car_one_way` (book) | 0 | **no** | **no** | `None` | 1 |
| `car_add_driver` (change vehicle) | 0 | **no** | **no** | `None` | 1 |
| `car_price_too_high` (just-asking) | 0 | **no** | **no** | `None` | 1 |
| `car_wrong_dates` (no-availability) | 0 | **no** | **no** | `None` | 1 |
| `car_luxury_upgrade` (book) | 0 | **no** | **no** | `None` | 1 |

Each row is one scripted caller persona driven over real audio. 'Closed' and 'goal met' are independent: an agent can satisfy the caller and never hang up, or hang up having satisfied nobody.
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
**Flow coverage**

| Measure | Covered | Declared | Uncovered |
|---|---|---|---|
| States | 0 | 11 | `greet`, `capture_details`, `search_fleet`, `quote`, `mark_selected`, `collect_contact`, `book`, `no_availability`, `confirm`, `close`, `end` |
| Transitions | 0 | 12 | `greet_to_capture`, `capture_to_search`, `search_to_quote`, `quote_selected`, `quote_no_match`, `selected_to_contact`, `contact_to_book`, `book_to_confirm`, `noavail_to_capture`, `noavail_to_end`, `confirm_to_end`, `close_to_end` |

An unfired transition is an untested branch. It is not a failure — it is the part of the flow this scenario set never reached, and therefore the part no result here speaks to.
<!-- /honesty:allow-context -->

## 5. Comparison to published baselines

There is no external comparator and there cannot be one: this suite tests our own flow definitions on our own agents. Its value is longitudinal — the same scenario set re-run after a change — and the regression view in the cross-run index is where that comparison lives, not a leaderboard.

_An empty comparison section is a result. Printing a number next to a differently-measured one would not be._

## 6. Failure analysis

| Category | Count | Rate | Severity |
|---|---:|---:|---|
<!-- honesty:allow-context -->
| `stuck_termination` | 11 | 100.0% | medium |
| `coverage` | 2 | 18.2% | info |
| Caller's goal not met | 6 | 54.5% | high |
<!-- /honesty:allow-context -->

### 6.1 `stuck_termination` — 11 of 11

<!-- honesty:allow-context -->
the session stalled without a classified cause
<!-- /honesty:allow-context -->

- **`car_add_driver`** — state `—`
  > session failed to run: ModelError: models/chat -> HTTP 402: {"detail":"Insufficient credit. Top up your workspace wallet to use the model API."}
  _artifact:_ `car_add_driver_20260806T153253Z.session.json`
- **`car_long_term`** — state `—`
  > session failed to run: ModelError: models/chat -> HTTP 402: {"detail":"Insufficient credit. Top up your workspace wallet to use the model API."}
  _artifact:_ `car_long_term_20260806T153212Z.session.json`

### 6.2 `coverage` — 2 of 11

<!-- honesty:allow-context -->
branches the scenario set never exercised
<!-- /honesty:allow-context -->

- **`run-level`** — state `—`
  > 11/11 states never entered across the session set: ['greet', 'capture_details', 'search_fleet', 'quote', 'mark_selected', 'collect_contact', 'book', 'no_availability', 'confirm', 'close', 'end'].
  _artifact:_ `SUMMARY.json`
- **`run-level`** — state `—`
  > 12/12 transitions never fired across the session set: ['greet_to_capture', 'capture_to_search', 'search_to_quote', 'quote_selected', 'quote_no_match', 'selected_to_contact', 'contact_to_book', 'book_to_confirm', 'noavail_to_capture', 'noavail_to_end', 'confirm_to_end', 'close_to_end'].
  _artifact:_ `SUMMARY.json`

### 6.3 Caller's goal not met — 6 of 11

<!-- honesty:allow-context -->
The grader judged the caller left without what they came for. This is the headline's complement, and the reason quoted below is the grader's own words.
<!-- /honesty:allow-context -->

- **`car_add_driver`** — change vehicle · 0 turns · final state `—`
  > not run
  _artifact:_ `car_add_driver_20260806T153253Z.session.json`
- **`car_long_term`** — book · 0 turns · final state `—`
  > not run
  _artifact:_ `car_long_term_20260806T153212Z.session.json`
- **`car_luxury_upgrade`** — book · 0 turns · final state `—`
  > not run
  _artifact:_ `car_luxury_upgrade_20260806T153355Z.session.json`

### Sample cases

Picked deterministically from this run's own artifacts — the best and worst by the benchmark's own score — so re-generating the report shows the same cases rather than reshuffling them.

<!-- honesty:allow-context -->
**✓ `car_change_vehicle`** — goal met
  - _task:_ persona `change vehicle` · 8 caller turns · final state `—`
  > AGENT: Hello! How can I help you today? USER: Hi, yes, I'm calling because I'd like to book a sedan for next weekend. Do you have anything available? AGENT: Great! I'd be happy to help you book a sedan for next weekend. Can you tell me which dates you need the car — what day would you like to pick it up and what day would you drop it off? Yes, we have a Honda City sedan available — it's automatic, seats five, and costs 3400 rupees per day with a 10000 rupee deposit. Would that work for you? Can
  - _why this one:_ The customer successfully began booking a sedan, changed to wanting an SUV (Hyundai Creta) midway through, and completed the full booking for the SUV with all necessary details confirmed and a reference number provided.
  - _artifact:_ `car_change_vehicle_20260807T032126Z.mix.wav`

**✓ `car_happy_economy`** — goal met
  - _task:_ persona `book` · 8 caller turns · final state `—`
  > AGENT: Hello! How can I help you today? USER: Hi, I'm looking to rent an economy car for a weekend trip — three days starting this Friday. What's your cheapest option? AGENT: Great! I can help you with that. When are you planning to pick up the car? Perfect. So that's this Friday through Sunday — what time would you like to pick up on Friday? Our cheapest option is the Maruti Swift at 1800 rupees per day with a 5000 rupee deposit — that's a manual hatchback with 150 free kilometers daily. Does t
  - _why this one:_ The customer successfully booked the cheapest economy car option (Maruti Swift at 1800 rupees per day) for a 3-day weekend rental (Friday to Sunday) and received a booking confirmation with reference number BK-772963.
  - _artifact:_ `car_happy_economy_20260807T031038Z.mix.wav`

**✗ `car_add_driver`** — goal not met
  - _task:_ persona `change vehicle` · 0 caller turns · final state `—`
  > AGENT: Hello! How can I help you today?
  - _why this one:_ not run
  - _artifact:_ `car_add_driver_20260806T153253Z.mix.wav`

**✗ `car_long_term`** — goal not met
  - _task:_ persona `book` · 0 caller turns · final state `—`
  > AGENT: Hello! How can I help you today?
  - _why this one:_ not run
  - _artifact:_ `car_long_term_20260806T153212Z.mix.wav`

<!-- /honesty:allow-context -->

## 7. Exclusions and what they do to the number

Nothing was excluded: all 11 attempted units produced a gradable result. The headline denominator is the full attempted set.

## 8. Limitations and threats to validity

- **tiny n** (high) — N = 11. At this size a single scenario flipping moves the headline by ten points. Every figure here is directional and is labelled PRELIMINARY for that reason.
- **not comparable** (high) — The scenario set is ours, the flows are ours, and the grader is ours. Nothing here can be compared to any published number, and it should never be presented alongside one as if it could.
- **simulated caller** (high) — The caller is a language model speaking through text-to-speech. It has cleaner prosody, no background noise and more patience than a person on a mobile in a car — so recognition error here is a floor, not an estimate.
- **coverage** (medium) — 12 declared transitions never fired across the whole set. Those branches are untested, and a green result says nothing about them.
- **run to run** (medium) — Speech recognition, generation and turn-taking are all stochastic. Two runs of the same scenario set differ; a one-scenario change between runs is noise until it repeats.

- **sample size** (high) — N = 11 is below the 30-unit threshold this reporting layer uses to call a figure settled. The report is labelled PRELIMINARY throughout.

## 9. Reproduction

```bash
uv sync --extra dev --extra voice
./run_flow_sim.sh car_rental
python -m tau2.reporting.cli build results/whissle/flow_sim/car_rental
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
| `R1_headline_requires_n` | pass | headline carries N = 11 everywhere it is stated |
| `R2_judge_independence_disclosed` | pass | not applicable — judge is independent or deterministic |
| `R3_exclusion_rate_adjacent` | pass | not applicable — nothing was excluded |
| `R4_preliminary_labelled` | pass | labelled PRELIMINARY |
| `R5_no_provider_names` | pass | no LLM vendor named outside the published-baseline table |
| `R6_comparability_stated` | pass | not applicable — no published baseline is registered |
| `R7_baseline_named` | pass | not applicable — no published baseline is registered |

<!-- generated by tau2.reporting from flow_sim/car_rental; schema tau2.reporting.run_report/v1 -->
