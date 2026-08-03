#!/usr/bin/env bash
# Run the Whissle TRANSCRIPTION benchmark (pre-recorded audio → WER/CER).
# Reads creds from .env (never from argv/stdout):
#   .env must define WHISSLE_API_KEY (a wsk_ secret key with models:invoke).
#   Optional: WHISSLE_BASE (defaults to prod gateway).
#
# Usage:
#   ./run_transcribe.sh                      # round-trip, all languages, seed set
#   ./run_transcribe.sh hi                   # one language
#   ./run_transcribe.sh all 3                # all languages, 3 repeats each
#   MODE=corpus MANIFEST=data/transcription/my_corpus.jsonl ./run_transcribe.sh
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then echo "MISSING .env — add WHISSLE_API_KEY" >&2; exit 1; fi
set -a; . ./.env; set +a
: "${WHISSLE_API_KEY:?set in .env}"
export WHISSLE_BASE="${WHISSLE_BASE:-https://aws-gateway-backend.whissle.ai/bot}"

# This benchmark drives the `whissle` CLI (github.com/WhissleAI/whissle-cli).
# Install it (npm i -g / npm link) so `whissle` is on PATH, or point WHISSLE_CLI at it.
if [ -z "${WHISSLE_CLI:-}" ] && ! command -v whissle >/dev/null 2>&1; then
  echo "MISSING the whissle CLI — install it or set WHISSLE_CLI=\"node /path/to/whissle-cli/bin/whissle.mjs\"" >&2
  exit 1
fi

LANG_ARG="${1:-all}"; REPEAT="${2:-1}"
MODE="${MODE:-round-trip}"; MANIFEST="${MANIFEST:-data/transcription/whissle_roundtrip.jsonl}"

ARGS=(run --manifest "$MANIFEST" --mode "$MODE" --repeat "$REPEAT")
[ "$LANG_ARG" != "all" ] && ARGS+=(--language "$LANG_ARG")

echo "transcribe run: mode=$MODE lang=$LANG_ARG repeat=$REPEAT base=$WHISSLE_BASE"
# --extra voice: this lives in the voice module, whose __init__ pulls websockets.
exec uv run --extra voice python -m tau2.voice.transcription.benchmark "${ARGS[@]}"
