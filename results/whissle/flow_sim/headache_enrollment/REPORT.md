# Conversation-flow suite — `headache_enrollment` (real-audio voice)

> **PRELIMINARY** — N = 1 is below the 30-unit threshold for a settled number. Treat every figure below as directional.

## Abstract

Whissle was evaluated on **Whissle conversation-flow suite** in `voice` mode. The headline result is **100.0%** (N = 1 · PRELIMINARY) for task success, 95% CI [20.7%, 100.0%].

Whether a deployed voice agent actually completes its job on a phone call: does it collect what the flow says it must collect, does it handle a caller who answers out of order or refuses to engage, and does it end the call cleanly rather than trailing off. Real audio, real speech recognition, real turn-taking — not a text transcript stand-in.

## At a glance

| Field | Value |
|---|---|
| **Task success** | **100.0%** (N = 1 · PRELIMINARY) |
<!-- honesty:allow-context -->
| 95% CI | [20.7%, 100.0%] |
| Attempted / scored / excluded | 1 / 1 / 0 (0.0%) |
| Judge | rule analyzer + LLM grader |
| Mode | `voice` |
| Date | 2026-08-07 |
| Run id | `flow_sim/headache_enrollment` |
| Status | **PRELIMINARY** |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
- **Reached a clean close:** 100.0% [20.7%, 100.0%], N = 1 — taken from the authoritative `flow_end` trace event
- **Flow states visited:** 9, N = 10 — of 10 declared
- **Flow transitions fired:** 8, N = 18 — of 18 declared
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
| Dataset size | 1 |
| Upstream | internal — no published equivalent |
| Repo commit at report time | `86b4475` |
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
| Population | 1 |
| Requested | 1 |
| Selected | 1 |
| Scored | 1 |

Every scenario in the set was run. There is no sampling error here — but there is selection: the set is what we thought to write down, and the transition-coverage table is the honest measure of what it misses.

## 4. Results

**Task success: 100.0%** (N = 1 · PRELIMINARY), 95% CI [20.7%, 100.0%].

| Metric | Value | 95% CI | N | Qualifiers |
|---|---:|---|---:|---|
| **Task success** | **100.0%** | [20.7%, 100.0%] | 1 | N = 1 · PRELIMINARY |
<!-- honesty:allow-context -->
| Reached a clean close | 100.0% | [20.7%, 100.0%] | 1 | taken from the authoritative `flow_end` trace event |
| Flow states visited | 9 | — | 10 | of 10 declared |
| Flow transitions fired | 8 | — | 18 | of 18 declared |
<!-- /honesty:allow-context -->

_The headline row is the claim and carries its qualifiers; the rest are components of it and inherit them._

<!-- honesty:allow-context -->
**Per-scenario outcomes**

| Scenario | Turns | Closed | Goal met | Final state | High-severity findings |
|---|---|---|---|---|---|
| `hx_migraine_classic` (migraine) | 18 | yes | yes | `None` | 0 |

Each row is one scripted caller persona driven over real audio. 'Closed' and 'goal met' are independent: an agent can satisfy the caller and never hang up, or hang up having satisfied nobody.
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
**Flow coverage**

| Measure | Covered | Declared | Uncovered |
|---|---|---|---|
| States | 9 | 10 | `urgent` |
| Transitions | 8 | 18 | `t2`, `t4`, `t6`, `t7_done`, `t8`, `t9_done`, `t10`, `t11_done`, `t12_done`, `t14` |

An unfired transition is an untested branch. It is not a failure — it is the part of the flow this scenario set never reached, and therefore the part no result here speaks to.
<!-- /honesty:allow-context -->

## 5. Comparison to published baselines

There is no external comparator and there cannot be one: this suite tests our own flow definitions on our own agents. Its value is longitudinal — the same scenario set re-run after a change — and the regression view in the cross-run index is where that comparison lives, not a leaderboard.

_An empty comparison section is a result. Printing a number next to a differently-measured one would not be._

## 6. Failure analysis

| Category | Count | Rate | Severity |
|---|---:|---:|---|
<!-- honesty:allow-context -->
| `coverage` | 2 | 20.0% | info |
| Caller's goal not met | 3 | 30.0% | high |
<!-- /honesty:allow-context -->

### 6.1 `coverage` — 2 of 10

<!-- honesty:allow-context -->
branches the scenario set never exercised
<!-- /honesty:allow-context -->

- **`run-level`** — state `—`
  > 1/10 states never entered across the session set: ['urgent'].
  _artifact:_ `SUMMARY.json`
- **`run-level`** — state `—`
  > 10/18 transitions never fired across the session set: ['t2', 't4', 't6', 't7_done', 't8', 't9_done', 't10', 't11_done', 't12_done', 't14'].
  _artifact:_ `SUMMARY.json`

### 6.2 Caller's goal not met — 3 of 10

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

## 7. Exclusions and what they do to the number

Nothing was excluded: all 1 attempted units produced a gradable result. The headline denominator is the full attempted set.

## 8. Limitations and threats to validity

- **tiny n** (high) — N = 1. At this size a single scenario flipping moves the headline by ten points. Every figure here is directional and is labelled PRELIMINARY for that reason.
- **not comparable** (high) — The scenario set is ours, the flows are ours, and the grader is ours. Nothing here can be compared to any published number, and it should never be presented alongside one as if it could.
- **simulated caller** (high) — The caller is a language model speaking through text-to-speech. It has cleaner prosody, no background noise and more patience than a person on a mobile in a car — so recognition error here is a floor, not an estimate.
- **coverage** (medium) — 10 declared transitions never fired across the whole set. Those branches are untested, and a green result says nothing about them.
- **run to run** (medium) — Speech recognition, generation and turn-taking are all stochastic. Two runs of the same scenario set differ; a one-scenario change between runs is noise until it repeats.

- **sample size** (high) — N = 1 is below the 30-unit threshold this reporting layer uses to call a figure settled. The report is labelled PRELIMINARY throughout.

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
| `R1_headline_requires_n` | pass | headline carries N = 1 everywhere it is stated |
| `R2_judge_independence_disclosed` | pass | not applicable — judge is independent or deterministic |
| `R3_exclusion_rate_adjacent` | pass | not applicable — nothing was excluded |
| `R4_preliminary_labelled` | pass | labelled PRELIMINARY |
| `R5_no_provider_names` | pass | no LLM vendor named outside the published-baseline table |
| `R6_comparability_stated` | pass | not applicable — no published baseline is registered |

<!-- generated by tau2.reporting from flow_sim/headache_enrollment; schema tau2.reporting.run_report/v1 -->
