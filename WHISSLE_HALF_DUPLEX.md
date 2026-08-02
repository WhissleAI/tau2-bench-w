# Testing YOUR Whissle agent (half-duplex) on τ²-bench

This is the guide for taking an agent you build at **whissle.ai** and running it
through this benchmark — end to end, through Whissle's real voice pipeline
(STT → LLM + tools → TTS), scored against the tau2 task suite.

It covers the **half-duplex** path (`--agent whissle_voice`). Half-duplex —
request/response, one turn at a time — is the faithful model of Whissle's cascade.
Use it for agent-quality and task-success numbers. (The tick-based *full-duplex*
provider is a separate, more experimental path documented in
[`WHISSLE_VOICE.md`](WHISSLE_VOICE.md); it fragments the cascade's turn structure
and is not the one to reach for when you want a trustworthy Pass^1.)

```
tau2 Orchestrator (turn-based)
  │  user-simulator text (gpt-4o)
  ▼
whissle_voice agent  ──ElevenLabs TTS──▶ user utterance as AUDIO
  │  POST {WHISSLE_BASE}/api/bench/voice/start  → LiveKit {url, token, room}
  ▼
WhissleRoomProvider ── joins the room as the USER
  ├─ publishes the user audio      → your agent's STT hears it
  ├─ subscribes to the bot audio   → your agent's TTS (transcript returned)
  └─ data channel: your agent delegates each tool call → tau2 runs it against
     the task environment and feeds the result back, so tau2 keeps the tools,
     task DB, and scoring authoritative.
```

Your agent runs **no tools of its own** in bench mode — it emits each tool call
over the data channel and the benchmark executes it. Its own LLM transcript (not a
re-transcription of the TTS) is what the user simulator and scoring see.

---

## Part 1 — Create the agent at whissle.ai

You need two things from the platform: an **agent id** and a **`wsk_` API key**.

1. **Sign in** to the platform: **https://platform.whissle.ai**.

2. **Create an agent.** Go to **Agents → New agent**. Any type works — a plain
   *General* or *Text* agent is fine. At session start the benchmark **overrides
   the agent's prompt with the tau2 domain policy** (retail / airline / telecom),
   and your org's real overlays (knowledge base, company brain, guardrails, active
   window) still layer on top. So you are testing Whissle's *runtime* given the
   domain policy — you do **not** hand-craft a retail agent. Name it something like
   `bench` so it's easy to find.

3. **Get the agent id** (a UUID). It's in the agent's URL and its settings panel on
   the platform. Or fetch it with the API once you have a key (step 4):

   ```bash
   curl -s -H "Authorization: Bearer wsk_XXXX" \
     https://aws-gateway-backend.whissle.ai/bot/api/agents \
     | python3 -m json.tool   # look for the {"id": ...} of your agent
   ```

4. **Create a `wsk_` API key.** Go to **Settings → API Keys → Create key**. Copy
   the **`wsk_…` secret — it is shown once**. This key is scoped to your
   organization and inherits your role; the benchmark endpoints accept it as a
   bearer token. (If you lose it, an owner/admin can reveal it again from the same
   page, or just create a new one.)

> Keep the `wsk_` key secret — it can act on your whole org. It goes only in the
> harness's local `.env` (git-ignored), never in a command line or a commit.

---

## Part 2 — Configure the harness

Copy `.env.example` to `.env` and fill in:

```bash
# Your Whissle agent + org key (Part 1)
WHISSLE_BASE=https://aws-gateway-backend.whissle.ai/bot   # AWS backend (prod)
WHISSLE_AGENT_ID=<the agent UUID from Part 1>
WHISSLE_API_KEY=<the wsk_ key from Part 1>

# The user simulator (drives the conversation) + its voice
OPENAI_API_KEY=<for the gpt-4o user simulator>
ELEVENLABS_API_KEY=<user-simulator TTS — the "caller" voice>
# WHISSLE_USER_VOICE_ID=<an ElevenLabs voice id>   # optional; defaults to "Sarah"
```

Install deps (once):

```bash
uv sync      # pulls livekit-agents / livekit.rtc, needed for the room client
```

---

## Part 3 — Run the test

The convenience script (reads `.env`, never echoes secrets):

```bash
./run_hd.sh <domain> <num-tasks> <concurrency> <max-steps>

# Smoke test — one retail task, verify the room join + tool bridge end to end:
./run_hd.sh retail 1 1

# Full retail run:
./run_hd.sh retail all 2
```

Or the raw command it wraps:

```bash
uv run tau2 run \
  --domain retail --agent whissle_voice \
  --user user_simulator --user-llm gpt-4o \
  --max-concurrency 2 --max-steps 40 \
  --save-to results/whissle/hd_retail_run.json
```

Domains: `retail`, `airline`, `telecom` (the standard tau2 suites).

---

## Part 4 — Read the result

- The run prints **`Pass^1`** (fraction of tasks fully solved) and writes the full
  trajectories to `results/whissle/hd_<domain>_*.json`.
- Scoring is primarily **DB-state** (the tool writes your agent made against the
  task environment) plus **communicate-info** checks against your agent's
  transcript — so a correct outcome scores whether or not the ASR was perfect.
- Compare against the **text** baseline (`--agent whissle`, `POST /api/bench/agent-turn`,
  no audio) to read the **text → voice delta** — how much the STT/TTS round trip
  costs your agent versus its pure-brain ceiling.

---

## Prerequisites on the backend

The `WHISSLE_BASE` you point at must have:

- `/api/bench/*` deployed, and
- `LIVEKIT_ENABLED=1` with LiveKit configured (the half-duplex path joins a real
  LiveKit room).

Both are live on the AWS backend (`aws-gateway-backend.whissle.ai/bot`) and on
`whissle-gw`. If you self-host, enable them there.

## Notes & honesty

- **Concurrency low (1–2).** Each task holds a live LiveKit room + a real voice
  pipeline; pushing concurrency also trips LLM-provider rate limits that pollute
  the score (a harness artifact, not a product gap).
- **Half-duplex vs full-duplex.** This path is turn-based on purpose — it lets the
  cascade finish its tool calls. Barge-in fidelity and per-tick proportional
  transcript are only modeled in the full-duplex provider ([`WHISSLE_VOICE.md`](WHISSLE_VOICE.md)),
  which is fine for latency experiments but not the number you report for task
  success.
- **The domain override is the point.** You don't tune the agent per domain; the
  benchmark supplies the policy and measures how Whissle's real assembly (KB,
  brain, guardrails) performs on it.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `401 Invalid or revoked API key` | `WHISSLE_API_KEY` wrong/rotated — recreate the `wsk_` key (Part 1.4). |
| `404 Agent not found in this org` | `WHISSLE_AGENT_ID` isn't an agent in the key's org — re-fetch with `GET /api/agents`. |
| Hangs at "waiting for room" / no audio | Backend missing `LIVEKIT_ENABLED=1`, or `ELEVENLABS_API_KEY` unset (no caller audio to publish). |
| `resume? (y/n)` EOF crash | A checkpoint from a prior run — `run_hd.sh` removes it; if running raw, delete `data/simulations/<save-to>` first. |
