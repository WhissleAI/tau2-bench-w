# How audio moves: OpenAI → Whissle (Deepgram) → Tau

Who owns which half of the call, and where each provider actually sits. This replaces
the older ElevenLabs-based description of the simulated customer's voice.

## The one-line version

Tau synthesises the **customer**. Whissle synthesises and transcribes the **agent**.
Tau never runs speech recognition.

```
  ┌─ Tau (this repo) ─────────────────┐        ┌─ Whissle (server) ───────────────┐
  │                                   │        │                                  │
  │  user_simulator (OpenAI LLM)      │        │   Deepgram STT                   │
  │        │ text                     │        │        │ text                    │
  │        ▼                          │        │        ▼                         │
  │  user_tts.py — OpenAI TTS         │        │   Agent Flow + LLM + tools       │
  │        │ PCM16 24 kHz             │        │        │                         │
  │        ▼ resample 24k→16k         │        │        ▼                         │
  │  PCM16 mono 16 kHz  ──────────────┼────────┼──▶ (agent hears the customer)    │
  │                                   │LiveKit │        │                         │
  │  WhissleRoomProvider              │  room  │   Deepgram TTS (aura-asteria-en) │
  │    ◀──────────────────────────────┼────────┼── agent audio (captured to WAV)  │
  │    ◀── bot-transcription ─────────┼─data───┼── agent's own transcript         │
  │    ◀── bench-tool-call ───────────┼channel─┼── tool the agent wants to run    │
  │    ─── bench-tool-result ─────────┼────────┼─▶                                │
  │                                   │        │                                  │
  │  scoring: DB state, tool trace,   │        │                                  │
  │           safety rules, transcript│        │                                  │
  └───────────────────────────────────┘        └──────────────────────────────────┘
```

## Component by component

| Stage | Who runs it | Where it is configured |
|---|---|---|
| Simulated customer's words | OpenAI LLM (`--user-llm`) | tau2 `user_simulator` |
| Simulated customer's voice | **OpenAI TTS** | `src/tau2/agent/user_tts.py` |
| Customer audio → agent | LiveKit room, published as a track | `voice/audio_native/whissle/provider.py` |
| **Agent's speech recognition** | **Deepgram, server-side in Whissle** | the agent's `stt_provider` field |
| Agent's decisions | Whissle Agent Flow + LLM + tools | the agent's `flow` |
| Agent's voice | **Deepgram TTS**, `aura-asteria-en` | the agent's `tts_provider` / `voice` |
| Agent's transcript back to Tau | LiveKit data channel (`bot-transcription`) | provider `_on_data` |
| Tool execution | **Tau**, against the task environment | `bench-tool-call` / `bench-tool-result` |
| Scoring | Tau | `evaluator/` |

## Why Tau does not run Deepgram

The benchmark needs the agent's words as text. There are two ways to get them, and
only one is correct here:

1. **Re-transcribe the agent's audio in Tau** (a second Deepgram pass). This measures
   *Tau's* ASR, not Whissle's, and injects errors the agent never made.
2. **Read the agent's own transcript off the LiveKit data channel.** Whissle already
   emits `bot-transcription` — the text its own pipeline produced.

Tau does (2). So **no client-side `DEEPGRAM_API_KEY` is required**, and adding one
would not improve fidelity — it would degrade it, by scoring a re-transcription
instead of what the agent actually said.

Deepgram is genuinely in the loop; it just runs **inside Whissle**, on the agent's
side, configured on the agent record (`stt_provider: deepgram`). Verify it with:

```bash
whissle agents get <agent-id> --json | python3 -c \
  'import json,sys; a=json.load(sys.stdin); print(a["stt_provider"], a["tts_provider"], a["voice"])'
```

A client-side Deepgram credential is only needed if you deliberately want an
independent transcription cross-check — a separate experiment, not this path.

## The sample-rate trap

The publisher requires **PCM16, mono, 16 kHz**. The two TTS providers disagree:

| Provider | `response_format` | Native rate | Handling |
|---|---|---|---|
| OpenAI | `pcm` | **24 kHz** | resampled 24k→16k in `user_tts.resample_pcm16` |
| ElevenLabs | `pcm_16000` | 16 kHz | passed through |

Publishing 24 kHz samples into a 16 kHz track raises **no error**. The audio simply
plays about 1.5× fast, and Deepgram mistranscribes it. The symptom looks like poor
ASR, which is why the conversion lives in one place with a test asserting that one
second in stays one second out.

## Configuration

```bash
# Simulated customer — OpenAI is the default; nothing else need be set.
OPENAI_API_KEY=<private>
# Optional:
WHISSLE_USER_TTS_PROVIDER=openai      # or `elevenlabs` to opt back in
OPENAI_TTS_MODEL=gpt-4o-mini-tts
OPENAI_TTS_VOICE=alloy
WHISSLE_USER_TTS_TIMEOUT_S=60

# The agent under test.
WHISSLE_BASE=... WHISSLE_AGENT_ID=... WHISSLE_API_KEY=<private>
```

`ELEVENLABS_API_KEY` is **no longer required**. It is read only when
`WHISSLE_USER_TTS_PROVIDER=elevenlabs`. Names only are listed here; never values.

## Error handling and secrets

Provider failures raise `TTSError` carrying the **response body**, which the previous
implementation discarded via `raise_for_status()`. That mattered: a real run lost ten
voice tasks to an ElevenLabs `400` whose body said `API key must start with 'sk_'` —
information that never reached a log.

Bodies are truncated to 500 characters and passed through `redact()`, which strips
anything credential-shaped (`sk-…`, `sk_…`, `wsk_…`, `Bearer …`, bare 64-hex tokens)
before it reaches a log line, an exception message, or a saved artefact. Tests assert
that a key echoed back by a provider never appears in the raised error.

## What voice cannot score yet

The voice pipeline runs the Agent Flow but does **not persist its step trace**: it
wires no `persist_fn` and creates no `conversations` row, and `GET /flow/trace` reads
only `conversations.flow_state`, which the text runner writes. So a voice session
yields a faithful transcript, tool trace, and final DB state — but no retrievable
per-step flow trace for the deterministic state-trace analyser.

The runner degrades honestly with a typed `voice_trace_unavailable` finding rather
than silently reporting a gap as a pass. See `WHISSLE_VOICE_TESTING.md`. This is
tracked as a product issue (`WHIS-VOICE-TRACE` draft) and needs reproducing before it
is filed as a backend bug.
