#!/usr/bin/env bash
# Run the Whissle VOICE benchmark. Reads creds from .env (never from argv/stdout).
#   .env must define WHISSLE_AGENT_ID and WHISSLE_API_KEY (see seed step below).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then echo "MISSING .env — run the seed step first" >&2; exit 1; fi
set -a; . ./.env; set +a
: "${WHISSLE_AGENT_ID:?set in .env}"; : "${WHISSLE_API_KEY:?set in .env}"
export WHISSLE_BASE="${WHISSLE_BASE:-https://aws-gateway-backend.whissle.ai/bot}"
export OPENAI_API_KEY="${OPENAI_API_KEY:?set in .env}"

DOMAIN="${1:-retail}"; N="${2:-1}"; CONC="${3:-1}"
OUT="results/whissle/voice_${DOMAIN}_$( [ "$N" = "all" ] && echo run || echo "smoke${N}" ).json"
ARGS=(run --domain "$DOMAIN" --audio-native --audio-native-provider whissle
      --user voice_streaming_user_simulator --user-llm gpt-4o
      --max-concurrency "$CONC" --save-to "$OUT")
[ "$N" != "all" ] && ARGS+=(--num-tasks "$N")
# Non-interactive: tau2 prompts "resume? (y/n)" if a checkpoint exists → EOF crash
# under background/no-stdin. It resolves --save-to under data/simulations/, so clean
# BOTH that and any cwd-relative copy. Start clean unless FRESH=0 keeps the prior run.
[ "${FRESH:-1}" = "1" ] && rm -rf "data/simulations/$OUT" "$OUT"
echo "voice run: domain=$DOMAIN tasks=$N conc=$CONC base=$WHISSLE_BASE agent=${WHISSLE_AGENT_ID:0:8}… -> $OUT"
exec uv run tau2 "${ARGS[@]}"
