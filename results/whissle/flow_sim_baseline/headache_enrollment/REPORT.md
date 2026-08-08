# Conversation-flow suite — `headache_enrollment` (real-audio voice)

> **PRELIMINARY** — N = 10 is below the 30-unit threshold for a settled number. Treat every figure below as directional.

## Abstract

Whissle was evaluated on **Whissle conversation-flow suite** in `voice` mode. The headline result is **50.0%** (N = 10 · PRELIMINARY) for task success, 95% CI [23.7%, 76.3%].

Whether a deployed voice agent actually completes its job on a phone call: does it collect what the flow says it must collect, does it handle a caller who answers out of order or refuses to engage, and does it end the call cleanly rather than trailing off. Real audio, real speech recognition, real turn-taking — not a text transcript stand-in.

## At a glance

| Field | Value |
|---|---|
| **Task success** | **50.0%** (N = 10 · PRELIMINARY) |
<!-- honesty:allow-context -->
| 95% CI | [23.7%, 76.3%] |
| Attempted / scored / excluded | 10 / 10 / 0 (0.0%) |
| Judge | rule analyzer + LLM grader |
| Mode | `voice` |
| Date | 2026-08-05 |
| Run id | `flow_sim_baseline/headache_enrollment` |
| Status | **PRELIMINARY** |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
- **Reached a clean close:** 30.0% [10.8%, 60.3%], N = 10 — taken from the authoritative `flow_end` trace event
- **Flow states visited:** 10, N = 10 — of 10 declared
- **Flow transitions fired:** 10, N = 14 — of 14 declared
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
| Agent id | `653ce25c-3c2b-49f2-948e-0b5b3f76a93c` |
| Transport endpoint | `LiveKit voice room (POST /api/bench/voice/start)` |
| Mode | `voice` |
| Dataset | scripted caller personas for `headache_enrollment` |
| Dataset size | 10 |
| Upstream | internal — no published equivalent |
| Repo commit at report time | `86b4475` |
| Captured at | 2026-08-05 |
| Run directory | `results/whissle/flow_sim_baseline/headache_enrollment` |
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
| Scored | 10 |

Every scenario in the set was run. There is no sampling error here — but there is selection: the set is what we thought to write down, and the transition-coverage table is the honest measure of what it misses.

## 4. Results

**Task success: 50.0%** (N = 10 · PRELIMINARY), 95% CI [23.7%, 76.3%].

| Metric | Value | 95% CI | N | Qualifiers |
|---|---:|---|---:|---|
| **Task success** | **50.0%** | [23.7%, 76.3%] | 10 | N = 10 · PRELIMINARY |
<!-- honesty:allow-context -->
| Reached a clean close | 30.0% | [10.8%, 60.3%] | 10 | taken from the authoritative `flow_end` trace event |
| Flow states visited | 10 | — | 10 | of 10 declared |
| Flow transitions fired | 10 | — | 14 | of 14 declared |
<!-- /honesty:allow-context -->

_The headline row is the claim and carries its qualifiers; the rest are components of it and inherit them._

<!-- honesty:allow-context -->
**Per-scenario outcomes**

| Scenario | Turns | Closed | Goal met | Final state | High-severity findings |
|---|---|---|---|---|---|
| `hx_happy_full` (full_intake) | 2 | **no** | **no** | `None` | 1 |
| `hx_red_flag_urgent` (urgent_escalation) | 2 | yes | yes | `None` | 0 |
| `hx_skips_topics` (skips) | 7 | yes | yes | `None` | 0 |
| `hx_unsure` (unsure) | 5 | **no** | **no** | `None` | 0 |
| `hx_out_of_order` (out_of_order) | 5 | **no** | yes | `None` | 0 |
| `hx_migraine_classic` (migraine) | 13 | yes | **no** | `None` | 0 |
| `hx_cluster_signs` (autonomic) | 9 | **no** | yes | `None` | 0 |
| `hx_keep_it_quick` (time_pressured) | 5 | **no** | **no** | `None` | 0 |
| `hx_hormonal` (hormonal) | 9 | **no** | yes | `None` | 0 |
| `hx_med_overuse` (medication) | 8 | **no** | **no** | `None` | 1 |

Each row is one scripted caller persona driven over real audio. 'Closed' and 'goal met' are independent: an agent can satisfy the caller and never hang up, or hang up having satisfied nobody.
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
**Flow coverage**

| Measure | Covered | Declared | Uncovered |
|---|---|---|---|
| States | 10 | 10 | none |
| Transitions | 10 | 14 | `t2`, `t4`, `t8`, `t10` |

An unfired transition is an untested branch. It is not a failure — it is the part of the flow this scenario set never reached, and therefore the part no result here speaks to.
<!-- /honesty:allow-context -->

## 5. Comparison to published baselines

There is no external comparator and there cannot be one: this suite tests our own flow definitions on our own agents. Its value is longitudinal — the same scenario set re-run after a change — and the regression view in the cross-run index is where that comparison lives, not a leaderboard.

_An empty comparison section is a result. Printing a number next to a differently-measured one would not be._

## 6. Failure analysis

| Category | Count | Rate | Severity |
|---|---:|---:|---|
<!-- honesty:allow-context -->
| `stuck_termination` | 7 | 70.0% | medium |
| `premature_termination` | 1 | 10.0% | medium |
| `coverage` | 1 | 10.0% | info |
| Caller's goal not met | 5 | 50.0% | high |
<!-- /honesty:allow-context -->

### 6.1 `stuck_termination` — 7 of 10

<!-- honesty:allow-context -->
the session stalled without a classified cause
<!-- /honesty:allow-context -->

- **`hx_cluster_signs`** — state `medications` · 9 turns
  > session never reached an end state (cap/stuck); final state 'medications' with 1 outgoing edge(s) un-fired.
  _artifact:_ `hx_cluster_signs_20260805T153944Z.session.json`
- **`hx_happy_full`** — state `—` · 2 turns
  > session failed to run: ModelError: models/chat failed after 6 attempts: HTTP 502: {"detail":"LLM call failed: all LLM providers failed; last error: [model provider] returned an empty completion"}
  _artifact:_ `hx_happy_full_20260805T150123Z.session.json`

### 6.2 `premature_termination` — 1 of 10

<!-- honesty:allow-context -->
the flow closed the call before the intake it declares was complete — the caller was served politely and the record is short
<!-- /honesty:allow-context -->

- **`hx_migraine_classic`** — state `done` · 13 turns
  > flow reached an end state but the simulated user's goal was NOT met (per the task-success judge).
  _artifact:_ `hx_migraine_classic_20260805T152556Z.session.json`

### 6.3 `coverage` — 1 of 10

<!-- honesty:allow-context -->
branches the scenario set never exercised
<!-- /honesty:allow-context -->

- **`run-level`** — state `—`
  > 4/14 transitions never fired across the session set: ['t2', 't4', 't8', 't10'].
  _artifact:_ `SUMMARY.json`

### 6.4 Caller's goal not met — 5 of 10

<!-- honesty:allow-context -->
The grader judged the caller left without what they came for. This is the headline's complement, and the reason quoted below is the grader's own words.
<!-- /honesty:allow-context -->

- **`hx_happy_full`** — full_intake · 2 turns · final state `—`
  > not run
  _artifact:_ `hx_happy_full_20260805T150123Z.session.json`
- **`hx_keep_it_quick`** — time_pressured · 5 turns · final state `—`
  > Ember acknowledged the customer's request to keep it quick but failed to deliver concise answers, instead asking repetitive and rambling questions that frustrated the customer, and did not reach a clean close within the turn budget.
  _artifact:_ `hx_keep_it_quick_20260805T155105Z.session.json`
- **`hx_med_overuse`** — medication · 8 turns · final state `—`
  > not run
  _artifact:_ `hx_med_overuse_20260805T160803Z.session.json`

## 7. Exclusions and what they do to the number

Nothing was excluded: all 10 attempted units produced a gradable result. The headline denominator is the full attempted set.

## 8. Limitations and threats to validity

- **tiny n** (high) — N = 10. At this size a single scenario flipping moves the headline by ten points. Every figure here is directional and is labelled PRELIMINARY for that reason.
- **not comparable** (high) — The scenario set is ours, the flows are ours, and the grader is ours. Nothing here can be compared to any published number, and it should never be presented alongside one as if it could.
- **simulated caller** (high) — The caller is a language model speaking through text-to-speech. It has cleaner prosody, no background noise and more patience than a person on a mobile in a car — so recognition error here is a floor, not an estimate.
- **coverage** (medium) — 4 declared transitions never fired across the whole set. Those branches are untested, and a green result says nothing about them.
- **run to run** (medium) — Speech recognition, generation and turn-taking are all stochastic. Two runs of the same scenario set differ; a one-scenario change between runs is noise until it repeats.

- **sample size** (high) — N = 10 is below the 30-unit threshold this reporting layer uses to call a figure settled. The report is labelled PRELIMINARY throughout.

## 9. Reproduction

```bash
uv sync --extra dev --extra voice
./run_flow_sim.sh headache_enrollment
python -m tau2.reporting.cli build results/whissle/flow_sim/headache_enrollment
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
| `R1_headline_requires_n` | pass | headline carries N = 10 everywhere it is stated |
| `R2_judge_independence_disclosed` | pass | not applicable — judge is independent or deterministic |
| `R3_exclusion_rate_adjacent` | pass | not applicable — nothing was excluded |
| `R4_preliminary_labelled` | pass | labelled PRELIMINARY |
| `R5_no_provider_names` | pass | no LLM vendor named outside the published-baseline table |
| `R6_comparability_stated` | pass | not applicable — no published baseline is registered |

<!-- generated by tau2.reporting from flow_sim_baseline/headache_enrollment; schema tau2.reporting.run_report/v1 -->
