#!/usr/bin/env bash
# Run the DEFAULT-FLOW COVERAGE suite: prove every seeded agent TYPE ships a
# correct default conversation flow that the backend auto-attaches on creation
# (no flow supplied) and that actually DRIVES the conversation.
#
# Sibling of run_flow.sh. Creates one throwaway agent per seeded type over the
# deterministic TEXT channel, asserts auto-attach + driving, and cleans up its own
# agents (per-type delete + an end-of-run flowcov-* sweep).
#
# Reads creds from .env (never from argv/stdout):
#   .env must define WHISSLE_API_KEY (a wsk_ secret key with agents:write).
#   Optional: WHISSLE_BASE (defaults to the prod gateway).
#
# Usage:
#   ./run_flow_defaults.sh                     # all 15 seeded types
#   ./run_flow_defaults.sh debt_collection     # one type
#   KEEP_AGENT=1 ./run_flow_defaults.sh debt_collection   # leave the agent (debug)
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then echo "MISSING .env — add WHISSLE_API_KEY" >&2; exit 1; fi
set -a; . ./.env; set +a
: "${WHISSLE_API_KEY:?set in .env}"
export WHISSLE_BASE="${WHISSLE_BASE:-https://aws-gateway-backend.whissle.ai/bot}"

TYPE="${1:-}"
ARGS=(run)
[ -n "$TYPE" ] && ARGS+=(--agent-type "$TYPE")
[ -n "${KEEP_AGENT:-}" ] && ARGS+=(--keep-agent)

echo "flow-defaults: type=${TYPE:-all} base=$WHISSLE_BASE"
# Base deps only (requests/typer/rich/dotenv) — no --extra needed.
exec uv run python -m tau2.flow.defaults "${ARGS[@]}"
