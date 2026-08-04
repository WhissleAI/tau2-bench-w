# Whissle flow-harness — overview

> **Private fork.** This repo and its `results/whissle/**` outputs may contain
> internal findings about Whissle's in-call conversation flow engine. Keep them
> in the private fork.

Whissle agents can run an optional **in-call conversation flow** — a per-agent
state machine that steers a live call turn-by-turn (which prompt goal is in force,
which tools are offered, what is said, per-state voice + turn-taking). This fork
ships a family of harnesses that exercise that engine end-to-end against the real
product surface. This doc is the index; each suite has its own detailed doc.

Every suite drives the SAME surface a customer hits —
`POST /api/agents/{id}/chat/turn` over the **deterministic text channel** (no audio
nondeterminism) — and reads `WHISSLE_API_KEY` (a `wsk_` key, `agents:write` scope)
+ optional `WHISSLE_BASE` (default `https://aws-gateway-backend.whissle.ai/bot`)
from a git-ignored `.env`. The text channel runs the exact same pure `FlowRuntime`
state machine the voice pipeline does (`services/flow/text_runner.py` vs. the
pipecat `FrameProcessor`), so a text-channel assertion is a faithful — and
repeatable — reading of what the voice call does, minus STT/TTS.

## Which suite do I want

| Suite | Flow comes from | User turns | Proves | Doc / runner |
|---|---|---|---|---|
| **Authored flow** | authored onto a throwaway agent | scripted | a hand-built state machine executes correctly over long multi-tool, multi-state sessions (say-markers, per-state tool-gating, expression + `llm_condition` branches, loop guards) | [WHISSLE_FLOW.md](WHISSLE_FLOW.md) — `./run_flow.sh` |
| **Default-flow coverage** | the type's **auto-attached** default (`flow.json`) | scripted | every seeded agent **type** ships a correct default flow that auto-attaches on create and actually drives the call | [WHISSLE_FLOW_DEFAULTS.md](WHISSLE_FLOW_DEFAULTS.md) — `./run_flow_defaults.sh` |
| **Flow-sim (bug finder)** | the type's auto-attached default | **LLM simulated user** (unscripted) | off-happy-path bugs: illegal jumps, gates opening without their variable, tool leakage, endless loops, compliance breaches | [WHISSLE_FLOW_SIM.md](WHISSLE_FLOW_SIM.md) — `./run_flow_sim.sh` |
| **Seeded analyzer tests** | hand-seeded fixtures (offline) | — | the deterministic analyzer itself is correct (regression tests) | `tests/test_flow_analyze.py` — `uv run pytest` |

All three live suites read the engine's **declared flow spec** (`GET
/api/agents/{id}`) and its **accumulated step trace** (`GET
/api/agents/{id}/flow/trace`, plus the per-turn `flow.steps` on each `chat/turn`
response). Step event kinds: `state_enter`, `say_emitted`,
`transition_check{result: fired|not_satisfied|error}`, `tools_gated`, `var_set`,
`guard_trip`, `flow_end`.

Code lives in `src/tau2/flow/`: `usersim.py` (the LLM user + judges + Whissle model
wrapper), `analyze.py` (the rule-analyzer), `simulate.py` / `defaults.py` /
`benchmark.py` (the three CLIs), `client.py` (`FlowClient`, the agent HTTP client).
Fixtures in `data/flow/`. Only base deps (`requests` / `typer` / `rich` / `dotenv`)
— no `--extra`.

## The flow-sim engine

The bug finder (`WHISSLE_FLOW_SIM.md`) is the richest of the three. It runs
entirely on **Whissle's own model API** — no external ANTHROPIC/OPENAI key:

- **LLM simulated user** (`usersim.py`) — a persona-with-a-goal caller. It
  improvises (disputes, gives wrong info first, changes its mind, refuses to
  verify), so the flow takes branches a scripted test never would. It talks to
  `POST /api/models/chat` (Whissle's à-la-carte chat model), mapping the *agent's*
  replies in as `user` messages, and appends an `[[END]]` sentinel when the goal is
  met / refused / it would hang up. Two LLM judges share that endpoint:
  `judge_task_success` (1 call/session) and `judge_goal_drift` (per turn,
  `--no-semantic` disables it).
- **Deterministic rule-analyzer** (`analyze.py`) — pure, I/O-free, the flow analogue
  of a WER/DER scorer. It audits the running machine (declared spec + step trace)
  against its own contract and emits **typed findings**. Every check degrades
  safely — an expression it cannot parse is skipped, never a false positive.

**Finding types** (severity): `illegal_transition`, `expression_integrity`,
`tool_leakage`, `variable_desync`, `guard_violation`, `dead_end`, `compliance`
(all **high**); `missed_transition`, `say_fidelity`, `stuck_loop`,
`stuck_termination`, `premature_termination` (**medium**); `coverage` (**info**).
See [WHISSLE_FLOW_SIM.md](WHISSLE_FLOW_SIM.md) for what each catches.

Findings are the **deliverable**, not a harness failure — a run exits `0` whether
or not it found bugs (only a setup/creds crash is non-zero). Read the SUMMARY.

## The default-flow coverage suite

`WHISSLE_FLOW_DEFAULTS.md` supplies **no flow** and proves every seeded **type**
auto-attaches a correct, driving default. It grades three tiers per type — **attach**
(`GET` shows an enabled flow with a real `start_state`), **drive** (`chat/turn` ×3–4:
turn-1 flow active, enters `start_state` first, ≥1 transition check, say-start text
verbatim), and **gate** (debt_collection only: unverified turns never disclose a
balance / enter a disclosure state — the identity-verification compliance gate
holds). It covers **15 seeded types** (dental_receptionist, patient_checkin,
medication_checkin, medical_followup, headache_enrollment, appointment_reminder,
appointment_scheduling, renewal_reminder, survey_feedback, lead_qualification,
customer_support, sales_handoff, car_rental, debt_collection, ai_tutor); the
flow-sim suite drives **5** of them (dental_receptionist, car_rental,
debt_collection, appointment_scheduling, customer_support), 10 sessions each. There
is no `skipped-pending-trace` degradation here — an absent flow is a real failure.

## Seeded validation

`tests/test_flow_analyze.py` is the analyzer's own regression suite — **offline, no
backend**. It validates `analyze_session` / `eval_expr` / `_referenced_names`
against hand-seeded flow + step-trace fixtures, and pins three fixed analyzer bugs:
lowercase `true`/`false` boolean literals wrongly flagged as `variable_desync`; the
debt-collection compliance gate spuriously opening at turn-0 when the start state is
itself the verify state; and a tool-leak judged against the wrong (adjacent) state's
gate instead of the live call-time gate. These are the deterministic seeded checks
that keep the analyzer honest before it ever runs against a live agent.

## How to run each

```bash
cp .env.example .env          # set WHISSLE_API_KEY=wsk_...   (agents:write scope)

# Authored-flow suite
./run_flow.sh                                   # all scenarios (marker / appointment / guarded_loop)
./run_flow.sh marker                            # one scenario
uv run python -m tau2.flow.benchmark list       # raw CLI

# Default-flow coverage (15 seeded types)
./run_flow_defaults.sh                          # all types
./run_flow_defaults.sh debt_collection          # one type
uv run python -m tau2.flow.defaults list        # raw CLI

# Flow-sim bug finder (5 types, 10 sessions each)
./run_flow_sim.sh list
./run_flow_sim.sh --agent-type dental_receptionist --sessions 10
./run_flow_sim.sh --agent-type debt_collection --task-id debt_dispute --no-semantic
uv run python -m tau2.flow.simulate run --agent-type <type> [--sessions N] [--task-id id,id] [--no-semantic] [--keep-agent] [--max-turns N]

# Seeded analyzer tests (offline — no backend / key needed)
uv run pytest tests/test_flow_analyze.py
```

All three live runners accept `KEEP_AGENT=1` to leave the throwaway agent
(`flowbench-*` / `flowcov-*` / `flowsim-*`) for debugging; otherwise each deletes
its agent in every exit path plus an end-of-run sweep, so a crashed run never
leaves agents behind.

## Results

Committed per-suite outputs live under `results/whissle/flow/`,
`results/whissle/flow_defaults/`, and `results/whissle/flow_sim/` — per-item
`.json` + human-readable `.md`, plus a `SUMMARY.{md,json}` rollup (for the sim,
findings by type & severity + a state/transition coverage table). Timestamped
`*.jsonl` per-run step logs are git-ignored and regenerated each run. As noted at
the top, these outputs are internal — keep them in the private fork.
