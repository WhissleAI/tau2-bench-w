#!/usr/bin/env bash
# Run the flow-sim engine over the REAL VOICE transport: drive an LLM persona+goal
# user through a flow-enabled agent's actual spoken pipeline (STT → flow-brain → TTS
# over LiveKit), instead of the deterministic /chat/turn text channel. This exercises
# the voice path the text harness cannot: turn-taking/endpointing, TTS, barge-in, and
# STT of the simulated caller's audio. Same user-simulator + task-success judge; the
# agent's own spoken transcript (RTVI bot-transcription) is the scored text, and the
# duplex call audio (caller/bot/mix WAVs) is written per session as evidence.
#
# This is `run_flow_sim.sh` with `--mode voice`. See WHISSLE_VOICE_TESTING.md for the
# transport, the honest headless-feasibility verdict, and the one flow-step-trace gap.
#
# Self-contained on a single wsk_ key: the user-simulator + judges use Whissle's chat
# model API and the caller's voice uses Whissle's TTS (/api/models/tts) — no external
# ANTHROPIC/OPENAI/ElevenLabs key required.
#
# Reads creds from .env (never from argv/stdout):
#   .env must define WHISSLE_API_KEY (a wsk_ secret with agents:write).
#   Optional: WHISSLE_BASE (defaults to the prod gateway),
#             WHISSLE_USER_VOICE_ID (a TTS voice for the simulated caller),
#             WHISSLE_VOICE_QUIET_GAP_S / WHISSLE_VOICE_MAX_TURN_S (turn-taking tuning).
#
# Prereqs: the LiveKit voice deps (`uv sync --extra voice` — pulls livekit.rtc), and a
# backend with /api/bench/voice/start + LIVEKIT_ENABLED=1 (live on the AWS backend).
#
# Usage:
#   ./run_flow_voice.sh --agent-type dental_receptionist --sessions 1
#   ./run_flow_voice.sh --agent-type dental_receptionist --task-id dental_happy_book
#   ./run_flow_voice.sh --agent-type car_rental --sessions 3
#   ./run_flow_voice.sh list          # list agent types + tasks
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then echo "MISSING .env — add WHISSLE_API_KEY" >&2; exit 1; fi
set -a; . ./.env; set +a
: "${WHISSLE_API_KEY:?set in .env}"
export WHISSLE_BASE="${WHISSLE_BASE:-https://aws-gateway-backend.whissle.ai/bot}"

if [ "${1:-}" = "list" ]; then
  exec uv run python -m tau2.flow.simulate list
fi

# livekit.rtc lives in the `voice` extra. Ensure it's importable, else guide the user.
if ! uv run python -c "import livekit.rtc" >/dev/null 2>&1; then
  echo "voice deps missing — run:  uv sync --extra voice" >&2
  exit 1
fi

echo "flow-voice: base=$WHISSLE_BASE args=$*"
exec uv run python -m tau2.flow.simulate run --mode voice "$@"
