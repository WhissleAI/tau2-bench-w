# Whissle flow-edit sensitivity suite

Proves the **studio edit → publish → runtime** chain end-to-end. In the owner's
words: *"for an agent like headache, we have a conversation flow with many steps.
Test the conversation by making changes to each step in the conversation flow and
check that the bench picks those changes up — that proves the flow is properly
connected: when a user edits a flow and publishes it, the live conversation has
the required changes."*

Where `run_flow.sh` checks a flow *behaves*, and `run_flow_sim.sh` hunts
state-tracking bugs with a simulated user, this suite answers a different
question: **is the flow the studio saves the flow the conversation runs?** Every
edit travels through the *exact* API the flow-designer UI uses — never a
backdoor — and is then hunted for in a real conversation.

## What it does, per mutation

For each step-kind of the flow contract it generates one targeted, minimal edit
that plants an unambiguous sentinel, then walks the studio user's path against a
fresh throwaway `flowsim-*` agent:

1. **create** an agent of `--agent-type` (the backend auto-attaches the type's
   default flow, e.g. `prompts/agent_types/headache_enrollment/flow.json`) and
   read the baseline flow back;
2. **validate** the mutated flow through `POST /api/agents/{id}/flow/validate`
   (the studio's dry-run validator — every mutation must be a legal edit);
3. **stage as draft** — `PATCH /api/agents/{id}?target=draft {"flow": …}` — and
   assert (a) the draft is staged (`GET ?include=draft` → `has_draft`, overlay
   matches), (b) the **live** flow is byte-identical to baseline, and (for cheap
   probes, on a *separate* throwaway agent) (c) a live conversation shows **no
   trace of the edit**: *draft-only must not change the live conversation*;
4. **publish** — `POST /api/agents/{id}/publish` — and assert the live flow now
   carries the edit: *publish must*;
5. **probe** — a short scripted conversation (2–6 turns; never a full intake)
   over the text channel (`POST /chat/turn` drives the identical `FlowRuntime`
   the voice pipeline runs), asserting the mutation's sentinel in the transcript
   **and** the persisted flow step-trace (`GET /flow/trace`). With
   `--mode voice` (or `--voice-spot-checks`) the probe runs over the **real
   voice pipeline** (STT → flow-brain → TTS over LiveKit), where a say-sentinel
   is additionally verified in an independent **re-ASR of the captured bot
   audio**;
6. **delete** the agent (`?confirm=true`), always — even on error.

## The mutation matrix

| kind | edit | detection signal |
|------|------|------------------|
| `say` | replace the entry `say` text with `SENTINEL ALPHA — …` | sentinel in the agent's reply + `say_emitted` trace step (+ bot-audio re-ASR over voice) |
| `conversation` | replace the first conversation state's goal with *"ask for their favorite color"* | the agent asks for the favorite color |
| `transition` (condition) | tighten the advance `llm_condition` to fire only on the magic word *pineapple* | routing **holds** on a normal "yes, I'm ready" and **advances** only after the magic word (`transition_check` results) |
| `transition` (retarget) | point the advance edge at the closing state | flow enters the new target, never the old one |
| `tool_gate` (remove) | empty a state's `allowed_tools` | `tools_gated` excludes the tool and it is never invoked |
| `tool_gate` (add) | add a tool to a tool-less state | `tools_gated` now admits it |
| `state_remove` | delete a mid-flow state, rewiring its inbound edges forward | `states_visited` skips it and reaches the forward state directly |
| `variable` | insert a `set_variable` state on the entry path + an `expression` edge keyed on it | `var_set` appears in the trace and the expression edge routes the next turn to the close |

Generation is generic — anchors (entry say, first/second conversation state,
advance edge, closing state, tool-ful/tool-less states) are resolved by walking
any flow — so the suite is parameterized by `--agent-type`
(`headache_enrollment` first). Kinds a flow's shape can't support are reported
as *skipped*, never silently dropped. Probe lines can be tuned per type in
`data/flow/mutation_probes.json`.

## Verdicts

A mutation **passes** only if every executed phase holds. A FAIL row is a
product bug — one of:

- a **draft leak**: a draft-target edit that changed the live conversation
  before publish;
- an **unpropagated publish**: the API stored the edit but the conversation
  kept running the old flow;
- a **broken step connection**: the runtime accepted the flow but the mutated
  step's behavior (say text, goal, routing, gate, skip, variable) never
  manifested.

The report — per-mutation PASS/FAIL with expected vs observed, per phase — lands
in `results/whissle/flow_mutation/<agent_type>/REPORT.md` + `report.json`, plus a
per-mutation evidence JSON (full probe turns + step-trace + captured audio paths
over voice). Exit status is non-zero when any mutation fails.

## Usage

```bash
cp .env.example .env      # WHISSLE_API_KEY=wsk_… (agents:write); optional WHISSLE_BASE

./run_flow_mutation.sh plan --agent-type headache_enrollment       # print the matrix
./run_flow_mutation.sh run  --agent-type headache_enrollment       # text-mode matrix
./run_flow_mutation.sh run  --agent-type headache_enrollment --voice-spot-checks
./run_flow_mutation.sh run  --agent-type headache_enrollment --mode voice
./run_flow_mutation.sh run  --agent-type headache_enrollment --mutation say_sentinel_greet
```

- `--mode text` (default) runs the full matrix over the deterministic text
  channel — fast, cheap, full step-trace, same `FlowRuntime` as voice.
- `--voice-spot-checks` re-probes **one mutation per step-kind** over the real
  voice pipeline after its text probe (the "voice-bench picks it up" proof
  without running the whole matrix over audio).
- `--mode voice` runs every probe over voice.
- `--draft-probe default|all|none` controls the behavioral draft-inertness
  probes (structural draft assertions always run). `default` runs only the
  1-turn ones.

## Design notes

- **Edit path = user path.** Mutations are applied with the same calls the
  studio (and `whissle agents flow set/publish`) makes: `PATCH ?target=draft` →
  `POST /publish`, validated by `POST /flow/validate`. The draft/publish
  distinction is itself asserted, both structurally and behaviorally.
- **Throwaway agents only.** Every mutation gets a fresh `flowsim-*` agent
  (mutate → test → delete, `confirm=true`); shared/seeded agents are never
  touched, and the existing `flowsim-*` cleanup sweep catches any straggler.
- **Draft-probe isolation.** The studio text channel *resumes* the caller's
  open thread per agent (`conversations.open_or_resume` on
  `studio:<user>`), and a thread's flow position never replays already-passed
  states. A pre-publish probe on the main agent would therefore poison the
  post-publish probe (it would resume mid-flow and e.g. never re-speak the
  greeting). The behavioral draft probe runs on its **own** throwaway agent.
- **Deterministic probes.** Scripted lines (not an LLM persona) keep the 2–6
  turn probes cheap and repeatable; the only LLM in the loop is the product's
  own runtime. Checks are pure functions over the transcript + step-trace —
  unit-tested offline in `tests/test_flow_mutations.py` against the fixture
  `data/flow/headache_enrollment.flow.json` and a mock draft/publish backend
  (including a draft-leak backend and a stale-runtime backend the suite must
  flag).
