# Whissle conversation-FLOW bench suite

A state-machine test suite that verifies Whissle's **in-call conversation flow
engine** drives correctly over long, multi-tool, multi-state sessions. It authors a
flow onto a throwaway agent, drives scripted user turns over the **deterministic
text channel** (no audio nondeterminism), and asserts the machine executed.

This is the flow analogue of the transcription (WER) and diarization (DER) suites:
a standalone harness that drives the SAME product surface a customer hits
(`POST /api/agents/{id}/chat/turn`), reading `WHISSLE_BASE` + `WHISSLE_API_KEY`
from `.env`. It lives in `src/tau2/flow/` and is invoked with `./run_flow.sh`.

## Why the text channel

Whissle's flow engine runs the exact same pure `FlowRuntime` state machine on both
the voice pipeline (a pipecat `FrameProcessor`) and the stateless text channel
(`services/flow/text_runner.py`). Transition evaluation, the expression grammar,
the batched `llm_condition` judge, variable mirroring and prompt composition are
shared code — so what the text channel does is what the voice call does, minus the
STT/TTS. Driving over text makes a state-sequence assertion **deterministic and
repeatable**, which audio cannot be.

## Quick start

```bash
cp .env.example .env          # set WHISSLE_API_KEY=wsk_...   (agents:write scope)
./run_flow.sh                 # run all scenarios
./run_flow.sh marker          # just the canary
./run_flow.sh appointment     # the multi-tool scenario
./run_flow.sh guarded_loop    # the loop-guard scenario
KEEP_AGENT=1 ./run_flow.sh marker   # leave the throwaway agent for debugging

# or directly:
uv run python -m tau2.flow.benchmark list
uv run python -m tau2.flow.benchmark run --scenario marker
```

Only base deps are used (`requests` / `typer` / `rich` / `dotenv`) — no `--extra`.

## The scenarios (`data/flow/*.json`)

Each scenario fixture is self-describing: the agent spec, the flow state-machine
definition, the scripted user turns, and the expected outcome (verbatim
say-markers, tool calls, state sequence, fired transitions).

| id | what it proves | turns |
|----|----------------|-------|
| **marker** | smoke canary — `mark(say)→ask(conversation)→wrap(say)→end`; verbatim say-markers prove each say-state ran and the `llm_condition` transition fired | 2 |
| **appointment** | realistic multi-tool run — `greet→verify(conversation)→lookup(tool)→offer(conversation)→book(tool)→confirm(say)→goodbye(say)→end`; exercises **per-state tool-gating** (`book_appointment` is never offered in the verify/offer states), an **expression branch** (`verify→lookup` on the `caller_name` variable set by `save_contact_field`) with an **llm_condition fallback** on the same edge, and a second `llm_condition` branch on slot choice | 5 |
| **guarded_loop** | loop guards — a `spin_a ⇄ spin_b` loop (sustained by a `to_override` redirect) that the `max_visits_per_state` guard trips within N re-entries; `on_guard_trip:fallback` then escapes to the landing state. Fully deterministic (say/set_variable/always only — no LLM judge), so it runs in one turn. The verbatim `LOOP-ESCAPED` marker appears **only if** the guard tripped and the fallback fired. | 1 |

## What it asserts, and graceful degradation

Assertions are graded in two tiers so the suite is meaningful **today** and becomes
strict automatically once the trace ships:

- **observable** (runs every time) — asserted on the agent's real replies and
  `tools_used`. A verbatim say-marker in the reply proves the say-state executed; a
  tool name in `tools_used` proves that tool state fired and gating admitted it; a
  gated-out tool's **absence** proves per-state gating.
- **trace** (`skipped-pending-trace` until deployed) — asserted on the flow
  step-trace (`flow.steps` per turn + the `GET /flow/trace` accumulation): the exact
  state-enter sequence, the fired-transition ids, and guard-trip events.

### Trace contract (the dependency)

The suite consumes a step-trace shipping in a parallel backend PR
(**flow-step-trace**). When an agent's flow is active, `chat/turn` responses will
carry `"flow": {"active": true, "current_state": "<id>", "steps": [ <events> ]}`
and `GET /api/agents/{id}/flow/trace?conversation_id=...` returns the full
accumulated `{steps:[...]}`. Step event kinds: `state_enter`, `say_emitted`,
`transition_check{result:"fired|not_satisfied|error"}`, `tools_gated`, `var_set`,
`guard_trip`, `flow_end`.

Until that PR is merged + deployed the `flow` field is absent (the trace endpoint
404s). The harness detects this per run: the observable assertions gate the suite
now, and every trace assertion reports `skipped-pending-trace` instead of failing.
When the field appears, the same assertions turn strict with no harness change.

## Output

Per run, into `results/whissle/flow/`:

- `<id>_<timestamp>.jsonl` — a step log: one record per turn (user message, agent
  reply, `tools_used`, any step events) plus one record per assertion (name, tier,
  pass/fail/skip) and a final summary. This is where a human inspects exactly where
  a flow diverged. (Git-ignored — regenerated each run.)
- `<id>.json` / `<id>.md` — the latest full result + a human-readable summary
  (committed, matching the `results/whissle/*.json` convention).

## Cleanup

Every scenario creates ONE throwaway agent (`flowbench-<id>`) and **deletes it in
every exit path** (success, assertion failure, or setup error). The runner also
does a belt-and-suspenders sweep at the end: it lists agents and deletes any
`flowbench-*` that survived, so a crashed run never leaves agents behind.
