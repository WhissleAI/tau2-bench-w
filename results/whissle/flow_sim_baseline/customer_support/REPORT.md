# Conversation-flow suite — `customer_support` (real-audio voice)

> **PRELIMINARY** — N = 2 is below the 30-unit threshold for a settled number; SUMMARY.json is missing; figures were recomputed from the session sidecars, and the run boundary had to be inferred from timestamps. Treat every figure below as directional.

> **Partial run.** SUMMARY.json is missing; figures were recomputed from the session sidecars, and the run boundary had to be inferred from timestamps

## Abstract

Whissle was evaluated on **Whissle conversation-flow suite** in `voice` mode. The headline result is **50.0%** (N = 2 · PRELIMINARY) for task success, 95% CI [9.5%, 90.5%].

Whether a deployed voice agent actually completes its job on a phone call: does it collect what the flow says it must collect, does it handle a caller who answers out of order or refuses to engage, and does it end the call cleanly rather than trailing off. Real audio, real speech recognition, real turn-taking — not a text transcript stand-in.

## At a glance

| Field | Value |
|---|---|
| **Task success** | **50.0%** (N = 2 · PRELIMINARY) |
<!-- honesty:allow-context -->
| 95% CI | [9.5%, 90.5%] |
| Attempted / scored / excluded | 2 / 2 / 0 (0.0%) |
| Judge | rule analyzer + LLM grader |
| Mode | `voice` |
| Date | 2026-08-05 |
| Run id | `flow_sim_baseline/customer_support` |
| Status | **PRELIMINARY** |
<!-- /honesty:allow-context -->

<!-- honesty:allow-context -->
- **Reached a clean close:** 100.0% [34.2%, 100.0%], N = 2 — taken from the authoritative `flow_end` trace event
<!-- /honesty:allow-context -->

## 1. What was measured, and why

Whether a deployed voice agent actually completes its job on a phone call: does it collect what the flow says it must collect, does it handle a caller who answers out of order or refuses to engage, and does it end the call cleanly rather than trailing off. Real audio, real speech recognition, real turn-taking — not a text transcript stand-in.

**Why this benchmark.** Every text benchmark in this repository removes the two things that break voice products: recognition error and turn-taking. This suite exists to measure what those two things cost, on the flows we actually ship.

## 2. Methodology

| Field | Value |
|---|---|
| Agent under test | the deployed `customer_support` agent, with its real flow definition, prompts and tools |
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
| Agent id | `9aa977a8-ea62-4773-bc49-b2686f235eae` |
| Transport endpoint | `LiveKit voice room (POST /api/bench/voice/start)` |
| Mode | `voice` |
| Dataset | scripted caller personas for `customer_support` |
| Dataset size | 2 |
| Upstream | internal — no published equivalent |
| Repo commit at report time | `86b4475` |
| Captured at | 2026-08-05 |
| Run directory | `results/whissle/flow_sim_baseline/customer_support` |
| Agent type | customer_support |

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
| Population | 2 |
| Requested | 2 |
| Selected | 2 |
| Scored | 2 |

Every scenario in the set was run. There is no sampling error here — but there is selection: the set is what we thought to write down, and the transition-coverage table is the honest measure of what it misses.

## 4. Results

**Task success: 50.0%** (N = 2 · PRELIMINARY), 95% CI [9.5%, 90.5%].

| Metric | Value | 95% CI | N | Qualifiers |
|---|---:|---|---:|---|
| **Task success** | **50.0%** | [9.5%, 90.5%] | 2 | N = 2 · PRELIMINARY |
<!-- honesty:allow-context -->
| Reached a clean close | 100.0% | [34.2%, 100.0%] | 2 | taken from the authoritative `flow_end` trace event |
<!-- /honesty:allow-context -->

_The headline row is the claim and carries its qualifiers; the rest are components of it and inherit them._

## 5. Comparison to published baselines

There is no external comparator and there cannot be one: this suite tests our own flow definitions on our own agents. Its value is longitudinal — the same scenario set re-run after a change — and the regression view in the cross-run index is where that comparison lives, not a leaderboard.

_An empty comparison section is a result. Printing a number next to a differently-measured one would not be._

## 6. Failure analysis

| Category | Count | Rate | Severity |
|---|---:|---:|---|
<!-- honesty:allow-context -->
| `premature_termination` | 1 | 50.0% | medium |
| Caller's goal not met | 1 | 50.0% | high |
<!-- /honesty:allow-context -->

### 6.1 `premature_termination` — 1 of 2

<!-- honesty:allow-context -->
the flow closed the call before the intake it declares was complete — the caller was served politely and the record is short
<!-- /honesty:allow-context -->

- **`cs_resolvable_login`** — state `transfer_human` · 6 turns
  > flow reached an end state but the simulated user's goal was NOT met (per the task-success judge).
  _artifact:_ `cs_resolvable_login_20260805T181604Z.session.json`

### 6.2 Caller's goal not met — 1 of 2

<!-- honesty:allow-context -->
The grader judged the caller left without what they came for. This is the headline's complement, and the reason quoted below is the grader's own words.
<!-- /honesty:allow-context -->

- **`cs_resolvable_login`** — resolvable · 6 turns · final state `—`
  > The customer's login issue was not resolved during the call; instead, the agent scheduled a callback for technical support to handle the password reset in the future.
  _artifact:_ `cs_resolvable_login_20260805T181604Z.session.json`

## 7. Exclusions and what they do to the number

Nothing was excluded: all 2 attempted units produced a gradable result. The headline denominator is the full attempted set.

## 8. Limitations and threats to validity

- **tiny n** (high) — N = 2. At this size a single scenario flipping moves the headline by ten points. Every figure here is directional and is labelled PRELIMINARY for that reason.
- **not comparable** (high) — The scenario set is ours, the flows are ours, and the grader is ours. Nothing here can be compared to any published number, and it should never be presented alongside one as if it could.
- **simulated caller** (high) — The caller is a language model speaking through text-to-speech. It has cleaner prosody, no background noise and more patience than a person on a mobile in a car — so recognition error here is a floor, not an estimate.
- **coverage** (medium) — 0 declared transitions never fired across the whole set. Those branches are untested, and a green result says nothing about them.
- **run to run** (medium) — Speech recognition, generation and turn-taking are all stochastic. Two runs of the same scenario set differ; a one-scenario change between runs is noise until it repeats.

- **sample size** (high) — N = 2 is below the 30-unit threshold this reporting layer uses to call a figure settled. The report is labelled PRELIMINARY throughout.

## 9. Reproduction

```bash
uv sync --extra dev --extra voice
./run_flow_sim.sh customer_support
python -m tau2.reporting.cli build results/whissle/flow_sim/customer_support
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
| `SUMMARY.json` | **missing** | run-level aggregation and coverage |
| `SUMMARY.md` | **missing** | the harness's own short summary |
| `*.session.json` | **missing** | per-session sidecar: turns, flow trace, findings |
| `*.mix.wav` | **missing** | the recorded call |
| `REPORT.md` | **missing** | this report |
| `report.json` | **missing** | machine-readable form of this report |

Every per-case record carries a `diagnostics` block (`tau2.health.diagnostics/v1`) with flow trace, signals, metadata sidecar, tool forensics, provenance and cost — and explicit availability flags, so an absent measurement reads as absent rather than as zero. See `HEALTH_DIAGNOSTICS.md`.

## Appendix B — honesty-rule compliance

These rules are executed against this document, not asserted about it. A failing rule blocks generation.

| Rule | Verdict | Checked |
|---|:---:|---|
| `R1_headline_requires_n` | pass | headline carries N = 2 everywhere it is stated |
| `R2_judge_independence_disclosed` | pass | not applicable — judge is independent or deterministic |
| `R3_exclusion_rate_adjacent` | pass | not applicable — nothing was excluded |
| `R4_preliminary_labelled` | pass | labelled PRELIMINARY |
| `R5_no_provider_names` | pass | no LLM vendor named outside the published-baseline table |
| `R6_comparability_stated` | pass | not applicable — no published baseline is registered |

<!-- generated by tau2.reporting from flow_sim_baseline/customer_support; schema tau2.reporting.run_report/v1 -->
