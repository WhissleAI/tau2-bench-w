#!/usr/bin/env bash
# Half-duplex whissle_voice benchmark (turn-based; the CORRECT model for Whissle's cascade).
set -euo pipefail
cd "$(dirname "$0")"
set -a; . ./.env; set +a
: "${WHISSLE_AGENT_ID:?}"; : "${WHISSLE_API_KEY:?}"; : "${OPENAI_API_KEY:?}"
# user-sim TTS provider (WHISSLE_USER_TTS_PROVIDER: openai default | elevenlabs | deepgram);
# require only the selected provider's key. OPENAI_API_KEY is always needed (user-sim LLM).
case "${WHISSLE_USER_TTS_PROVIDER:-openai}" in
  elevenlabs) : "${ELEVENLABS_API_KEY:?set ELEVENLABS_API_KEY or pick another WHISSLE_USER_TTS_PROVIDER}" ;;
  deepgram)   : "${DEEPGRAM_API_KEY:?set DEEPGRAM_API_KEY or pick another WHISSLE_USER_TTS_PROVIDER}" ;;
esac
echo "user-sim TTS provider: ${WHISSLE_USER_TTS_PROVIDER:-openai}"
# livekit.rtc lives in the `voice` extra; without it every task fails identically
# ("No module named 'livekit'") only after the run is already underway.
uv run python -c 'import livekit.rtc' 2>/dev/null || {
  echo "error: livekit.rtc missing — run 'uv sync --extra voice'" >&2; exit 1; }
DOMAIN="${1:-retail}"; N="${2:-1}"; CONC="${3:-1}"; STEPS="${4:-40}"
OUT="results/whissle/hd_${DOMAIN}_$( [ "$N" = all ] && echo run || echo "n${N}" ).json"
rm -rf "data/simulations/$OUT" "$OUT"
ARGS=(run --domain "$DOMAIN" --agent whissle_voice --user user_simulator --user-llm gpt-4o
      --max-concurrency "$CONC" --max-steps "$STEPS" --save-to "$OUT")
[ "$N" != all ] && ARGS+=(--num-tasks "$N")
echo "HD voice: domain=$DOMAIN tasks=$N conc=$CONC steps=$STEPS agent=${WHISSLE_AGENT_ID:0:8}… -> $OUT"
exec uv run tau2 "${ARGS[@]}"
