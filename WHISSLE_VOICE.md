# Benchmarking Whissle's VOICE pipeline on τ³-bench

This fork can benchmark Whissle's **real voice pipeline** — STT → LLM brain → TTS,
plus the hesitation/emotion "glass-box" signals — end-to-end on the same tau2
tasks as the text run, and report the **text → voice success delta** and **real
latency**.

Whissle is registered as an **audio-native provider** (`whissle`), alongside
`openai`, `gemini`, and `livekit`. So it plugs into the same `FullDuplexOrchestrator`
and voice user simulator, and its number is directly comparable to OpenAI Realtime /
Gemini Live on the leaderboard.

## How it works

```
tau2 FullDuplexOrchestrator
  │  (tick: user audio in ─ agent audio out)
  ▼
DiscreteTimeAudioNativeAgent  provider="whissle"
  ▼
WhissleAdapter (src/tau2/voice/audio_native/whissle/)
  │  POST /api/bench/voice/start  → LiveKit {url, token, room}
  ▼
WhissleRoomProvider  ── joins the room as the USER participant
  ├─ publishes user-sim audio  → Whissle STT hears it
  ├─ subscribes to bot audio    → Whissle TTS (what the user "hears")
  ├─ data channel  bench-tool-call  ← Whissle delegates each tool
  │                bench-tool-result →  we run env.step(), reply
  └─ data channel  bench-agent-text  ← Whissle's own transcript (no re-ASR)
```

Whissle runs **no tools itself** in bench mode — its voice pipeline emits each tool
call over the LiveKit data channel; the benchmark executes it against the tau2
environment (so tau2 keeps the tools, task DB, and scoring authoritative) and
returns the result. This is the voice analog of the text path's
`POST /api/bench/agent-turn`.

The agent transcript is Whissle's **own** LLM text (surfaced as `bench-agent-text`),
not a re-transcription of our TTS — faithful, and fair vs. providers that return
their own text.

## Prereqs

1. A configured agent in your Whissle org and a `wsk_` API key for it. The agent's
   own prompt is **overridden** with the domain policy at session start (the real
   overlays — KB, company brain, guardrails — still layer on top), so a plain
   general/text agent works.
2. The backend must expose `/api/bench/voice/start` with `LIVEKIT_ENABLED=1` and
   LiveKit configured (this is live on `whissle-gw`; the AWS backend needs the
   bench-voice PR deployed + LiveKit enabled).
3. `uv sync` (installs `livekit-agents`, which brings `livekit.rtc`).

```bash
export WHISSLE_BASE=https://aws-gateway-backend.whissle.ai/bot   # or the gw host
export WHISSLE_AGENT_ID=<agent uuid in your org>
export WHISSLE_API_KEY=<wsk_ key>
export OPENAI_API_KEY=<for the gpt-4o user simulator + its TTS/ASR>
```

## Run

```bash
# One task first (smoke test the room join + tool bridge):
uv run tau2 run \
  --domain retail --audio-native --audio-native-provider whissle \
  --user-model gpt-4o --num-tasks 1 --max-concurrency 1 \
  --save-to results/whissle/voice_retail_smoke.json

# Full retail voice run (low concurrency — real rooms + provider rate limits):
uv run tau2 run \
  --domain retail --audio-native --audio-native-provider whissle \
  --user-model gpt-4o --max-concurrency 2 \
  --save-to results/whissle/voice_retail_run1.json
```

Compare `Pass^1` against the text run (`results/whissle/retail_run1.json`) for the
text → voice delta. Latency: the backend's `response-latency Xms` probe
(`RESPONSE_LATENCY_LOG=1`) measures user-stopped-speaking → first TTS audio per
turn — the real spoken-turn latency.

## Notes / honesty

- **Concurrency**: keep it low (1–2). Each task holds a live LiveKit room + a real
  voice pipeline; higher concurrency also hits LLM-provider rate limits that
  pollute the score (a harness artifact, not a product gap) — same caveat as the
  text runs.
- **Conservative transcript**: scoring is primarily DB-state (tool writes), which
  flows structurally over the data channel and is transcript-independent. The
  agent transcript (`bench-agent-text`) drives the user simulator + communicate-info
  checks.
- **Not yet**: barge-in fidelity and per-tick proportional transcript are not
  modeled (Whissle emits one final text per turn); this is fine for scoring but
  means the tick trajectory's transcript timing is coarse.

## Files

- `src/tau2/voice/audio_native/whissle/config.py` — env-driven connection config.
- `src/tau2/voice/audio_native/whissle/provider.py` — LiveKit room client (audio +
  data-channel tool/transcript bridge).
- `src/tau2/voice/audio_native/whissle/discrete_time_adapter.py` — tick adapter.
- Wired into `create_adapter()`, `config.py` provider dicts, `cli.py` choices, and
  `DiscreteTimeAudioNativeAgent`'s provider list.
