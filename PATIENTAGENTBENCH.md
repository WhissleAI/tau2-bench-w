# PatientAgentBench adapter

Evaluate a Whissle agent on [PatientAgentBench](https://github.com/amazon-science/PatientAgentBench)
(Amazon Science, [arXiv:2607.25485](https://arxiv.org/abs/2607.25485)) — a clinician-vetted,
patient-facing healthcare agent benchmark.

Adapter code: `src/tau2/health/patientagent/`. Nothing here forks or patches the
benchmark; it plugs into their public registry.

---

## Read this before publishing a number

**1. The benchmark is CC-BY-NC-4.0.** `LICENSE` in their repo is
Attribution-**NonCommercial** 4.0. Using it to produce marketing or competitive
material for a commercial product is very plausibly outside that grant. Their README
also states the benchmark "is not an Amazon product or service … its scores are not a
clinical certification or a deployment-readiness assessment of any system." Get a
legal read before any external publication, and never present a score as a safety or
readiness claim. Internal/research use and an academic write-up with attribution are
the comfortable cases.

**2. There are two modes and they measure different things.** See below. The default
(`--mode harness`) is the only one comparable to their published leaderboard.

**3. Voice numbers are never comparable to their baselines.** Every published
baseline is text, and our voice path necessarily runs in agent-tools mode. The only
honest voice comparison is *our* text agent-tools run vs *our* voice agent-tools run.
`scoring.compare_runs` refuses to render a cross-mode delta without a warning.

---

## The two modes

| Mode | Flag | Agent gets | Measures | Comparable to the paper? |
|---|---|---|---|---|
| Harness tools | `--mode harness` (default) | **Their** ReAct loop, **their** 15 sandbox tools, **their** system prompt; only the model is ours | The **brain** | **Yes** |
| Agent tools | `--mode native` | Its **own** prompt, tools and guardrails | The **product** | **No** |
| Voice | `--mode voice` | Its own prompt/tools, over the real speech pipeline | The **product over voice** | **No** (compare to `--mode native`) |

In harness mode the Whissle agent id only selects the org, model and guardrails — the
prompt and tools come from the benchmark. In native/voice mode the agent is fully
itself, so dimensions that grade sandbox tool use (notably workflow accuracy) are
scoring a different substrate entirely.

---

## How it plugs in

Their assistant is a LangGraph ReAct agent built by
`langchain.agents.create_agent(model=..., tools=..., system_prompt=...)`. Anything
satisfying `BaseChatModel` + `bind_tools` can be the `model`, which is the
apples-to-apples seam.

```
patient simulator (Claude Sonnet 5)
        |  text turn
        v
their ReAct loop  --bind_tools-->  their 15 sandbox tools
        |
        v
   WhissleChatModel              <-- the ONLY thing we replace
        |  POST {WHISSLE_BASE}/api/bench/agent-turn
        v
   deployed Whissle agent (its model + guardrails)
        |
        v
   transcript --> LLM-as-a-Jury (Claude Opus 4.8) --> 6 dimensions x 102 criteria
```

Registration uses their public `register_assistant_agent(name, cls)`, selected by
`agent_class` in the config — so the adapter needs no fork of a repo that explicitly
does not accept pull requests.

Module map:

| Module | Needs | Purpose |
|---|---|---|
| `client.py` | `requests` | Auth, retries, **error taxonomy** (infra vs request) |
| `chat_model.py` | `langchain_core` | LangChain `BaseChatModel`; message + tool translation |
| `agents.py` | PatientAgentBench | The two text agent classes |
| `voice_agent.py` | tau2 voice extras | Live-call agent over `flow/voice_transport.py` |
| `scoring.py` | stdlib | Their exact aggregation + infra-fail exclusion + CIs |
| `sampling.py` | stdlib | Seeded stratified sampling |
| `collect.py`, `report.py` | stdlib | Run dir -> outcomes -> paper-shaped report |

`scoring`, `sampling`, `collect` and `report` are deliberately free of any langchain
import, so the whole collect-and-score path is unit-testable anywhere.

### Infrastructure failures are excluded, not scored

A conversation that dies because our endpoint 5xx'd, timed out, or lost its voice data
channel measured *our uptime*, not clinical quality. `client.py` raises
`WhissleInfraError`, the agent stamps `[WHISSLE_INFRA_FAIL]` on the error, and
`scoring.classify_session` buckets it `infra_fail` — reusing the taxonomy already in
`src/tau2/flow/analyze.py` (imported, not copied). Excluded sessions are counted and
printed in every report; they never silently shrink N.

---

## Setup

PatientAgentBench pins **langchain 1.x**, tau2 pins **0.3.x**, so give it its own venv
and install tau2 into it (verified compatible — `pip check` is clean):

```bash
git clone https://github.com/amazon-science/PatientAgentBench.git ../pab
python3.12 -m venv ../pabvenv
../pabvenv/bin/pip install -e ../pab
../pabvenv/bin/pip install -e .          # this repo, for the adapter
```

Environment:

```bash
WHISSLE_BASE=https://aws-gateway-backend.whissle.ai/bot
WHISSLE_API_KEY=wsk_...                  # a key for the agent's org
WHISSLE_AGENT_ID=<agent uuid>            # GET {WHISSLE_BASE}/api/agents to list

# Only for --judge-provider anthropic|openai (see "Who judges" below):
ANTHROPIC_API_KEY=sk-ant-...             # or AWS credentials for the -bedrock keys
OPENAI_API_KEY=sk-...
```

**The default run needs nothing but `WHISSLE_API_KEY` + `WHISSLE_AGENT_ID`.**

---

## Who judges — and what that number may be used for

PatientAgentBench needs four LLMs and only one of them is the thing being measured:

| role | what it does | selected by |
|---|---|---|
| **assistant** | the agent under test | ours, over `/api/bench/agent-turn` |
| **patient simulator** | plays the patient, turn by turn | `--judge-provider` |
| **jury** | the paper's LLM evaluators, 6 rubrics x K | `--judge-provider` |
| **sandbox** | generates the simulated EHR responses | `--judge-provider` |

| `--judge-provider` | needs | K | independent? | use it for |
|---|---|:---:|:---:|---|
| `whissle` **(default)** | `WHISSLE_API_KEY` only | 1 | **no** | internal diagnostics, regression tracking, before/after comparisons |
| `anthropic` | Bedrock creds / `ANTHROPIC_API_KEY` | 1 | yes | a number published against the paper |
| `openai` | `OPENAI_API_KEY` | 1 | yes | a number published against the paper |
| explicit `--jury A B` | that provider's creds | 2 | yes | the paper's exact K=2 configuration |

> **The independence caveat, in full.** Routing the patient simulator, jury and sandbox
> through Whissle's own model API (`POST /api/models/chat`) is what makes the benchmark
> runnable on one key. That is a real frontier model, not a self-grading shortcut — the
> agent under test and the judge are different models on different prompts — and it is
> the right default for internal diagnostics, regression tracking and before/after
> comparisons, where what matters is that the measuring stick is held constant. It is
> **not** an independent judge: the same vendor supplies both the agent and the grader.
> A number published against the paper's leaderboard is materially stronger when the
> judge is re-run on an independent provider. **Never present a Whissle-judged number
> as if it were independently graded.**

Two further honesty notes on the default route:

* **K = 1, not 2.** The paper averages two evaluators. On the Whissle route both would
  be the same endpoint — one grader sampled twice, not a jury — so the default is a
  single evaluator and every report says `K = 1 (the paper uses K=2)`. Claiming K=2
  there would fake evaluator agreement.
* **The report refuses to guess.** A run directory with no judge record renders as
  "Judge provider: unrecorded — do not publish these numbers".

`summary.json` carries `judge_provider` / `judge_independent` at the top level and the
full judge block (models, K, spend, caveat text) under `judge`; `REPORT.md` prints the
provider under the headline N and the caveat in its own section.

### How it plugs in without forking

PatientAgentBench is CC-BY-NC-4.0 and does not accept pull requests, so nothing here
patches their tree. `judge_model.install()` does two things:

1. **adds** three keys (`whissle-judge`, `whissle-patient`, `whissle-sandbox`) to their
   `MODEL_STORE` dict — no existing entry is touched;
2. **wraps** `config.create_chat_model`, documented as "the single factory for all LLM
   creation in the project". Configs whose provider is `whissle-model-api` get our
   `BaseChatModel`; every other provider falls through to their untouched code. Their
   six consumers import the factory *by name* at module import, so the wrapper is
   rebound in each already-imported module too.

Retries are not reimplemented: the model delegates to `tau2.flow.usersim.WhissleModel`,
the single owner of the retry policy for this endpoint (5xx and empty completions
retried with a long backoff; 4xx never).

### Cost

`/api/models/chat` is metered against our own wallet, and the jury is many calls per
session. Every Whissle-routed run prints and records total judge calls and USD, per run
and per case. Measured: **$0.0065 for 3 sessions** (30 calls, ~10/session).

---

## Running

```bash
# preview the sample (no network, no cost)
python -m tau2.health.patientagent.cli sample \
  --cases ../pab/data/sample_benchmark.json --limit 40 --seed 42

# small text smoke — WHISSLE_API_KEY is the only key needed
../pabvenv/bin/python -m tau2.health.patientagent.cli run \
  --cases ../pab/data/sample_benchmark.json \
  --limit 6 --seed 42 --mode harness --max-turns 15 --max-parallel 3

# the same smoke with an INDEPENDENT judge (needs that provider's credentials)
../pabvenv/bin/python -m tau2.health.patientagent.cli run \
  --cases ../pab/data/sample_benchmark.json \
  --limit 6 --seed 42 --mode harness --max-turns 15 \
  --judge-provider anthropic --max-parallel 3

# regenerate a report from an existing run directory (no cost)
python -m tau2.health.patientagent.cli report --run-dir <run-dir> --mode harness
```

### The full matrices

Generate the case set once and reuse it, so every row scores the same scenarios:

```bash
../pabvenv/bin/patient-agent-bench generate-seeds \
  --count 1200 --seed 42 --output data/patientagentbench_1200.json
```

**Full text matrix** (the publishable number is row 1). The `--jury` /
`--patient-model` / `--sandbox-model` flags below pin the paper's own K=2 apparatus and
need Bedrock + OpenAI credentials — that is the configuration a *published* number
should use. **Drop all three flags to run the same matrix on `WHISSLE_API_KEY` alone**;
the numbers are then internal-diagnostic grade, and every report will say so.

```bash
# 1. harness tools — comparable to the paper's leaderboard
../pabvenv/bin/python -m tau2.health.patientagent.cli run \
  --cases data/patientagentbench_1200.json --limit 0 --seed 42 \
  --mode harness --max-turns 15 \
  --jury claude-opus-4.8-bedrock gpt-5.5-api \
  --patient-model claude-sonnet-5-bedrock --sandbox-model claude-sonnet-5-bedrock \
  --max-parallel 8 --name pab_text_harness --label "Whissle (harness tools)"

# 2. agent tools — the product baseline, and the comparator for voice
../pabvenv/bin/python -m tau2.health.patientagent.cli run \
  --cases data/patientagentbench_1200.json --limit 0 --seed 42 \
  --mode native --max-turns 15 \
  --jury claude-opus-4.8-bedrock gpt-5.5-api \
  --patient-model claude-sonnet-5-bedrock --sandbox-model claude-sonnet-5-bedrock \
  --max-parallel 8 --name pab_text_native --label "Whissle (agent tools, text)"
```

**Voice matrix** — sample it; a live call is minutes of wall-clock, not seconds:

```bash
export PAB_VOICE_ARTIFACT_DIR=results/whissle/patientagentbench/voice_audio
export PAB_VOICE_REASR=1     # re-transcribe captured agent audio as independent evidence

../pabvenv/bin/python -m tau2.health.patientagent.cli run \
  --cases data/patientagentbench_1200.json --limit 200 --seed 42 \
  --mode voice --max-turns 15 \
  --jury claude-opus-4.8-bedrock gpt-5.5-api \
  --patient-model claude-sonnet-5-bedrock --sandbox-model claude-sonnet-5-bedrock \
  --max-parallel 4 --name pab_voice --label "Whissle (agent tools, voice)" \
  --compare-to results/whissle/patientagentbench/pab_text_native/summary.json
```

The voice row's comparator is the **text native** run on the **same seed and limit**.
To make the delta exact, re-run step 2 with `--limit 200 --seed 42` so both sides
score an identical case set.

### Cost control

`--limit N` draws a **seeded, proportionally stratified** sample (default strata
`task_type` x `severity_level`) using largest-remainder allocation, so counts sum
exactly to N and marginal distributions are preserved. Their own `--num-cases N` takes
the *first* N, which inherits generator ordering — the wrong thing for a headline
number. Every report prints the seed, the strata, N at every level, and the
achieved-vs-population distribution.

---

## Output

```
results/whissle/patientagentbench/<run>/
  REPORT.md        # paper Table-4-shaped table, per-dimension CIs, exclusions, sampling
  summary.json     # the same, machine-readable
  cases/<id>.json  # transcript, tool calls, rubric scores, classification
  voice_audio/     # duplex WAVs (voice runs)
```

Scoring reproduces `eval/aggregator.py` exactly: per-evaluator weighted aggregate
first (`sum(w*s)/8.3`), then the mean across jurors; pass is score >= 3.

Weights: clinical safety **2.0**, workflow accuracy **1.6**, triage quality **1.4**,
clinical helpfulness **1.4**, task completion **1.0**, conversational quality **0.9**.

---

## Tests

```bash
uv run pytest tests/test_patientagent_adapter.py \
              tests/test_patientagent_translation.py \
              tests/test_patientagent_scoring.py -q
```

Fully offline (mocked endpoint, mocked jury): transport + error taxonomy, tool-call
translation in both directions, parallel-tool-result coalescing, the exact aggregate
weights, infra-fail exclusion, and sampling determinism.
