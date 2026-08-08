# MedAgentBench — Whissle adapter

[MedAgentBench](https://github.com/stanfordmlgroup/MedAgentBench) (Jiang et al.,
*NEJM AI* 2025) is a FHIR-grounded clinical agent benchmark: **300
physician-written tasks** across **10 categories** over a virtual EHR seeded with
**100 patient profiles / ~700k data elements**. It measures exactly the
capability Whissle already ships in production — FHIR R4 read plus write-back —
against tasks a clinician wrote, which is the credential a healthcare SI channel
asks for.

This adapter plugs Whissle in as the agent under test, and adds a write-integrity
layer the upstream harness does not have.

---

## The headline: MedAgentBench never actually writes

This is the single most important thing to know before quoting any Action
number, ours or anyone's.

Upstream's harness (`src/server/tasks/medagentbench/__init__.py`) handles a POST
like this:

```python
elif r.startswith('POST'):
    try:
        payload = json.loads('\n'.join(r.split('\n')[1:]))
    except Exception:
        session.inject({... "Invalid POST request"})
    else:
        session.inject({... "POST request accepted and executed successfully. ..."})
```

The payload is parsed and then **thrown away**. Nothing is ever sent to the FHIR
server. The graders later recover the payload string out of the transcript
(`extract_posts`) and string-compare its fields.

So the published **Action success rate measures the intent to write, not a
write.** Three distinct events are collapsed into one number, and this adapter
pulls them apart:

| event | meaning | who measures it |
|---|---|---|
| **said** | the agent's own words claim a chart action was carried out | this adapter |
| **emitted** | the agent emitted a POST the harness accepted | upstream |
| **wrote** | the FHIR resource actually exists in the chart afterwards | this adapter |

`said` without `emitted` is the failure the backend's `transition.requires_tool`
+ tool-state `outcome_variable` (#647) were shipped to close. This benchmark is
the only place we can measure its rate against physician-written clinical tasks.

---

## What the benchmark actually requires

**Agent protocol — text, not JSON tool calls.** One action per turn, plain text:

```
GET <url>
POST <url>
<json payload>
FINISH([answer1, answer2, ...])
```

Anything else ends the episode as `agent_invalid_action`. The observation is
injected back as a **user** message. Round budget is **8**
(`configs/tasks/medagentbench.yaml`). The agent is shown a catalogue of **9 FHIR
functions** (`funcs_v1.json`) covering GET/POST on Condition, Observation,
MedicationRequest, Procedure, ServiceRequest and Patient.

**Metric — strict success rate**, `correct / N`. The paper reports overall SR
plus a **Query / Action** split and a per-category breakdown:

| categories | kind | n | what |
|---|---|---:|---|
| task1, task2, task4, task6, task7 | **Query** | 150 | MRN lookup, age, last magnesium in 24h, mean glucose in 24h, last glucose |
| task3, task5, task8, task9, task10 | **Action** | 150 | record BP, conditional IV magnesium, orthopedic referral, potassium repletion + morning lab, HbA1C + conditional lab order |

Query/Action is exactly this read-only vs write-capable split — verified against
the published table: Claude 3.5 Sonnet v2 = 209/300 = 69.67 % overall,
128/150 = 85.33 % Query, 81/150 = 54.00 % Action.

Two details that trip people up:

* **Read-only tasks fail on *any* POST**, even a malformed one the harness
  rejected. `check_has_post` is looser than `extract_posts`, deliberately.
* **The conditional tasks (5, 9, 10) frequently require ordering *nothing*.**
  Over-ordering scores zero. Correctly declining is the right answer, and the
  agent usually says so in words — which is why our claim detector has an
  explicit negation guard (see below).

### Corrections to the brief

* It is **not** a JSON tool-calling benchmark — binding native tool schemas
  would change the task. Mode A uses the upstream text protocol verbatim.
* "Clinical documentation" is one Observation-recording category (task3) plus a
  free-text referral note (task8), not a general documentation suite.
* The grading module (`refsol.py`) is **not in the public repo** — it is a
  separate Stanford Box download, kept out of training corpora. We do not vendor
  it; see *Grading* below.

---

## How the FHIR environment is provisioned

A prebuilt HAPI FHIR server, seeded with all 100 patients:

```bash
docker pull jyxsu6/medagentbench:latest
docker run -d --rm -p 8080:8080 --name medagentbench-fhir jyxsu6/medagentbench:latest
# cold start ~2 min; verify:
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8080/fhir/metadata   # 200
```

The image is `linux/amd64` (emulated, and slower, on Apple silicon). If port 8080
is taken, map another and point the adapter at it:

```bash
docker run -d --rm -p 8090:8080 --name medagentbench-fhir jyxsu6/medagentbench:latest
export MEDAGENTBENCH_FHIR_BASE=http://localhost:8090/fhir/
```

The **trailing slash is load-bearing** — every grader builds URLs by direct
concatenation (`f"{base}Observation"`) and compares for equality.

Task data is fetched, not vendored, so a run is always measured against
upstream's current files:

```bash
uv run python -m tau2.health.medagent.run fetch
```

---

## The two modes

### Mode A — `brain-parity` (default, the publishable number)

The benchmark's own FHIR environment and its 9 functions, bound to the Whissle
brain via `POST {WHISSLE_BASE}/api/bench/agent-turn`. Upstream prompt, upstream
text protocol, upstream grading. **Apples-to-apples with the published
baselines.**

The one deliberate deviation: upstream sends no system prompt, but
`agent-turn` with `system=null` lets the configured platform agent's own persona,
KB and company-brain overlays layer in — which is not what mode A measures. We
therefore send a minimal neutral system prompt. Configurable:
`--system-mode neutral | prompt-as-system | agent-default`, and the choice is
recorded in every result file.

### Mode B — `agent-tools` (the shipped product)

The Whissle `ehr_assistant` agent using its **own registered `fhir_*` tools**,
executing server-side against the benchmark's virtual EHR. This answers the
question an SI actually asks: *does the product you ship do this?*

The two modes grade differently, on purpose:

* mode A grades the POST payload recovered from the transcript (upstream parity)
* mode B grades **the FHIR resources that exist afterwards** (ground truth)

Mode B is therefore immune to the said-vs-wrote blind spot by construction: an
agent that narrates an order it never placed scores zero.

**Status: designed, gated, not blocking.** `preflight-mode-b` reports exactly
what is missing and mode A is unaffected:

```bash
uv run python -m tau2.health.medagent.run preflight-mode-b
```

Preconditions:

1. The `ehr_assistant` agent type exists and an agent of that type is in the org
   with enabled `fhir_*` tools. *(A colleague is seeding this.)* Set
   `WHISSLE_EHR_AGENT_ID`.
2. The virtual EHR is reachable **from the backend** — tools execute
   server-side, so `localhost:8080` will not do, and the backend's
   `validate_fhir_config` requires https for non-loopback hosts. Set
   `MEDAGENTBENCH_FHIR_PUBLIC_BASE` to a tunnelled https URL.
3. A text-turn endpoint that executes the agent's real tools. `/api/bench/agent-turn`
   is deliberately *not* it — it executes nothing, and per-request `tools` fully
   replace the agent's own. That is exactly right for mode A and exactly wrong
   for mode B.

---

## Write integrity

`--write-check` controls how hard we interrogate each write:

| mode | mutates? | asks the EHR | use |
|---|---|---|---|
| `none` | no | nothing | pure upstream parity |
| `validate` | **no** | `POST {Resource}/$validate` — strict FHIR R4 conformance | **default**; safe against the server being read for grading |
| `execute` | yes | `$validate` **and** a real `POST`, then GETs the resource back | proves the write landed; use a disposable container (`--cleanup-writes` deletes afterwards, on by default) |

The observation handed back to the agent stays **byte-identical to upstream** in
every mode, so the agent's behaviour — and therefore the score — is unaffected by
the instrumentation.

**Conformant and stored are different questions, and they disagree.** Found on
task8: MedAgentBench's grader requires `note` to be an *object*
(`payload["note"]["text"]`), but FHIR R4 defines `ServiceRequest.note` as
`0..* Annotation` — an **array**. HAPI's strict `$validate` rejects the object
form; HAPI's *create* endpoint leniently coerces it to `note: [{...}]` and stores
it. So the benchmark's own reference shape is non-conformant FHIR, and an agent
emitting *correct* FHIR would **fail** task8. We report `emitted_but_ehr_rejected`
and `emitted_nonconformant_fhir` as separate signals rather than collapsing them.

### Claim detection

`said_action` fires on completed-action language ("I have ordered…", "the referral
has been placed"). Three things are explicitly **not** claims:

* intentions — "I will order the magnesium"
* questions — "should I place the referral?"
* **negations** — "No replacement IV magnesium order was placed"

The negation guard is not cosmetic. On the conditional tasks the correct answer
is frequently to order nothing *and say so*; without it, the headline
said-but-did-not-write rate is dominated by agents behaving correctly. (This
fired as a false positive on task5_1 in the first smoke run and is now pinned by
tests.)

---

## Grading is programmatic — there is no judge LLM here

Unlike the other two health adapters, MedAgentBench needs **no LLM except the agent
under test**, and therefore has no `--judge-provider` flag. There is nothing to route.

* No patient simulator: the task text is fixed and the agent talks to a FHIR server,
  not to a person.
* No LLM-as-a-judge: `grader.py` answers a strict boolean per task by checking the
  trajectory against constants stated in the task's own `context`/`instruction` (NDC
  codes, SNOMED/LOINC codes, dosing rules, referral free text). `--refsol` swaps in
  upstream's official grading module for the number you publish; both are
  deterministic.

The practical consequence is worth stating plainly: a MedAgentBench number carries **no
judge-independence caveat at all**, because no model produced it. It needs
`WHISSLE_API_KEY` and a FHIR server, and it always did.

## Running it

```bash
export WHISSLE_BASE=https://aws-gateway-backend.whissle.ai/bot
export WHISSLE_API_KEY=wsk_...
export WHISSLE_AGENT_ID=<agent uuid>
export MEDAGENTBENCH_FHIR_BASE=http://localhost:8080/fhir/

uv run python -m tau2.health.medagent.run fetch          # once
uv run python -m tau2.health.medagent.run list           # category breakdown
```

**Cheap subset run (first-class — start here):**

```bash
uv run python -m tau2.health.medagent.run run --limit 10
```

`--limit` is **stratified**: it takes a round-robin slice across all 10
categories, so a 10-task run still covers both Query and Action. A subset number
is never directly comparable to the published table, and the runner says so
loudly and records `n` everywhere.

Filter by category or exact task:

```bash
uv run python -m tau2.health.medagent.run run --tasks task3,task8
uv run python -m tau2.health.medagent.run run --tasks task8_1,task9_2
uv run python -m tau2.health.medagent.run run --categories task5,task9
```

**Full suite (the coordinator schedules this — do not run it casually):**

```bash
uv run python -m tau2.health.medagent.run run \
  --concurrency 4 \
  --write-check execute \
  --run-name full-300
```

That is all 300 tasks, ≈8 rounds each. Add `--refsol /path/to/refsol.py` for the
official grading module when publishing a number.

### Grading

`--refsol` is not vendored (see above). The built-in graders are a faithful
reimplementation derived from the published task specs — every constant they
check (NDC `0338-1715-40`, `40032-917-01`; SNOMED `306181000000106`; LOINC
`4548-4`, `2823-3`; the dosing ladders; the referral free text) is stated in the
task's own `context`/`instruction`, so nothing here is secret. `Trajectory`
exposes `.history` / `.result` in exactly the shape `refsol.py` expects, so the
official module loads unmodified.

### Results

Per-run under `results/whissle/medagentbench/<mode>_<name>/`:

* `SUMMARY.json` / `SUMMARY.md` — the paper's table shape (overall, Query,
  Action, per-category), plus write integrity, status counts, findings, and the
  published baselines inline for comparison
* `tasks/<task_id>.json` — full prompt, messages, every action + observation,
  the resulting FHIR write records (payload, created id, read-back, conformance
  issues), grade with expected-vs-got, and findings
* …plus a `diagnostics` block on every task record, in the shape all three health
  benchmarks share (**[HEALTH_DIAGNOSTICS.md](HEALTH_DIAGNOSTICS.md)**), so one
  reader works across them

### The `diagnostics` block

`tools.calls` normalizes every `GET`/`POST` with its **resolved** URL and JSON body
and the observation it got back, `ok`/`error` included. `tools.writes` lifts the
said-vs-emitted-vs-landed split out of the integrity report and adds a
plain-language `verdict` — `SAID but never EMITTED`, `EMITTED but NOT ACCEPTED`,
`EMITTED and LANDED` — so the headline failure this suite exists to catch cannot
flatten into "tools called: 0". `provenance` copies the run's agent, base URL,
grader, system mode and write-check mode onto each case, with the category as its
stratum.

Two sections are deliberately marked unavailable rather than zeroed. Every round is
a `POST /api/bench/agent-turn` — a stateless brain call that runs no flow engine and
mints no conversation — so there is **no flow trace**; and nothing here is spoken, so
there are **no voice signals**. Both carry `available: false`, a reason, and `null`
payloads. Likewise `cost` reports `judge_cost_usd: null`, not `$0.00`: grading here
is programmatic and no judge LLM is called at all.

**No `--voice-subset` flag.** MedAgentBench's actions are structured `GET`/`POST`
strings executed by the harness; there is no spoken surface to drive, and inventing
one would produce a number that measures nothing. The other two adapters have the
flag because they have real dialogue.

### Infrastructure failures

An episode whose brain or EHR was unreachable is classified **`infra_fail`**,
retried once, and **excluded from every denominator** — a network outage is not a
wrong clinical answer. It is reported, never silently dropped, and uses the
`infra_fail` type from `tau2.flow.analyze` so it means the same thing here as in
the flow suites. A run where everything failed reports `success_rate_pct: null`,
not `0`.

---

## Tests

```bash
uv run pytest tests/test_medagentbench_*.py -q
```

107 offline tests, no network — the Whissle endpoint and the FHIR server are both
mocked. They cover the upstream protocol quirks (so we can tell if our number
stops being comparable), the adapter contract and retry/error taxonomy, tool-call
translation both ways, all 10 graders including the conditional-order traps,
the said-vs-wrote check, the conformance-vs-stored split, scoring aggregation,
infra-fail exclusion, and stratified subset selection.
