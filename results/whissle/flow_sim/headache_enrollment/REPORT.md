# Conversation-flow suite — `headache_enrollment` (real-audio voice)

> **PRELIMINARY** — N = 9 is below the 30-unit threshold for a settled number; `SUMMARY.json` describes a 1-session invocation (`20260807T045436Z`), but 10 scenarios have sessions on disk. This report covers the latest session of each scenario, computed from the sidecars; the coverage and finding roll-ups that only the summary carries are omitted rather than quoted out of scope.. Treat every figure below as directional.

> **Partial run.** `SUMMARY.json` describes a 1-session invocation (`20260807T045436Z`), but 10 scenarios have sessions on disk. This report covers the latest session of each scenario, computed from the sidecars; the coverage and finding roll-ups that only the summary carries are omitted rather than quoted out of scope.

## Abstract

Whissle was evaluated on **Whissle conversation-flow suite** in `voice` mode. The headline result is **77.8%** (N = 9 · 1/10 excluded (10.0%) · PRELIMINARY) for task success, 95% CI [45.3%, 93.7%].

Whether a deployed voice agent actually completes its job on a phone call: does it collect what the flow says it must collect, does it handle a caller who answers out of order or refuses to engage, and does it end the call cleanly rather than trailing off. Real audio, real speech recognition, real turn-taking — not a text transcript stand-in.

**1 of 10 units (10.0%) were excluded** before scoring — see §7. Had every excluded unit been scored at the floor of the scale the figure would be 70.02; at the ceiling, 80.02. The true all-10 value lies in that interval, and the headline is not it.

## At a glance

| Field | Value |
|---|---|
| **Task success** | **77.8%** (N = 9 · 1/10 excluded (10.0%) · PRELIMINARY) |
<!-- honesty:allow-context -->
| 95% CI | [45.3%, 93.7%] |
| Attempted / scored / excluded | 10 / 9 / 1 (10.0%) |
| Judge | rule analyzer + LLM grader |
| Mode | `voice` |
| Date | 2026-08-07 |
| Run id | `flow_sim/headache_enrollment` |
| Status | **PRELIMINARY** |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
- **Reached a clean close:** 44.4% [18.9%, 73.3%], N = 9 — taken from the authoritative `flow_end` trace event
<!-- /honesty:allow-context -->

## 1. What was measured, and why

Whether a deployed voice agent actually completes its job on a phone call: does it collect what the flow says it must collect, does it handle a caller who answers out of order or refuses to engage, and does it end the call cleanly rather than trailing off. Real audio, real speech recognition, real turn-taking — not a text transcript stand-in.

**Why this benchmark.** Every text benchmark in this repository removes the two things that break voice products: recognition error and turn-taking. This suite exists to measure what those two things cost, on the flows we actually ship.

## 2. Methodology

| Field | Value |
|---|---|
| Agent under test | the deployed `headache_enrollment` agent, with its real flow definition, prompts and tools |
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
| Agent id | `7aaef348-6e8a-4dd7-a09b-68bd11d7643d` |
| Transport endpoint | `LiveKit voice room (POST /api/bench/voice/start)` |
| Mode | `voice` |
| Dataset | scripted caller personas for `headache_enrollment` |
| Dataset size | 10 |
| Upstream | internal — no published equivalent |
| Repo commit at report time | `89f2e02` |
| Captured at | 2026-08-07 |
| Run directory | `results/whissle/flow_sim/headache_enrollment` |
| Agent type | headache_enrollment |

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
| Population | 10 |
| Requested | 10 |
| Selected | 10 |
| Scored | 9 |

Every scenario in the set was run. There is no sampling error here — but there is selection: the set is what we thought to write down, and the transition-coverage table is the honest measure of what it misses.

## 4. Results

**Task success: 77.8%** (N = 9 · 1/10 excluded (10.0%) · PRELIMINARY), 95% CI [45.3%, 93.7%].

| Metric | Value | 95% CI | N | Qualifiers |
|---|---:|---|---:|---|
| **Task success** | **77.8%** | [45.3%, 93.7%] | 9 | N = 9 · 1/10 excluded (10.0%) · PRELIMINARY |
<!-- honesty:allow-context -->
| Reached a clean close | 44.4% | [18.9%, 73.3%] | 9 | taken from the authoritative `flow_end` trace event |
<!-- /honesty:allow-context -->

_The headline row is the claim and carries its qualifiers; the rest are components of it and inherit them._

<!-- honesty:allow-context -->
**Per-scenario outcomes**

| Scenario | Turns | Closed | Goal met | Final state | Findings | Session |
|---|---|---|---|---|---|---|
| `hx_cluster_signs` (autonomic) | 7 | **no** | yes | `—` | 1 | `20260807T005330Z` |
| `hx_happy_full` (full_intake) | 0 | **no** | **no** | `—` | 1 | `20260807T044115Z` |
| `hx_hormonal` (hormonal) | 7 | **no** | yes | `—` | 1 | `20260807T010615Z` |
| `hx_keep_it_quick` (time_pressured) | 4 | **no** | **no** | `—` | 1 | `20260807T010113Z` |
| `hx_med_overuse` (medication) | 6 | **no** | **no** | `—` | 1 | `20260807T011410Z` |
| `hx_migraine_classic` (migraine) | 18 | yes | yes | `—` | 0 | `20260807T044526Z` |
| `hx_out_of_order` (out_of_order) | 8 | yes | yes | `—` | 0 | `20260807T003622Z` |
| `hx_red_flag_urgent` (urgent_escalation) | 2 | yes | yes | `—` | 0 | `20260807T000603Z` |
| `hx_skips_topics` (skips) | 13 | yes | yes | `—` | 0 | `20260807T000758Z` |
| `hx_unsure` (unsure) | 18 | **no** | yes | `—` | 1 | `20260807T002014Z` |

Each row is one scripted caller persona driven over real audio, taken from the most recent session recorded for that scenario. 'Closed' and 'goal met' are independent: an agent can satisfy the caller and never hang up, or hang up having satisfied nobody.
<!-- /honesty:allow-context -->

## 5. Comparison to published baselines

There is no external comparator and there cannot be one: this suite tests our own flow definitions on our own agents. Its value is longitudinal — the same scenario set re-run after a change — and the regression view in the cross-run index is where that comparison lives, not a leaderboard.

_An empty comparison section is a result. Printing a number next to a differently-measured one would not be._

## 6. Failure analysis

| Category | Count | Rate | Severity |
|---|---:|---:|---|
<!-- honesty:allow-context -->
| `agent_no_close` | 3 | 30.0% | high |
| `stuck_termination` | 2 | 20.0% | medium |
| `infra_fail` | 1 | 10.0% | medium |
| Caller's goal not met | 3 | 30.0% | high |
<!-- /honesty:allow-context -->

### 6.1 `agent_no_close` — 3 of 10

<!-- honesty:allow-context -->
the caller's goal was met and the agent never hung up; the call ends because the harness stops driving it, which on a real line is a caller waiting in silence
<!-- /honesty:allow-context -->

- **`hx_cluster_signs`** — state `patterns_symptoms` · 7 turns
  > goal met and the simulated user stayed cooperative for 4 post-goal turn(s), but the agent never delivered its closing / reached flow_end; the agent replied EMPTY on turn(s) [7]; final state 'patterns_symptoms' with 3 outgoing edge(s) un-fired.
  _artifact:_ `hx_cluster_signs_20260807T005330Z.session.json`
- **`hx_hormonal`** — state `medications` · 7 turns
  > goal met and the simulated user stayed cooperative for 4 post-goal turn(s), but the agent never delivered its closing / reached flow_end; final state 'medications' with 1 outgoing edge(s) un-fired.
  _artifact:_ `hx_hormonal_20260807T010615Z.session.json`

### 6.2 `stuck_termination` — 2 of 10

<!-- honesty:allow-context -->
the session stalled without a classified cause
<!-- /honesty:allow-context -->

- **`hx_keep_it_quick`** — state `about_you` · 4 turns
  > session never reached an end state (cap/stuck); final state 'about_you' with 3 outgoing edge(s) un-fired.
  _artifact:_ `hx_keep_it_quick_20260807T010113Z.session.json`
- **`hx_med_overuse`** — state `about_you` · 6 turns
  > session never reached an end state (cap/stuck); final state 'about_you' with 3 outgoing edge(s) un-fired.
  _artifact:_ `hx_med_overuse_20260807T011410Z.session.json`

### 6.3 `infra_fail` — 1 of 10

- **`hx_happy_full`** — state `—`
  > infrastructure failure — the session could not be measured (attempt 1): VoiceInfraError: bot audio is flowing but no transcript events arrived (data channel dead) — after one ready-handshake retry
  _artifact:_ `hx_happy_full_20260807T044115Z.session.json`

### 6.4 Caller's goal not met — 3 of 10

<!-- honesty:allow-context -->
The grader judged the caller left without what they came for. This is the headline's complement, and the reason quoted below is the grader's own words.
<!-- /honesty:allow-context -->

- **`hx_happy_full`** — full_intake · 0 turns · final state `—`
  > not run
  _artifact:_ `hx_happy_full_20260807T044115Z.session.json`
- **`hx_keep_it_quick`** — time_pressured · 4 turns · final state `—`
  > The goal was not achieved because Ember did not keep answers short and concise as requested; instead, Ember provided lengthy, repetitive responses with multiple clarifications that violated the customer's explicit request to 'keep this quick' and contradicted the instruction to reach a clean close within the turn budge
  _artifact:_ `hx_keep_it_quick_20260807T010113Z.session.json`
- **`hx_med_overuse`** — medication · 6 turns · final state `—`
  > The customer described frequent OTC painkiller use (ibuprofen most days) and mentioned stopped prescription medications, but the agent failed to respond after the user's initial medication disclosures, preventing confirmation that Ember captured the headache_medications and stopped_medications data before the call ende
  _artifact:_ `hx_med_overuse_20260807T011410Z.session.json`

### Sample cases

Picked deterministically from this run's own artifacts — the best and worst by the benchmark's own score — so re-generating the report shows the same cases rather than reshuffling them.

<!-- honesty:allow-context -->
**✓ `hx_cluster_signs`** — goal met
  - _task:_ persona `autonomic` · 7 caller turns · final state `—`
  > AGENT: Hi, I'm Ember. I'd love to understand your headaches a little better. I'll ask you a few questions by voice — it takes about five minutes, and you can skip anything or pause and come back anytime. Ready to start? USER: Hi, yes, I'm calling because I've been having these really bad headaches on one side of my head, right around my eye, and I need to figure out what's going on. AGENT: Got it — pain on one side around your eye sounds really uncomfortable. Just so you know, I 'm an AI assista
  - _why this one:_ Ember successfully captured all key features: severe one-sided eye pain (9/10), autonomic signs (tearing, droopy lid, stuffy nose), restlessness/pacing, and confirmed these symptoms occur together during attacks, explicitly stating 'I'm saving all of
  - _artifact:_ `hx_cluster_signs_20260807T005330Z.mix.wav`

**✓ `hx_hormonal`** — goal met
  - _task:_ persona `hormonal` · 7 caller turns · final state `—`
  > AGENT: Hi, I'm Ember. I'd love to understand your headaches a little better. I'll ask you a few questions by voice — it takes about five minutes, and you can skip anything or pause and come back anytime. Ready to start? USER: Hi, I'm calling because I've been getting these really bad headaches and I'm not sure what's causing them, so I'm hoping you can help me figure it out. AGENT: I hear you — headaches like that can be really frustrating. Just so you know, I'm an AI assistant and this call is
  - _why this one:_ The user successfully raised that headaches relate to their cycle and that they take hormonal birth control (the pill for 5 years), and Ember captured this information sensitively by acknowledging it, saving it, and continuing with relevant follow-up
  - _artifact:_ `hx_hormonal_20260807T010615Z.mix.wav`

**✗ `hx_happy_full`** — goal not met
  - _task:_ persona `full_intake` · 0 caller turns · final state `—`
  - _why this one:_ not run
  - _artifact:_ `hx_happy_full_20260807T044115Z.mix.wav`

**✗ `hx_keep_it_quick`** — goal not met
  - _task:_ persona `time_pressured` · 4 caller turns · final state `—`
  > AGENT: Hi, I'm Ember. I'd love to understand your headaches a little better. I'll ask you a few questions by voice — it takes about five minutes, and you can skip anything or pause and come back anytime. Ready to start? USER: Hi, yeah, I'm Nina Park — I'm actually in a bit of a rush, so can we keep this quick? AGENT: Got it, Nina. I'll keep it brief — just the essentials. When did your headaches start, roughly? I'm here whenever you're ready — no rush at all. USER: Wait, headaches? I think you'v
  - _why this one:_ The goal was not achieved because Ember did not keep answers short and concise as requested; instead, Ember provided lengthy, repetitive responses with multiple clarifications that violated the customer's explicit request to 'keep this quick' and con
  - _artifact:_ `hx_keep_it_quick_20260807T010113Z.mix.wav`

<!-- /honesty:allow-context -->

## 7. Exclusions and what they do to the number

<!-- honesty:allow-context -->
| Attempted | Scored | Excluded | Exclusion rate |
|---:|---:|---:|---:|
| 10 | 9 | 1 | **10.0%** |
<!-- /honesty:allow-context -->

**Why each unit was excluded**

| Reason | Count | Share of attempted |
|---|---:|---:|
<!-- honesty:allow-context -->
| `infra_fail` | 1 | 10.0% |
<!-- /honesty:allow-context -->

Verbatim, from the artifacts:

> `VoiceInfraError: bot audio is flowing but no transcript events arrived (data channel dead) — after one ready-handshake retry`

**Effect on interpretation.**

An exclusion rate of 10.0% is not a rounding detail. The headline describes 9 units; it is silent about 1.

Bounding it: if every excluded unit had scored at the floor of the scale, the all-10 figure would be **70.02**; at the ceiling, **80.02**. That interval is wider than the sampling confidence interval, which means the exclusions — not the sample size — are the dominant uncertainty in this run. These are bounds, not estimates: nobody knows how the excluded units would have scored.

The excluded set is also unlikely to be random with respect to difficulty. Transport failures accumulate over turns, so longer and harder units are more exposed to them, and the scored set is plausibly the easier half of what was drawn.

## 8. Limitations and threats to validity

- **tiny n** (high) — N = 9. At this size a single scenario flipping moves the headline by ten points. Every figure here is directional and is labelled PRELIMINARY for that reason.
- **not comparable** (high) — The scenario set is ours, the flows are ours, and the grader is ours. Nothing here can be compared to any published number, and it should never be presented alongside one as if it could.
- **simulated caller** (high) — The caller is a language model speaking through text-to-speech. It has cleaner prosody, no background noise and more patience than a person on a mobile in a car — so recognition error here is a floor, not an estimate.
- **coverage** (medium) — 10 declared transitions never fired across the whole set. Those branches are untested, and a green result says nothing about them.
- **run to run** (medium) — Speech recognition, generation and turn-taking are all stochastic. Two runs of the same scenario set differ; a one-scenario change between runs is noise until it repeats.

- **sample size** (high) — N = 9 is below the 30-unit threshold this reporting layer uses to call a figure settled. The report is labelled PRELIMINARY throughout.

## 9. Reproduction

```bash
uv sync --extra dev --extra voice
./run_flow_sim.sh headache_enrollment
python -m tau2.reporting.cli build results/whissle/flow_sim/headache_enrollment
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
| `R1_headline_requires_n` | pass | headline carries N = 9 everywhere it is stated |
| `R2_judge_independence_disclosed` | pass | not applicable — judge is independent or deterministic |
| `R3_exclusion_rate_adjacent` | pass | 1/10 exclusion rate shown beside the score |
| `R4_preliminary_labelled` | pass | labelled PRELIMINARY |
| `R5_no_provider_names` | pass | no LLM vendor named outside the published-baseline table |
| `R6_comparability_stated` | pass | not applicable — no published baseline is registered |
| `R7_baseline_named` | pass | not applicable — no published baseline is registered |

**Generator warnings**

- SUMMARY.json scope (1) != scenarios on disk (10); coverage roll-up suppressed

<!-- generated by tau2.reporting from flow_sim/headache_enrollment; schema tau2.reporting.run_report/v1 -->
