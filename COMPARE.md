# Scenario comparison: Whissle vs. external voice-agent platforms

> ## ⚠ READ THIS FIRST — Whissle's differentiator is currently DISABLED
>
> **Whissle's acoustic metadata head (emotion / intent / hesitation off the Whissle ASR) is NOT producing in production.**
> The gRPC metadata target `136.115.121.123:50051` is unreachable from the AWS prod hosts — `StatusCode.UNAVAILABLE`, `"tcp handshaker shutdown"`. Verified **2026-08-08**.
>
> Therefore **any Whissle-vs-vendor comparison run today measures Whissle's LLM + flow engine against the vendor's full stack, with Whissle's stated differentiator disabled.**
>
> A Whissle *win* under these conditions was not won by the metadata head. A Whissle *loss* is not evidence the head would not have changed the outcome. Neither may be quoted as if it were.

This banner is generated from **one constant** — `DIFFERENTIATOR_OUTAGE` in
[`src/tau2/compare/honesty.py`](src/tau2/compare/honesty.py). Setting it to `None`
removes the banner from every generated report and flips the machine-readable
`differentiator_status` field from `"disabled"` to `"operational"`. That is the
entire restoration edit. Update the copy in this file at the same time.

---

## What this package is

`tau2.compare` runs six scenarios against Whissle and, where credentials exist,
against a competing voice-agent platform, and writes a report that shows **why**
each system did what it did — not just whether it passed.

The premise being tested is architectural. Whissle is a **cascade**: ASR → LLM →
flow engine → TTS, with each stage separately addressable and a step trace you
can read afterwards. ElevenLabs Conversational AI (and comparable products) are
increasingly **opaque speech-to-speech**: audio in, audio out, no separable
transcript, no state machine, no trace. That difference should have measurable
consequences in both directions, and the scenarios are chosen where it should.

**A pass/fail-only comparison is explicitly out of scope.** The deliverable is
the Whissle trace excerpt attached to every result. If all you want is a
scoreboard, this package is the wrong tool and its output will annoy you.

## Quick start

```bash
python -m tau2.compare.run list                      # scenarios + vendor availability
python -m tau2.compare.run run --out results/compare # run everything runnable
python -m tau2.compare.run run --vendor whissle --scenario misheard_proper_noun --out /tmp/x
python -m tau2.compare.run report --out results/compare/<run-id>
```

Running with no ElevenLabs credentials **succeeds**. It produces a Whissle-only
report whose title, first section, JSON and terminal summary all say
`NOT A COMPARISON`. Exiting non-zero there would push an operator toward the one
thing this package must never do.

### Environment

| Variable | Required for | Notes |
| --- | --- | --- |
| `WHISSLE_API_KEY` | the `whissle` vendor | a `wsk_` key |
| `WHISSLE_BASE` | optional | defaults to `https://aws-gateway-backend.whissle.ai/bot` |
| `WHISSLE_COMPARE_AGENT_ID` | optional | drive an existing agent instead of a throwaway |
| `ELEVENLABS_API_KEY` | the `elevenlabs` vendor | |
| `ELEVENLABS_AGENT_ID` | the `elevenlabs` vendor | a Conversational AI agent id |

---

## The core distinction: setup-matched vs. published-external

There are exactly two ways a competitor number can enter a comparison, and they
are **not interchangeable**.

### `setup_matched`

We ran both systems ourselves, on the same scenario file, in the same window,
scored by the same deterministic criteria. Requires credentials and a live agent
on both sides.

**This is the only kind that supports a head-to-head verdict.**

### `published_external`

A number quoted from a vendor's own published material — a docs page, a benchmark
post, a launch blog. We did not run it. Usually we cannot even establish what it
measured. The constructor in
[`baselines.py`](src/tau2/compare/baselines.py) **refuses to build one** without
all four of:

1. a **citation URL**,
2. a **publication date**,
3. the vendor's **exact metric definition**, and
4. an explicit list of **what we could not match** (task set, scoring rule, audio
   conditions, model version).

An empty `unmatched` list is rejected: for a published number, "we matched
everything" is essentially never true, and if it were, it would be a
`setup_matched` run rather than a quote.

Rendering enforces the rest. Every published value is labelled
`[VENDOR-PUBLISHED — NOT MEASURED BY US]`, and any table holding both kinds
prints the mixing warning **above** the numbers, not under them — a reader who
reads one line reads the caveat.

`baselines.medagent_published_baselines()` re-expresses the existing
MedAgentBench leaderboard (`tau2.health.medagent.data.PUBLISHED_BASELINES`) in
this taxonomy rather than copying its numbers, so the two paths cannot drift.

### The refusal rule

**A comparison with no `setup_matched` run does not emit a head-to-head
verdict.** It emits `cannot_compare` and the reason. Published numbers cannot
stand in for one, and neither can a single-vendor run.

---

## Honesty rules

These are enforced in code and covered by tests in
[`tests/test_compare.py`](tests/test_compare.py), not left to reviewer diligence.

**1. Never fabricate a competitor number.** The ElevenLabs adapter detects
missing credentials and returns a structured *not runnable — credentials absent*
result. There is no fallback-to-estimate path, no simulation, no interpolation
from published material, anywhere in the package. A vendor we could not reach did
**not** score zero; it has no score, and every criterion about it evaluates to
*cannot tell*.

**2. Absence is not a measurement.** Inherited wholesale from
`tau2.health.diagnostics`: an unavailable section sets every payload field to
`None` with a canonical reason, never `[]` or `0`. A reader who sees
`turns: null` cannot mistake it for "nothing fired".

**3. "Cannot tell" is a first-class answer and is never resolved in Whissle's
favour.** Criterion results are `Optional[bool]`. Any unknown makes the scenario
`cannot_tell` — including the tempting case where Whissle has a trace and the
vendor publishes none. *Having more evidence than your competitor is not the same
as winning.*

**4. Two systems that heard different sentences did not run the same scenario.**
The ElevenLabs adapter drives the vendor's own LLM simulated user (there is no
turn-by-turn text endpoint equivalent to Whissle's `/chat/turn`). Every run is
checked afterwards for verbatim parity against the script; a run that drifted is
marked `utterances_matched: false` and the pair is refused as setup-matched.

**5. A win without mechanism evidence says so, in its own verdict line.** If
Whissle passes but the trace does not show the claimed mechanism firing, the
verdict text carries: *"the Whissle trace does not establish that the claimed
mechanism fired … so this win does not support the scenario's hypothesis."*

**6. No LLM judge.** Every criterion is a substring, a regex, or a fact about the
tool record — checkable by hand from the transcript printed in the report. In a
Whissle-vs-vendor comparison any judge is either our model marking its own
homework or the competitor's marking its competitor's; the independence problem
`tau2.health.model_router.is_independent` exists to flag has no good answer here,
so we avoid needing one.

**7. Proxies announce themselves.** A scenario whose real mechanism is acoustic
but which is being driven over text carries a `proxy_note` printed beside its
result, stating what the proxy does and does not measure.

---

## The six scenarios

Data lives in [`data/compare/scenarios.json`](data/compare/scenarios.json) —
scenarios are data plus a loader, never hardcoded Python. Each declares a
hypothesis (with a **falsifier**, required by the loader), deterministic pass
criteria, and the flow-trace evidence that would prove the mechanism fired.

| id | expectation | why |
| --- | --- | --- |
| `misheard_proper_noun` | cascade wins | The mis-heard value is a separately addressable transcript token and a flow variable a correction can **overwrite**. An opaque model has no such handle. |
| `barge_in_interrupt` | **cascade loses** | On genuine acoustic barge-in a monolithic model hears the overlap in the same forward pass; a cascade must detect, stop TTS, discard the in-flight turn and re-plan across component boundaries. |
| `intent_switch_midturn` | cascade wins | When the operative intent is the second half of an utterance, acting on the first half requires a transition into a state where the creation tool is even bound. |
| `hesitation_and_silence` | cascade wins | Hesitation as an explicit signal lets the agent wait and re-ask instead of treating a stalled caller as a completed turn. **Runs through the disabled head — currently unprovable.** |
| `required_field_no_fabrication` | cascade wins | A required field is a flow variable with a recorded `source`; a value never extracted has no source, and the write is gated on the variable being set. |
| `mutating_write_matches_speech` | cascade wins | Said-versus-written divergence is *detectable* in a cascade and undetectable in an opaque stack — the same said/emitted/landed distinction MedAgentBench exposed. |

A suite where the cascade wins every scenario would be a marketing document; a
test asserts both directions are represented.

### Trace evidence

Each scenario names what must appear in the Whissle flow trace — a second
`var_set` for a corrected field with `source: extraction`, a `transition_check`
that did *not* fire with a recorded reason, a `tools_gated` step showing the
write was never bound, the **absence** of a `var_set` for a refused field. Three
outcomes:

- **found** — the trace shows it; the mechanism fired.
- **absent** — the trace was read and does not show it; whatever produced the
  outcome, it was not the claimed mechanism.
- **cannot tell** — there is no trace to read (bench endpoint, fetch failure,
  vendor publishes nothing), or the mechanism runs through the disabled metadata
  head.

For a product whose pitch is inspectability, an unreadable run is itself the
finding, and the report says so rather than leaving a blank.

---

## Transports, and why `/chat/turn`

| Transport | Flow trace | Voice signals | Metadata |
| --- | --- | --- | --- |
| `POST /api/bench/agent-turn` | **no** | no | no |
| `POST /api/agents/{id}/chat/turn` + `GET /flow/trace` | **yes** | no | no |
| LiveKit voice (`/api/bench/voice/start`) | yes | yes | yes (when the head is up) |

The adapter **defaults to `/chat/turn`** precisely because
`/api/bench/agent-turn` is a stateless brain call: it runs no `FlowRuntime` and
mints no conversation row, so no trace exists. `--transport bench_agent_turn`
remains available as a cheap smoke path; runs driven that way carry
`diagnostics.REASON_BENCH_ENDPOINT` on the flow section, and the report states
that they contribute no mechanism evidence rather than showing an unexplained
blank.

By default each scenario gets a **throwaway agent** of its declared
`agent_type` (the backend auto-attaches that type's default flow), torn down in a
`finally`. Pass `--whissle-agent-id` to drive a long-lived agent instead; the run
records which happened, because "we ran your published agent" and "we ran a
fresh default-flow agent" are different claims.

---

## Output

Each run writes to `<out>/<run-id>/`:

- `report.md` — the banner, availability, rollup, and per scenario: the
  hypothesis, the criteria table, the **flow-trace narrative**, and both
  transcripts.
- `report.json` — the same, machine-readable, always carrying
  `differentiator_status` and the full `outage` block.
- `runs/<scenario>__<vendor>.json` — one per-vendor case file via
  `tau2.health.diagnostics.write_case`, so a single scenario's evidence can
  travel into a bug report on its own.

`report --rerender` regenerates Markdown from a written `report.json` and prints
**both** the status recorded at run time and the status now, flagging any change.
A report regenerated after the metadata head is restored must not silently claim
the old run had it.

---

## Module map

| File | Responsibility |
| --- | --- |
| `honesty.py` | the single outage constant; banner text and machine-readable block |
| `baselines.py` | the `setup_matched` / `published_external` taxonomy and its rendering |
| `scenarios.py` | dataclasses + loader for `data/compare/scenarios.json` |
| `criteria.py` | deterministic checks; `Optional[bool]` outcomes |
| `evidence.py` | flow-trace reading, mechanism verdict, narrative extraction |
| `compare.py` | verdicts, and the refusals |
| `report.py` | Markdown + JSON writers |
| `run.py` | the CLI |
| `vendors/base.py` | the adapter seam; `Preflight`, `ScenarioRun`, `not_runnable` |
| `vendors/whissle.py` | drives `/chat/turn`, captures the trace |
| `vendors/elevenlabs_convai.py` | drives ElevenLabs, or refuses |

`vendors/elevenlabs_convai.py` is deliberately **not** under `tau2.voice.*`:
`tau2.voice.scripts.elevenlabs` already owns that name where ElevenLabs is a TTS
component *we use*. Here it is a competing platform *we measure*. Same vendor,
opposite role — collapsing them would eventually let a synthesis credential
satisfy a comparison preflight.

## Tests

```bash
uv run pytest tests/test_compare.py
make check-all
```

Everything runs offline. No key, no network, no vendor account.
