# Whissle simulated-user FLOW-testing engine

A **state-tracking / state-rule bug finder** for Whissle's in-call conversation flow
engine. Where `run_flow.sh` drives *scripted* turns and `run_flow_defaults.sh` proves
every seeded type *auto-attaches* a driving flow, this suite drives an **LLM
simulated user** — a persona with a goal — through a full, unscripted conversation
against a flow-enabled agent, captures the engine's declared flow spec + accumulated
step trace, and runs a **deterministic rule-analyzer** that audits the running
machine against its own declared contract.

It is the shared engine the 5 parallel runners use to run 10 sessions each against 5
flow-enabled agent types.

## Why a simulated user

Scripted turns only ever walk the paths the author imagined. A persona+goal LLM user
improvises — it disputes, gives wrong info first, changes its mind, refuses to verify
— so the flow takes branches a fixed script never would. That surfaces the bugs that
only appear off the happy path: gates that open without their variable, illegal state
jumps, tools leaking into states that should not offer them, loops that never end.

Everything runs on **Whissle's own product surface** and Whissle's **own model API** —
no external ANTHROPIC/OPENAI key:

- the agent under test:   `POST /api/agents/{id}/chat/turn` (deterministic text channel)
- the simulated user + judges:   `POST /api/models/chat` (à-la-carte chat model)

## Quick start

```bash
cp .env.example .env          # set WHISSLE_API_KEY=wsk_...   (agents:write scope)
./run_flow_sim.sh list        # list the 5 types + their tasks

# what each of the 5 parallel runners calls for its assigned type:
./run_flow_sim.sh --agent-type dental_receptionist   --sessions 10
./run_flow_sim.sh --agent-type car_rental            --sessions 10
./run_flow_sim.sh --agent-type debt_collection       --sessions 10
./run_flow_sim.sh --agent-type appointment_scheduling --sessions 10
./run_flow_sim.sh --agent-type customer_support      --sessions 10

# one specific task (comma-separate several); skip the per-turn drift judge for speed:
./run_flow_sim.sh --agent-type dental_receptionist --task-id dental_reschedule
./run_flow_sim.sh --agent-type debt_collection --sessions 10 --no-semantic
```

## Pipeline (per session)

1. Create a flow-enabled agent of `--agent-type` supplying **no flow** → the backend
   auto-attaches that type's default state machine (enabled).
2. `GET /api/agents/{id}` → read the **declared flow** (states, transitions,
   variables, settings, start_state) — the contract to audit against.
3. Drive a simulated conversation: the user-sim opens, the agent replies, the
   user-sim reacts, … until the agent flow `ended`, the user is done (the **agent**
   closed / refused — goal-met alone does **not** end the call, see below), the
   post-goal allowance is exhausted, or the per-task **turn budget** is hit
   (fixture-declared per type/task; global fallback 24).
4. `GET /api/agents/{id}/flow/trace` → the full accumulated step trace.
5. Judge **task success** (LLM) and, optionally, per-turn **goal-drift** (LLM).
6. Run the deterministic analyzer → typed findings.
7. **Always** delete the agent; write the per-session JSON; aggregate a SUMMARY.

## The simulated user (`usersim.py`)

An LLM given a strict system prompt: *play only the customer, one natural utterance,
pursue the goal, invent consistent details, behave like the persona.* The dialogue
is mapped so the **agent's** replies arrive as `user` messages and the sim's own
lines are `assistant` messages — the model then generates the next user utterance.
Two LLM judges share the same endpoint: `judge_task_success` (1 call/session) and
`judge_goal_drift` (per turn, `--no-semantic` to disable).

**Call-ending protocol (two sentinels).** Goal-met and hang-up are separate signals,
so the sim never deflates the "ended cleanly" metric by going silent the moment its
goal is satisfied:

- `[[GOAL_MET]]` — appended once when the sim's goal is achieved. The call stays
  open: the sim keeps responding naturally (brief acknowledgements, answering
  "anything else?" with a no-thanks, returning the goodbye) so the **agent** gets to
  deliver its closing and reach `flow_end`.
- `[[END]]` — appended only when the call is actually over: the agent delivered its
  goodbye/closing, clearly refused / cannot help, or the persona would genuinely
  abandon the call.

The runner grants the agent a **post-goal allowance** (`post_goal_turns`, default 4
cooperative turns after `[[GOAL_MET]]`). If the agent still hasn't closed when the
allowance runs out, that is the agent's failure and is reported as `agent_no_close`
— never blamed on the sim. An EMPTY agent reply is surfaced to the sim as silence
(it prompts once, "are you still there?", instead of hanging up).

## Finding types (`analyze.py`)

Pure, I/O-free, deterministic — the flow analogue of a WER/DER scorer. Every check
degrades safely (an expression it cannot parse is skipped, never a false positive).

| type | severity | what it catches |
|------|----------|-----------------|
| `illegal_transition` | high | current_state advanced to a state with **no declared transition** from the prior state (and not a legal guard-fallback jump) |
| `expression_integrity` | high | an `expression` transition **fired** but its expression is false / references an unset variable given var_set history — the gate opened without its variable |
| `missed_transition` | medium | an `expression` edge whose vars were satisfied was recorded `not_satisfied`; or a higher-priority satisfiable edge lost to a lower one |
| `tool_leakage` | high | a tool was invoked in a turn whose active state's gate (the engine's own `tools_gated.allowed`) did not admit it |
| `variable_desync` | high | a transition **gates on a phantom variable** — neither declared nor ever set at runtime (tool_result / llm slot-fills of undeclared vars are NORMAL and not flagged) |
| `guard_violation` | high | `max_visits_per_state` / `max_transitions_per_call` exceeded with no `guard_trip`; or a trip not handled per `on_guard_trip` |
| `say_fidelity` | medium | a `say` state was entered but its exact text was not emitted |
| `stuck_loop` | medium | the same state was re-entered ≥ N times |
| `dead_end` | high | the session ended in a non-end state with **no outgoing transitions** |
| `agent_no_close` | high | the goal **was** met and the sim stayed cooperative through its post-goal allowance (or the agent replied EMPTY), yet the agent never delivered its closing / reached `flow_end` — the agent's own failure to close |
| `turn_cap_exceeded` | medium | the turn budget ran out **before** the goal was met — the flow is genuinely too long for its declared budget |
| `stuck_termination` | medium | never reached an end, for reasons the above two don't explain (no trace, judge unavailable, goal met on the very last turn, …) |
| `premature_termination` | medium | reached an end state but the goal was **not** met (per the task-success judge) |
| `compliance` | high | (parameterized per type) a forbidden disclosure happened **before** a required gate variable / verify-state became true — e.g. `debt_collection` must verify identity before disclosing any balance |
| `coverage` | info | (aggregate) states / transitions never exercised across the session set |

## Task sets (`data/flow/sim_tasks.json`)

≥ 10 persona+goal tasks **each** for 5 types, spanning happy-path **and** edges so the
sessions drive branch coverage:

- **dental_receptionist** — book / reschedule / cancel / no-slot / wrong-info / hours-only
- **car_rental** — book / no-availability / change-vehicle / just-asking / wrong-dates
- **debt_collection** — right-party-pays / promise-to-pay / **dispute** / **wrong-party** / **refuse** (probes the verify-before-disclose gate)
- **appointment_scheduling** — new / reschedule / cancel / out-of-hours / wrong-details
- **customer_support** — resolvable / needs-escalation / angry / account-lookup
- **headache_enrollment** — full spoken intake / red-flag escalation / skips / unsure / out-of-order / migraine / cluster / hormonal / medication-overuse

### Fixture fields (budgets)

Turn budgets are declared **where the flow length is known** instead of one global
cap — resolution is most-specific-wins:

| field | levels | default | meaning |
|-------|--------|---------|---------|
| `max_turns` | top-level → type block → task | 24 | per-session caller-turn budget. Sized to the flow: e.g. `headache_enrollment` (10-state spoken intake) declares 28; `dental_receptionist` declares 18; short scenarios override lower (`dental_hours_only`: 10, `hx_red_flag_urgent`: 14, `hx_keep_it_quick`: 18) |
| `post_goal_turns` | top-level → type block → task | 4 | cooperative turns the sim grants **after** its goal is met for the agent to deliver its closing; exhausting it without a close ⇒ `agent_no_close` |

`--max-turns` on the CLI still overrides everything for a run.

## Reporting

- `results/whissle/flow_sim/<agent_type>/<task_id>.json` — per-session transcript +
  trace-derived detail + findings + task success.
- `results/whissle/flow_sim/<agent_type>/SUMMARY.{md,json}` — per-agent rollup:
  findings by type & severity + a **state/transition coverage** table.
- `results/whissle/flow_sim/SUMMARY.{md,json}` — overall, when a run spans types.
- Timestamped `*.jsonl` per-run step logs (gitignored, regenerated each run).

## Cleanup

Every throwaway agent (`flowsim-*`) is deleted in every exit path — per-session, plus
an end-of-run sweep that flags and deletes any straggler. A run prints
`cleanup verified: no flowsim-* agents linger`.

## Exit code

A findings-oriented harness: findings are the **deliverable**, not a harness failure,
so a run exits `0` whether or not it found bugs. Only a genuine setup/creds crash is
non-zero. Read the SUMMARY for the findings.
