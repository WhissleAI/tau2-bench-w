#!/usr/bin/env bash
# Run the conversation-FLOW state-machine bench suite (multi-turn, multi-tool).
# Drives a flow onto throwaway agents over the deterministic TEXT channel and
# asserts the state machine executes correctly. Cleans up its own agents.
#
# Reads creds from .env (never from argv/stdout):
#   .env must define WHISSLE_API_KEY (a wsk_ secret key with agents:write).
#   Optional: WHISSLE_BASE (defaults to the prod gateway).
#
# Usage:
#   ./run_flow.sh                 # all scenarios
#   ./run_flow.sh marker          # one scenario (the proven canary)
#   ./run_flow.sh appointment     # the multi-tool scenario
#   ./run_flow.sh guarded_loop    # the loop-guard scenario
#   KEEP_AGENT=1 ./run_flow.sh marker   # leave the agent for debugging
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then echo "MISSING .env — add WHISSLE_API_KEY" >&2; exit 1; fi
set -a; . ./.env; set +a
: "${WHISSLE_API_KEY:?set in .env}"
export WHISSLE_BASE="${WHISSLE_BASE:-https://aws-gateway-backend.whissle.ai/bot}"

SCENARIO="${1:-}"
ARGS=(run)
[ -n "$SCENARIO" ] && ARGS+=(--scenario "$SCENARIO")
[ -n "${KEEP_AGENT:-}" ] && ARGS+=(--keep-agent)

echo "flow bench: scenario=${SCENARIO:-all} base=$WHISSLE_BASE"
# Base deps only (requests/typer/rich/dotenv) — no --extra needed.
exec uv run python -m tau2.flow.benchmark "${ARGS[@]}"
