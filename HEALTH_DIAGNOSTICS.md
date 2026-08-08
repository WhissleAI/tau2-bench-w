# Health-benchmark diagnostic artifacts

One artifact shape for all three healthcare adapters — PatientAgentBench,
MedAgentBench, AgentClinic — so a single reader works across them and a scaled run
stays debuggable.

**The problem this solves.** Each adapter used to persist a transcript, a tool list
and a score. At 3 cases you can eyeball that. At ~100 cases each you cannot: a bare
score with no trace is not debuggable, and three record shapes mean three readers.
The flow-sim harness already produced the forensics we wanted into its
`<task>_<ts>.session.json` sidecar; this is that discipline, factored into
`src/tau2/health/diagnostics.py` and emitted by all three adapters.

---

## The shape

Every per-case record grows **one** key, `diagnostics`. Nothing else moves — every
existing key (`case_id` / `rubric_scores` / `integrity` / `score` / …) stays exactly
where it was, so current report paths are unaffected.

```jsonc
"diagnostics": {
  "schema": "tau2.health.diagnostics/v1",
  "benchmark": "patientagentbench" | "medagentbench" | "agentclinic",
  "case_id": "...",
  "mode": "text" | "harness" | "native" | "voice",

  // one-lookup answer to "was this measured?"
  "availability": {
    "flow_available": false,   "flow_reason": "driven over POST /api/bench/agent-turn …",
    "signals_available": false, "signals_reason": "text mode — these are VOICE-pipeline signals …",
    "metadata_sidecar_available": false, "metadata_sidecar_reason": "…",
    "tools_available": true,   "tools_reason": null
  },

  "provenance": { benchmark, mode, transport_endpoint, agent_id, base_url, seed,
                  stratum, judge{…}, judge_provider, judge_independent,
                  run_id, run_dir, harness_commit, captured_at, …},
  "cost":       { available, judge_calls, judge_cost_usd, agent_calls, … },

  "flow":       { available, reason, source, conversation_id, current_state,
                  start_state, final_state, ended, flow_end, num_steps,
                  step_counts_by_kind, states_visited, states_declared,
                  states_unvisited, transitions[], transitions_fired[],
                  var_sets[], var_sources{}, guard_trips[], state_divergences[],
                  tools_gated[], says[], steps[] },

  "signals":    { available, reason, source, turns[], summary{ frames_total,
                  by_kind, turns_with_signals, hesitation_turns, shadow_turns,
                  speculative_tools, barge_in_turns, response_latency_ms{p50,p95},
                  turn_completeness{p50}, emotions_seen, intents_seen,
                  emitted_nothing } },

  "metadata_sidecar": { available, reason, source, turns[], summary{…} },

  "tools":      { available, source, calls[{turn,id,name,arguments,result,ok,error}],
                  summary{n_calls,by_name,n_ok,n_error,errors[]},
                  writes{…} },

  "turns": [...], "audio": {...}
}
```

### Flow trace

Every step kind the engine emits is captured verbatim in `flow.steps` and rolled
up: `state_enter`, `transition_check` (**with the `reason` rationale added in
#650** — without it a non-firing transition is an unexplained dead end),
`say_emitted`, `tools_gated`, `var_set` (**with its `source`**: `tool_result` |
`extraction` | `goal_complete` — a value the engine derived and one the caller
stated are different evidence), `guard_trip`, `state_divergence`, `flow_end`.
`step_counts_by_kind` always lists every kind, so a kind that stops being emitted
shows as `0` next to its peers instead of silently vanishing.

### Tool forensics

Not just *which* tools were called: the **resolved** arguments, the result, and
`ok`/`error`. For a benchmark that writes, `tools.writes` carries the three-way
split — what the agent **said**, what it **emitted**, and what actually **landed**
in the EHR — with a plain-language `verdict`.

---

## Which signals exist in which mode

This is the part that must not be fudged. Signals and the metadata sidecar are
produced by the **voice pipeline**. A text run does not have them at zero — it does
not have them at all.

| transport | flow trace | voice signals | metadata sidecar | tool args |
|---|---|---|---|---|
| `POST /api/bench/agent-turn` | **no** [1] | **no** | **no** | yes [2] |
| `POST /api/agents/{id}/chat/turn` + `GET /flow/trace` | **yes** | no | no | yes |
| LiveKit voice (`POST /api/bench/voice/start`) | yes [3] | **yes** [4] | **yes** [4] | yes |

**[1] `/api/bench/agent-turn` exposes no flow block and no trace — verified against
the backend route, not assumed.** It is a *stateless brain call*: it assembles the
real system prompt, calls the LLM, and returns `reply` / `tool_calls` / `content` /
`stop_reason`. It runs no `FlowRuntime` and mints no `conversations` row, so there
is nothing for `GET /api/agents/{id}/flow/trace` to address. All three adapters
drive this endpoint in their default text mode, so all three record
`REASON_BENCH_ENDPOINT` — the reason names the endpoint, because *that* is the
finding.

**[2]** The harness executes the tools itself, so arguments and results are ours to
record and are fully captured.

**[3]** Persisted since PR #613, when `/api/bench/voice/start` returns a
`conversation_id`. Read back with `GET /api/agents/{id}/flow/trace`. Note the
AgentClinic caveat below.

**[4]** Captured live off the LiveKit data channel (`{kind:"signal"}` /
`{kind:"metadata"}` frames). `GET /api/calls/{call_id}/trace` (PR #636) serves the
same two sections, but only for a call with a persisted `calls` row — a **bench
voice room has no such row** (nothing calls the session-save path), so the data
channel is the primary source and the HTTP fetch is a fallback for runs that do
carry a call id (`diagnostics.TraceClient.call_trace` +
`sections_from_call_trace`). The section's `source` field always says which one
produced it.

### AgentClinic voice: a further honest caveat

Bench voice connects with `real=false`, so the pipeline runs the *harness's doctor
prompt* with delegated tools rather than the deployed agent. The deployed agent's
state machine is therefore not what is under test, and the flow section says so
(`REASON_BENCH_VOICE_NO_FLOW`) rather than reporting an empty trace. Signals and
metadata are unaffected — those come from the audio path, which is entirely real.

---

## How absence is represented

An unavailable section is:

```jsonc
{ "available": false,
  "reason": "text mode — these are VOICE-pipeline signals and do not exist for a text run (absence, not zero)",
  "source": null, "turns": null, "summary": null, /* …every payload field null… */ }
```

**Every payload field is `null`. Never `[]`, never `0`, never `{}`.** A reader that
sees `turns: null` cannot mistake it for "no signals fired"; a reader that saw
`turns: []` could — and "the hesitation predictor never fired" is a claim about the
*agent*, while the truth is that no audio was involved. The flat `availability`
block mirrors the same booleans beside their reasons so the question is answerable
with one key lookup and never by counting an empty list. Unit-tested in
`tests/test_health_diagnostics.py::test_unavailable_sections_null_every_payload_field`.

One deliberate exception, in the other direction: over **voice**, a capture that
produced zero frames stays `available: true` with `summary.emitted_nothing: true`.
That *is* a measurement — of the signal emitter or the metadata GPU being dark —
and it is a different fact from a text run having no signals at all.

Canonical reasons live in `diagnostics.py` (`REASON_TEXT_MODE`,
`REASON_BENCH_ENDPOINT`, `REASON_NO_JUDGE`, …) so three adapters cannot drift into
three phrasings of the same gap.

---

## Per-case provenance

`summary.json` already carried the run's provenance. It is now copied **onto every
case** too: agent id, base URL, transport endpoint, mode, seed, the case's sampling
stratum, the judge block with `judge_independent`, run id/dir, harness commit,
capture timestamp — plus per-case cost.

A case file gets copied into a bug report, an issue or a slide on its own. At that
moment "which agent, which judge, was the judge independent, which stratum" has to
travel *with* it.

A benchmark that grades deterministically (MedAgentBench) records
`judge: {available: false, reason: "this benchmark grades deterministically — no
judge LLM is called"}` and a cost section with `judge_cost_usd: null` — never a
`$0.00`, which would read as a measured zero spend.

PatientAgentBench's per-case judge cost is an **allocation** (run total ÷ N — the
jury grades every rubric of every session) and is labelled as one in the record.

---

## Voice subset — scale *and* depth

A 100-case text run gives a score anyone can stand behind: parallel, cheap,
deterministic. But the signals that make a bad score *explainable* only exist over
audio. So drive a small slice of the same cases through the real voice pipeline:

```bash
# AgentClinic: 100 text cases, then re-run 3 of them over voice
python -m tau2.health.agentclinic.run --dataset MedQA --limit 100 --voice-subset 3

# PatientAgentBench: same idea, as a second PatientAgentBench run
python -m tau2.health.patientagent.cli run --mode harness --limit 100 --voice-subset 3
```

* The slice is the **head of the already-seeded sample**, so it reproduces for a
  given `--seed` without introducing a second, unreconstructable draw.
* Voice cases are written and scored **separately** — AgentClinic writes
  `<out>/voice/cases/*.json` + `SUMMARY.voice.{json,md}`; PatientAgentBench runs the
  slice as its own benchmark run under `<output-dir>/<name>/voice/`. A voice number
  carries ASR and TTS error a text number does not; averaging them would destroy the
  only thing that makes the comparison interesting.
* Cases in the slice carry `provenance.voice_subset: true`.
* Both use the existing `src/tau2/flow/voice_transport.py`; no second voice loop
  exists.

**MedAgentBench has no voice path and this flag is deliberately absent.** It is a
FHIR tool-use benchmark whose actions are structured `GET`/`POST` strings executed
by the harness; there is no spoken surface to drive, and adding a fake one would
produce a number that measures nothing. Its envelope states this rather than
offering an option that silently no-ops.

---

## Reading the artifacts

```python
import json, glob
for path in glob.glob("results/whissle/*/*/cases/*.json"):
    rec = json.load(open(path))
    d = rec["diagnostics"]                      # same shape for all three benchmarks
    if not d["availability"]["signals_available"]:
        continue                                # not measured — do NOT count as zero
    print(d["case_id"], d["signals"]["summary"]["hesitation_turns"])
```

MedAgentBench writes to `tasks/<task_id>.json`; the `diagnostics` key is identical.

---

## Files

| file | role |
|---|---|
| `src/tau2/health/diagnostics.py` | the shared schema, section builders, availability rules, `TraceClient` |
| `src/tau2/health/patientagent/diagnostics.py` | PatientAgentBench builder (voice signals off `response_metadata`) |
| `src/tau2/health/medagent/diagnostics.py` | MedAgentBench builder (said / emitted / landed) |
| `src/tau2/health/agentclinic/diagnostics.py` | AgentClinic builder (text vs voice availability) |
| `tests/test_health_diagnostics.py` | the availability + trace-capture contract |
| `tests/test_health_diagnostics_adapters.py` | all three adapters emit the same shape |
