# Whissle bench runbook — running the text & voice benches

Everything a teammate needs to run the Whissle agent benches: the **flow
simulator** (text + voice), the **flow-mutation sensitivity suite**, the
**half-duplex retail voice bench**, and the **whissle CLI** used alongside them.
All benches run from your laptop against the production gateway — no infra
access required, just API keys.

---

## 0. What the suites are

| Suite | What it proves | Entry point | Doc |
|---|---|---|---|
| Flow sim (text) | An agent type's conversation flow works end-to-end against an LLM user-sim: reaches its goal, closes, no state-machine bugs | `python -m tau2.flow.simulate run` | `WHISSLE_FLOW_SIM.md` |
| Flow sim (voice) | Same, but over the REAL voice pipeline (LiveKit room, STT→flow→TTS, recorded audio) | same + `--mode voice` | `WHISSLE_FLOW_SIM.md` |
| Flow mutation | The studio edit→publish→runtime chain: mutate each flow step via the API, publish, prove the live conversation picks it up (drafts must stay inert) | `python -m tau2.flow.mutation_suite run` | `WHISSLE_FLOW_MUTATION.md` |
| Half-duplex retail voice | A real whissle.ai agent driven by an ElevenLabs-voiced GPT user-sim through the τ²-bench retail tasks | `./run_hd.sh` | `WHISSLE_HALF_DUPLEX.md` |
| Transcription / diarization | WER + DER of `/api/models/transcribe` | see doc | `WHISSLE_TRANSCRIBE.md` |
| Agentic (τ² retail via API) | Tool-use benchmark through `/api/bench` | see doc | `WHISSLE_VOICE.md` / repo README |

Results land under `results/whissle/…` (per-session JSON + flow trace + WAVs in
voice mode + per-type `SUMMARY.md`). `scripts/build_bench_report_md.py`
aggregates everything into one report.

---

## 1. Prerequisites

- **Machine:** macOS or Linux. Python **3.11+**, [`uv`](https://docs.astral.sh/uv/)
  (`curl -LsSf https://astral.sh/uv/install.sh | sh`), Node **20+** (for the CLI), git.
- **Whissle workspace + key:** log in at **https://platform.whissle.ai** →
  *Settings → API keys* → create a **workspace secret key** (`wsk_…`). The key
  needs `agents:write` (default for workspace secret keys).
- **Wallet credit:** benches bill the workspace like real usage (voice
  per-minute + model calls). Check *Settings → Billing* first — a drained
  wallet makes the sim's LLM calls fail with **HTTP 402** and sessions record
  as `infra_fail`. A full 6-type voice sweep costs roughly $50–100; text runs
  are pennies.
- **Third-party keys** (voice suites only):
  - `OPENAI_API_KEY` — the half-duplex user-sim (gpt-4o).
  - `ELEVENLABS_API_KEY` — the user-sim's voice.
  - (`ANTHROPIC_API_KEY`, `DEEPGRAM_API_KEY` only for specific configs — see `.env.example`.)

---

## 2. Setup — tau2-bench-w

```bash
git clone git@github.com:WhissleAI/tau2-bench-w.git
cd tau2-bench-w
uv sync --extra voice        # the voice extra is REQUIRED for any --mode voice run
                             # (plain `uv sync` → "livekit.rtc is required" mid-run)
cp .env.example .env         # then edit:
#   WHISSLE_API_KEY=wsk_...                                   (required, all suites)
#   WHISSLE_BASE=https://aws-gateway-backend.whissle.ai/bot   (default; leave as is)
#   WHISSLE_AGENT_ID=<agent uuid>                             (half-duplex suite only)
#   OPENAI_API_KEY / ELEVENLABS_API_KEY                       (voice suites)
```

Sanity check: `uv run python -c "import livekit.rtc; print('voice ok')"`.

---

## 3. Flow simulator (the main bench)

The sim creates a **throwaway agent** of the given type (named `flowsim-…`),
drives an LLM user-sim (persona + goal) through a full conversation, pulls the
flow step-trace, judges task success, runs the rule analyzer, deletes the
agent, and writes per-session JSON + a summary.

```bash
# list available task fixtures (6 agent types, ~65 scenarios)
uv run python -m tau2.flow.simulate list

# TEXT mode (default; fast, cheap — full matrix material)
uv run python -m tau2.flow.simulate run --agent-type dental_receptionist --sessions 11

# VOICE mode (real LiveKit call; slow — minutes per session; records WAVs)
uv run python -m tau2.flow.simulate run --agent-type headache_enrollment --sessions 10 --mode voice

# a single scenario
uv run python -m tau2.flow.simulate run --agent-type headache_enrollment --task-id hx_red_flag_urgent --mode voice
```

Gotchas:
- `--sessions` **defaults to 2** — pass the full count (fixtures have 10–11 per type).
- Run types **sequentially** (or ≤3 concurrent runs total) — each voice session
  is a real prod call.
- Turn budgets/post-goal allowances come from `data/flow/sim_tasks.json`
  (per-task > per-type > global). `--max-turns` overrides for a run.
- Findings taxonomy: `agent_no_close` (agent's fault — goal met, cooperative
  caller, no close), `turn_cap_exceeded` (flow too long), `premature_termination`
  (flow ended, goal unmet), `stuck_termination` (residual), `infra_fail`
  (never executed / transcript-dead — auto-retried once, excluded from flow metrics).
- Voice sessions save `bot.wav`/`caller.wav`/`mix.wav` + a re-ASR of the bot
  track in `metadata.audio.bot_reasr` — **trust the audio, not just the
  transcript**, when a session looks "silent".

Aggregate report:

```bash
uv run python scripts/build_bench_report_md.py --results results/whissle/flow_sim --out REPORT.md --title "Flow bench"
```

---

## 4. Flow-mutation sensitivity suite

Proves user edits actually reach the runtime: for each step of a type's flow it
creates a throwaway agent, applies one mutation **via the same API the studio
uses** (validate → PATCH draft → assert draft inertness → publish → assert
live), then runs a 2–6-turn probe asserting the change manifests (sentinel say
text, new question, rerouted transition, tool gated in/out, skipped state).

```bash
uv run python -m tau2.flow.mutation_suite plan --agent-type headache_enrollment   # show the matrix
uv run python -m tau2.flow.mutation_suite run  --agent-type headache_enrollment              # text mode (full matrix)
uv run python -m tau2.flow.mutation_suite run  --agent-type headache_enrollment --mode voice --voice-spot-checks
```

Any FAIL here = a product bug in the edit→publish→runtime chain. Report lands
in `results/whissle/flow_mutation/<type>/REPORT.md`; non-zero exit on failure.

---

## 5. Half-duplex retail voice bench

Runs τ²-bench retail tasks against **your own** platform agent over voice
(tools delegated to the bench over the data channel). Needs
`WHISSLE_AGENT_ID`, `OPENAI_API_KEY`, `ELEVENLABS_API_KEY` in `.env`.

```bash
./run_hd.sh                  # env-guarded; preflights livekit.rtc before spending a run
```

Walkthrough incl. agent creation: `WHISSLE_HALF_DUPLEX.md`.

---

## 6. whissle CLI (agent management alongside the benches)

```bash
git clone git@github.com:WhissleAI/whissle-cli.git
cd whissle-cli && npm install
node bin/whissle.mjs login        # paste your wsk_ key   (or: npm link → `whissle`)
whissle whoami                    # confirms workspace + role
```

Most useful during bench work:

```bash
whissle agents list / get <id> / delete <id>
whissle agents types                                  # valid --agent-type keys
whissle agents flow show <id>                         # the live state machine
whissle agents flow set <id> --file flow.json --draft # stage an edit
whissle agents flow publish <id>                      # promote draft → live
whissle agents flow trace <id> --conversation <cid>   # step trace for one run
whissle chat <agent-id> -m "hi"                       # quick text probe
whissle models tts "hello" --language hi              # model endpoints à la carte
```

---

## 7. Hygiene & troubleshooting

- **Leftover bench agents:** suites delete their `flowsim-*` agents; if a run is
  killed, sweep stragglers:
  `curl -X DELETE "$WHISSLE_BASE/api/agents/<id>?confirm=true" -H "Authorization: Bearer $WHISSLE_API_KEY"`
  (the `confirm=true` is needed when the agent has knowledge docs) or
  `whissle agents delete <id>`.
- **HTTP 402 anywhere** → workspace wallet is empty; top up in Settings → Billing.
- **`livekit.rtc is required`** → you ran `uv sync` without `--extra voice`.
- **Session records `infra_fail` / "not run"** → infra, not the flow: check
  wallet, then retry; these are excluded from flow metrics by design.
- **Results are git-tracked** — commit run artifacts on a branch and PR them
  (`results: <what you ran>`), so baselines stay comparable.
- Don't run big voice sweeps during live customer demos — the bench shares the
  production fleet.
