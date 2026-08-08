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
ANTHROPIC_API_KEY=sk-ant-...             # patient simulator + jury
```

The patient simulator, sandbox and jury must NOT be Whissle models — they are the
measurement apparatus. Use the paper's models (Claude Sonnet 5 / Claude Opus 4.8),
via Bedrock (`-bedrock` keys) or the Anthropic API (`-api` keys).

---

## Running

```bash
# preview the sample (no network, no cost)
python -m tau2.health.patientagent.cli sample \
  --cases ../pab/data/sample_benchmark.json --limit 40 --seed 42

# small text smoke
../pabvenv/bin/python -m tau2.health.patientagent.cli run \
  --cases ../pab/data/sample_benchmark.json \
  --limit 6 --seed 42 --mode harness --max-turns 15 \
  --jury claude-opus-4.8-api \
  --patient-model claude-sonnet-5-api --sandbox-model claude-sonnet-5-api \
  --max-parallel 3

# regenerate a report from an existing run directory (no cost)
python -m tau2.health.patientagent.cli report --run-dir <run-dir> --mode harness
```

### The full matrices

Generate the case set once and reuse it, so every row scores the same scenarios:

```bash
../pabvenv/bin/patient-agent-bench generate-seeds \
  --count 1200 --seed 42 --output data/patientagentbench_1200.json
```

**Full text matrix** (the publishable number is row 1):

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
