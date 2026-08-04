# Flow testing over the REAL VOICE transport

The flow-sim suite ([`WHISSLE_FLOW_SIM.md`](WHISSLE_FLOW_SIM.md)) drives an LLM
persona+goal user through a flow-enabled agent and audits the in-call state machine.
By default it drives the **text** channel (`POST /api/agents/{id}/chat/turn`) — the
same `FlowRuntime` the voice pipeline runs, but with zero audio nondeterminism, so a
state-sequence assertion is exact and repeatable.

That text path exercises **none** of the voice stack. This adds a **voice** transport
(`--mode voice`, [`run_flow_voice.sh`](run_flow_voice.sh)) that drives the SAME
persona, user-simulator, and task-success judge through Whissle's **real spoken
pipeline** — STT → flow-brain(+tools) → TTS over a live LiveKit room — so the run
validates the parts the text harness cannot: **turn-taking/endpointing, TTS, STT of
the caller's audio, and barge-in.** It reuses `usersim.py` and the analyzer wiring in
`simulate.py` unchanged; only the per-turn transport swaps.

## What this is (and how it differs from the existing voice runs)

This repo already has two LiveKit voice paths that score tau2 **retail/airline/telecom**
tool-use tasks: the turn-based `whissle_voice` agent ([`WHISSLE_HALF_DUPLEX.md`](WHISSLE_HALF_DUPLEX.md))
and the tick-based full-duplex provider ([`WHISSLE_VOICE.md`](WHISSLE_VOICE.md)). Both
delegate tool calls back to the tau2 environment (`bench` mode).

**This is different**: it drives the **flow / in-call-state-machine** suite (the
seeded agent types — `dental_receptionist`, `car_rental`, `debt_collection`, …) over
voice, in `real=true` mode (the agent runs its OWN prompt + flow + tools + greeting,
nothing delegated), and reuses the flow-sim's persona/goal user + task-success judge.
It answers: *does the agent's real spoken pipeline still complete the flow's task when
the turns go through STT and TTS instead of a JSON text turn?*

## Architecture

```
flow/simulate.py  run_session(mode="voice")
  │  create_typed_agent(agent_type)  → backend auto-attaches the type's flow
  │  GET /api/agents/{id}            → the DECLARED flow spec (for coverage/audit)
  ▼
flow/voice_transport.py  VoiceTransport
  │  POST /api/bench/voice/start {"agent_id","real":true}  → LiveKit {url, token, room}
  │      backend spawns the agent's REAL voice pipeline (flow_active → FlowController)
  ▼
voice/audio_native/whissle/provider.py  WhissleRoomProvider  (reused as-is)
  ├─ joins the room as the USER participant
  ├─ each user-sim turn → /api/models/tts (pcm_16000) → publish as mic audio → STT hears it
  ├─ subscribes to the agent TTS track → captured as a duplex WAV
  └─ reads the agent's own spoken transcript off the data channel (RTVI bot-transcription)
  ▼
usersim.py  (UNCHANGED)  persona/goal user + judge_task_success  →  transcript-scored
```

Per turn the transport TTS-synthesizes the user utterance (Whissle's own
`/api/models/tts`, `output_format=pcm_16000` → raw PCM16 @ 16 kHz — no external voice
key, no decode), publishes it as 10 ms WebRTC frames + 0.5 s trailing silence so the
endpointer fires, then blocks until the agent produces transcript+audio and stays
quiet for `WHISSLE_VOICE_QUIET_GAP_S`. It measures **per-turn latency**
(user-stopped-speaking → first agent audio) and writes `caller.wav` / `bot.wav` /
`mix.wav` per session as evidence.

## Feasibility from a headless environment — VERDICT

**Fully feasible and proven end-to-end from a headless server — no browser, no media
device.** `livekit.rtc` (the pipecat transport dep, in the `voice` extra) is a native
WebRTC client; it connects to the room, publishes a synthetic mic track, and
subscribes to the bot's audio entirely in-process. The caller's voice is TTS from an
API and the agent's words come back as a data-channel transcript, so nothing needs a
soundcard, microphone, or display. A real session runs from CI/a plain shell.

The one requirement is network reachability to the LiveKit signaling/media host
(`wss://aws-gateway-backend.whissle.ai`) and a backend with `LIVEKIT_ENABLED=1` +
`/api/bench/voice/start` (live on the AWS backend today).

## The one honest gap — voice flow **step-trace** is not persisted

The deterministic state-trace analyzer (`analyze.py`) audits the **step trace** the
engine records (`state_enter`, `transition_check`, `tools_gated`, `guard_trip`, …).
Over **text**, `POST /chat/turn` persists that trace and `GET
/api/agents/{id}/flow/trace?conversation_id=…` returns it. Over **voice, it is not
retrievable**:

- The voice pipeline **runs the identical flow** — `bot/pipeline.py` calls
  `build_flow_controller(...)` which instantiates the same `FlowRuntime` + `FlowTrace`
  as the text runner (`services/flow/controller.py`). State entries, transitions,
  say-lines, and tool-gating all execute.
- But the controller is built with **`persist_fn=None`**, so `_persist_trace()` is a
  no-op; the voice path creates **no `conversations` row**; and `GET /flow/trace`
  reads only `conversations.flow_state["trace"]`, which **only the text runner writes**
  (`services/text_turn.py` → `save_flow_state`). A voice session's identifier is the
  LiveKit **room name**, which is not a `conversations.id`. So there is nothing to GET.

Consequently, over voice the harness scores from the **spoken transcript** (task
success, communicate-info) + **latency** + **duplex-audio** evidence, and emits a
typed **`voice_trace_unavailable`** (info) finding instead of running the state-trace
checks. This is a transport/persistence gap, **not** a product bug, and the runner is
forward-compatible: the moment the backend persists the voice trace, `full_steps` is
non-empty and the **unchanged** analyzer runs on it (`simulate.py` already branches on
`full_steps`).

### Exact backend change that closes it (one PR, ~15 lines)

In `whissle_gateway_backend/pipecat-bot`:

1. In `POST /api/bench/voice/start` (`server.py`), for `real=true` create a
   `conversations` row and stash its id on the agent dict (e.g. `agent["_conv_id"]`),
   and **return it** in the JSON (`{"url","token","room","conversation_id"}`).
2. In `bot/pipeline.py` where `build_flow_controller(...)` is called, pass
   `persist_fn=lambda snap: conversations_store.save_flow_state(pool, conv_id, snap)`
   (the exact call `services/text_turn.py` already uses), so the FlowController
   persists `export_flow_state()` at turn boundaries / call end.

Then `VoiceTransport` should thread that returned `conversation_id` onto the
`TurnResult` (a two-line change), and `simulate.py`'s existing `get_trace(agent_id,
conv_id)` call — already guarded with `and not voice` — is re-enabled for voice. No
analyzer change; the deterministic state-trace suite then runs identically over voice.

## Usage

```bash
uv sync --extra voice     # pulls livekit.rtc (once)
# .env needs WHISSLE_API_KEY (a wsk_ key); WHISSLE_BASE defaults to the AWS gateway.

./run_flow_voice.sh --agent-type dental_receptionist --task-id dental_happy_book
./run_flow_voice.sh --agent-type dental_receptionist --sessions 3
./run_flow_voice.sh --agent-type car_rental --sessions 2
./run_flow_voice.sh list
```

Results land under `results/whissle/flow_sim/<agent_type>/` — per-session JSON (with
`mode:"voice"`, `voice_room`, `greeting`, `audio` paths + per-turn latencies), the
JSONL event log, and `<task>_<ts>.{caller,bot,mix}.wav`.

### Tuning (env)

| var | default | meaning |
|---|---|---|
| `WHISSLE_VOICE_QUIET_GAP_S` | `2.0` | silence after the agent stops = end-of-turn |
| `WHISSLE_VOICE_MAX_TURN_S`  | `45`  | hard cap awaiting one agent turn |
| `WHISSLE_USER_VOICE_ID`     | —     | a Whissle TTS voice id for the simulated caller |

## Notes & honesty

- **Concurrency 1.** Each session holds a live LiveKit room + a real voice pipeline;
  run sessions serially (the flow-sim runner already does).
- **Transcript = the agent's own words.** The scored agent text is Whissle's RTVI
  `bot-transcription` (its TTS transcript), deduped for the interim/final repetition
  the surface emits — not a re-ASR of our capture. `finish(transcribe=True)`
  additionally re-transcribes the captured `bot.wav` via `/api/models/transcribe` as
  an independent cross-check, stored under `audio.bot_reasr`.
- **No per-turn state over voice**, so the goal-drift judge (which grades against the
  active flow state) is auto-disabled in voice mode; task-success (whole-transcript)
  still runs.
- **The agent greets first** over voice (a real answered call), unlike the text path
  where the user opens; the greeting is captured and included in the scored transcript.
