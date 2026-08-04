# Whissle default-flow COVERAGE suite

A companion to the [conversation-FLOW suite](WHISSLE_FLOW.md). Where that suite
**authors** a flow onto a throwaway `text_assistant` and asserts the state machine
runs, this suite supplies **no flow at all**. It proves that every seeded agent
**TYPE** now ships a correct **default** conversation flow: the backend
auto-attaches that type's `prompts/agent_types/<type>/flow.json` on agent
creation, flow-enabled, and the attached flow actually **drives** the call.

It drives the SAME product surface a customer hits
(`POST /api/agents/{id}/chat/turn`) over the deterministic **text channel**,
reading `WHISSLE_BASE` + `WHISSLE_API_KEY` from `.env`. It lives in
`src/tau2/flow/defaults.py` and is invoked with `./run_flow_defaults.sh`.

## Quick start

```bash
cp .env.example .env          # set WHISSLE_API_KEY=wsk_...   (agents:write scope)
./run_flow_defaults.sh                 # all 15 seeded types
./run_flow_defaults.sh debt_collection # just one type
KEEP_AGENT=1 ./run_flow_defaults.sh debt_collection  # leave the agent for debugging

# or directly:
uv run python -m tau2.flow.defaults list
uv run python -m tau2.flow.defaults run --agent-type debt_collection
```

Only base deps are used (`requests` / `typer` / `rich` / `dotenv`) — no `--extra`.

## The 15 seeded types

`dental_receptionist`, `patient_checkin`, `medication_checkin`,
`medical_followup`, `headache_enrollment`, `appointment_reminder`,
`appointment_scheduling`, `renewal_reminder`, `survey_feedback`,
`lead_qualification`, `customer_support`, `sales_handoff`, `car_rental`,
`debt_collection`, `ai_tutor`.

Five start with a `say` state (dental_receptionist, patient_checkin,
medication_checkin, medical_followup, headache_enrollment); the other ten start
with a `conversation` state. The suite discovers each type's `start_state` and
its kind from the **auto-attached flow itself** (nothing is hard-coded), so it
stays correct as the seeded flows evolve.

## What it asserts (per type)

The fixture (`data/flow/defaults.json`) lists the 15 types and a few scripted user
turns each. For every type the harness creates one agent `flowcov-<type>`
supplying **no flow**, then grades three tiers:

| tier | source | proves |
|------|--------|--------|
| **attach** | `GET /api/agents/{id}` | `flow` present + `flow.enabled == true` + `flow.states` non-empty + `flow.start_state` names a real state — the loader auto-attached the type's default flow, flow-enabled |
| **drive** | `chat/turn` × 3-4 | turn-1 `flow.active == true`; the flow **enters its `start_state` first** (the first `state_enter` in the trace — a `say`-start advances past itself via an `always` edge on the same turn, so we assert the first ENTRY, which is the faithful reading of "`current_state == start_state` initially"); the trace has ≥1 `transition_check`; and for `say`-start types the start `say` text (read back from the attached flow) appears **verbatim** in the turn-1 reply |
| **gate** (debt_collection only) | `chat/turn` (unverified) | driving UNVERIFIED turns, **no** balance / amount / debt is disclosed and the flow **never enters** a disclosure state (`disclose_balance`/`pay_now`/`promise_to_pay`) — the identity-verification compliance gate holds |

### Why the start `say` text is read back from the flow

The say-marker assertion pulls the expected string from the agent's own
auto-attached `flow` (the `say` on its `start_state`) rather than hard-coding it,
so a wording change to a seeded flow updates the assertion automatically. If the
say-start's text ever contains a template (`{{…}}`) it would be substituted in the
reply; none of the five say-starts do today.

### No `skipped-pending-trace` tier

Unlike the authored-flow suite, there is **no** graceful-degradation skip here: the
entire point is that the flow (and its per-turn step trace) IS present. A flow that
is absent after retries is a real **FAILURE** — either the `flow.json` did not
package into the image or the loader did not attach it. The GET-for-flow is retried
with backoff (`ATTACH_RETRIES` × `ATTACH_BACKOFF_S`) so a rolling deploy or an
attach that trails creation by a beat does not produce a false negative.

## Output

Per run, into `results/whissle/flow_defaults/`:

- `<type>_<timestamp>.jsonl` — a step log: one record per turn (user, reply,
  `current_state`, step events) plus one record per assertion and a per-type
  summary. Git-ignored (regenerated each run).
- `<type>.json` / `<type>.md` — the latest full result + a human-readable summary
  per type (committed, matching the `results/whissle/*` convention).
- `SUMMARY.json` / `SUMMARY.md` — the combined **type → attached? / driving? /
  pass** table across the whole run.

## Cleanup

Every type creates ONE throwaway agent (`flowcov-<type>`) and **deletes it in every
exit path** (success, assertion failure, or setup error). The runner also does a
belt-and-suspenders sweep at the end: it lists agents and deletes any `flowcov-*`
that survived, so a crashed run never leaves agents behind.
