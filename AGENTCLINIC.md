# AgentClinic — Whissle as the doctor

[AgentClinic](https://github.com/SamuelSchmidgall/AgentClinic) (Schmidgall et al.,
[arXiv:2405.07960](https://arxiv.org/abs/2405.07960); npj Digital Medicine, 2026) is a
simulated-clinic benchmark. A **doctor** agent interviews a **patient** agent under
incomplete information, may order tests from a **measurement** agent, and must commit
to a diagnosis, which a **moderator** agent grades against the case's ground truth.

This adapter plugs a real Whissle platform agent in as the doctor — over text
(`POST /api/bench/agent-turn`) or over the **real voice pipeline** — and leaves the
benchmark's own agents and prompts byte-for-byte untouched.

```bash
python -m tau2.health.agentclinic.run --dataset MedQA --limit 5
```

---

## What the benchmark actually requires

Read `agentclinic.py` upstream and the contract is smaller and stranger than the paper
suggests:

* **The doctor has no tools.** Every action is a marker inside free text:
  `REQUEST TEST: [test]`, `REQUEST IMAGES`, `DIAGNOSIS READY: [diagnosis]`. Upstream
  detects them with a plain **case-sensitive substring test**.
* **One episode = up to `--total-inferences` (default 20) doctor turns.** A test
  request is answered by the measurement agent; anything else goes to the patient. The
  last turn is prefixed with "This is the final question. Please provide a diagnosis."
* **Scoring is one line**: `accuracy = total_correct / total_presents`, where
  `total_presents` counts **every** scenario attempted. A doctor that never commits
  scores exactly as badly as one that names the wrong disease. The moderator's reply
  must lowercase-equal `"yes"` — `"Yes."` scores as wrong.
* **Case counts** (verified in the upstream tree, not the paper's prose):

  | dataset | file | cases |
  |---|---|---|
  | `MedQA` | `agentclinic_medqa.jsonl` | 107 |
  | `MedQA_Ext` | `agentclinic_medqa_extended.jsonl` | **214** ← the paper's "215 language agents" |
  | `NEJM` | `agentclinic_nejm.jsonl` | 15 |
  | `NEJM_Ext` | `agentclinic_nejm_extended.jsonl` | **120** ← the paper's "120 multimodal agents" |
  | `MIMICIV` | `agentclinic_mimiciv.jsonl` | referenced by upstream's loader but **not distributed** (credentialed source) |

  Cases are fetched from upstream on first use into `data/agentclinic/`; nothing is
  vendored here.
* **Biases** (12 doctor, 11 patient) are part of the benchmark and are ported verbatim
  (`--doctor-bias`, `--patient-bias`).

Known property of the benchmark, not of us: the measurement agent is given the case's
**entire** `Test_Results` block and will happily volunteer findings beyond the test
that was ordered. Once a doctor orders anything, a lot of the answer can come back.
This inflates accuracy for every model equally, so it stays as-is.

## Design

```
     patient agent            measurement agent          moderator agent
   (upstream prompt)          (upstream prompt)         (upstream prompt)
          │                          │                         │
          └────────── runner.run_case (upstream's loop) ───────┘
                                 │
                    DoctorTransport (one interface)
                    ├── WhissleDoctor  →  POST /api/bench/agent-turn
                    └── VoiceDoctor    →  LiveKit room, real STT→agent→TTS
```

| module | job |
|---|---|
| `dataset.py` | upstream's five case sets, normalized; `--limit/--sample/--seed` |
| `protocol.py` | doctor prompt (verbatim), marker ⇄ tool-call translation, refusal patterns |
| `agents.py` | patient / measurement / moderator, upstream prompts, pluggable backend |
| `doctor.py` | the adapter: agent-turn transport, auth + retry, history modes, image caps |
| `voice.py` | the same episode over real speech, via `tau2.flow.voice_transport` |
| `vision.py` | case image → base64 blocks / `analyze_image` tool |
| `runner.py` | upstream's episode loop + full recording |
| `scoring.py` | upstream's metric + the accounting that explains it |
| `run.py` | CLI, artifacts, summary |

Auth, retry (3 attempts, backoff on 5xx, no retry on 4xx) and error shapes are lifted
from `tau2/agent/whissle_agent.py`.

### Knobs that change what is being measured

| flag | default | meaning |
|---|---|---|
| `--protocol markers\|tools` | `markers` (text), `tools` (voice) | markers = upstream's exact contract; tools = the same three actions as real function calls |
| `--history native\|agentclinic` | `native` | multi-turn `messages` (how the product is really driven) vs upstream's stateless rolling-string prompt |
| `--prompt-mode override\|agent` | `override` | `override` sends AgentClinic's doctor prompt as `system` (comparable to the paper). **`agent` sends no system prompt at all**, so the agent's own shipped prompt and guardrails run — the only arm where a "we don't diagnose" boundary can show up |
| `--vision off\|block\|tool\|both` | `off` | how the case image reaches the agent |
| `--agent-id` / `--agent-type` | env | which agent plays the doctor; `--agent-type` creates and tears down a throwaway agent of a seeded type |

## Scores it reports

Never one number:

* **`accuracy`** — upstream's formula, unmodified. **This is the number to quote.**
* **`declined_rate`** — cases where the agent refused to commit (product boundary),
  split into `declined_by_pattern` (deterministic phrasing) and `declined_by_judge`
  (an LLM classifier that catches role-scope deferrals like "that's for your doctor").
* **`spurious_commits`** — refusals that upstream's substring rule logged as
  commitments because the agent *quoted* the marker while refusing.
* **`accuracy_when_committed`** — correct / actually-committed, i.e. refusals removed
  rather than scored as wrong diagnoses.
* **`accuracy_lenient_moderator`** and **`cases_with_format_deviation`** — how much of
  any gap is grader/format strictness rather than medicine.

Outcomes per case are `correct | incorrect | declined | no_commit | infra_fail`, and
**`infra_fail` is excluded from every denominator** — same rule and same
`tau2.flow.analyze` taxonomy the flow suite uses (`errors.py`).

Artifacts land in `results/whissle/agentclinic/<ts>-<tag>/`:
`RUN.json` (provenance incl. the selected case ids), `SUMMARY.json`, `SUMMARY.md`,
`cases/<id>.json` (full turn records, tests, refusals, latency), `transcripts/<id>.txt`,
and `audio/` in voice mode.

## Running it

```bash
# text, full MedQA (107 cases)
python -m tau2.health.agentclinic.run --dataset MedQA --concurrency 6

# the paper's main language set (214 cases)
python -m tau2.health.agentclinic.run --dataset MedQA_Ext --concurrency 6

# the product-boundary arm: the agent's OWN prompt, no override
python -m tau2.health.agentclinic.run --dataset MedQA --limit 25 \
    --agent-id <health-agent-uuid> --prompt-mode agent --tag boundary

# a purpose-built agent type (creates + deletes a throwaway agent)
python -m tau2.health.agentclinic.run --dataset MedQA --limit 25 \
    --agent-type clinical_intake_triage --tag cit

# multimodal (120 NEJM cases, images as base64 blocks)
python -m tau2.health.agentclinic.run --dataset NEJM_Ext --vision block --concurrency 4
# withhold the image until the doctor asks for it (upstream's --doctor_image_request)
python -m tau2.health.agentclinic.run --dataset NEJM_Ext --vision both \
    --doctor-image-request --protocol tools

# VOICE: the same interview over real speech (slow, serial, costs voice minutes)
python -m tau2.health.agentclinic.run --dataset MedQA --limit 10 --mode voice --audio
```

Env: `WHISSLE_BASE`, `WHISSLE_API_KEY`, and either `WHISSLE_AGENT_ID` or `--agent-id` /
`--agent-type`. **No OpenAI, Anthropic or AWS key is required** — see the judge section
below. Tests: `pytest tests/test_agentclinic.py tests/test_health_model_router.py`
(fully offline).

## Who judges — and what that number may be used for

AgentClinic is not just the doctor. Three of its four agents belong to the *benchmark*:
the **patient**, the **measurement reader** and the **moderator** that decides whether
the doctor's diagnosis matches the ground truth (plus our own decline classifier).
Upstream calls OpenAI/Anthropic/Replicate for those. Here they are selected by
`--judge-provider`:

| `--judge-provider` | needs | independent? | use it for |
|---|---|:---:|---|
| `whissle` **(default)** | `WHISSLE_API_KEY` only | **no** | internal diagnostics, regression tracking, before/after comparisons |
| `openai` | `OPENAI_API_KEY` | yes | a number published against the paper |
| `anthropic` | `ANTHROPIC_API_KEY` | yes | a number published against the paper |

`--judge-model` overrides the model; `--support-llm litellm:<model>` remains as a raw
escape hatch for reproducing a specific published configuration.

> **The independence caveat, in full.** Routing the benchmark's own agents through
> Whissle's model API (`POST /api/models/chat`) is what makes the whole matrix runnable
> on one key. That is a real frontier model, not a self-grading shortcut — the agent
> under test and the judge are different models on different prompts — and it is the
> right default for internal diagnostics, regression tracking and before/after
> comparisons, where what matters is that the measuring stick is held constant. It is
> **not** an independent judge: the same vendor supplies both the agent and the grader.
> A number published against the paper's leaderboard is materially stronger when the
> judge is re-run on an independent provider. **Never present a Whissle-judged number
> as if it were independently graded.**

Every artifact records `judge_provider`, `judge_model` and `judge_independent`, and
`SUMMARY.md` prints the applicable caveat, so a number can never travel without it.

### The moderator must answer exactly `yes`

Upstream's grading rule is a literal test against the string `yes`. Upstream could rely
on that because it pinned one model to one prompt; the moment the moderator is routed
through a different backend, a reply of `"Yes."` scores a **correct** diagnosis wrong
and the benchmark starts measuring the grader's punctuation.

So the moderator's decode is constrained (`agents.moderate`): the system and user
prompts stay byte-for-byte upstream, a decorated reply canonicalizes to the bare token,
and a genuinely non-conforming reply is retried with a one-word instruction appended to
a *follow-up user message only*. Anything that still never conforms falls back to
upstream's strict rule and is flagged rather than guessed. Each summary reports
`moderator_retried`, `moderator_normalized` and `moderator_unconstrained`, so grader
formatting can never move a number invisibly.

### Cost

Judge calls dominate a run (~7 per case at 12 inferences), and on the default route
they are metered against our own wallet. Every summary prints total judge calls and
USD, per run and per case. Measured: **$0.0017 for 5 MedQA cases** (35 calls).

## Voice mode, honestly

Reuses `tau2/flow/voice_transport.py` (speech-energy end-of-turn detection, data-channel
guards, per-turn latency). The patient's lines are synthesized with Whissle's own TTS
and published into the LiveKit room; the doctor is the real voice pipeline; the doctor's
own transcript is what gets scored, by the identical scorer.

Differences from text mode — a voice number is not a text number:

1. Protocol is `tools` (nobody says "REQUEST TEST colon Chest underscore X dash Ray").
2. The system prompt is fixed at session start, so the live question budget is rendered
   once at 0.
3. The patient opens with "Hello?" so the doctor still takes the first substantive turn.
4. ASR/TTS error is real and is part of what is measured.
5. No image channel — `--mode voice` requires `--vision off`.

Sessions that die on transport (dead data channel, provider outage, credit exhaustion)
raise `VoiceInfraError`, are classified `infra_fail`, and are excluded.

**Status: unit-tested against a fake room provider, not yet run live.** The voice matrix
was deliberately not run as part of the adapter PR.

## Comparability caveats

* The benchmark's own agents run on **Whissle's à-la-carte chat model**
  (`/api/models/chat`) by default, not the paper's GPT-4/Claude — so the judge is *not*
  independent of the agent's vendor. Use `--judge-provider openai|anthropic` (or
  `--support-llm litellm:<model>` for a specific published configuration) before
  publishing. The backend that ran is recorded in every artifact. See
  "Who judges" above.
* `--history native` and `--protocol tools` are *not* upstream's prompting. For the
  tightest comparison use `--history agentclinic --protocol markers --prompt-mode
  override`.
* Sampling defaults to upstream's first-N (`--sample head`), so a limited run here is
  the same subset a limited run upstream would grade.
