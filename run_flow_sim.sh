#!/usr/bin/env bash
# Run the SIMULATED-USER flow-testing engine: drive an LLM persona+goal user through
# a flow-enabled agent, capture the engine's declared flow + step trace, and run the
# deterministic rule-analyzer to surface state-tracking / state-rule bugs. Cleans up
# every throwaway agent it creates (per-session delete + an end-of-run flowsim-* sweep).
#
# The simulated user AND the LLM judges run on Whissle's OWN model API
# (POST /api/models/chat) — no external ANTHROPIC/OPENAI key is needed.
#
# Reads creds from .env (never from argv/stdout):
#   .env must define WHISSLE_API_KEY (a wsk_ secret key with agents:write).
#   Optional: WHISSLE_BASE (defaults to the prod gateway).
#
# Usage:
#   ./run_flow_sim.sh --agent-type dental_receptionist --sessions 10
#   ./run_flow_sim.sh --agent-type debt_collection --sessions 10
#   ./run_flow_sim.sh --agent-type dental_receptionist --task-id dental_reschedule
#   ./run_flow_sim.sh --agent-type customer_support --sessions 10 --no-semantic
#   ./run_flow_sim.sh list          # list agent types + tasks
#
# The 5 parallel runners each call, for their assigned type:
#   ./run_flow_sim.sh --agent-type <type> --sessions 10
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then echo "MISSING .env — add WHISSLE_API_KEY" >&2; exit 1; fi
set -a; . ./.env; set +a
: "${WHISSLE_API_KEY:?set in .env}"
export WHISSLE_BASE="${WHISSLE_BASE:-https://aws-gateway-backend.whissle.ai/bot}"

if [ "${1:-}" = "list" ]; then
  exec uv run python -m tau2.flow.simulate list
fi

echo "flow-sim: base=$WHISSLE_BASE args=$*"
# Base deps only (requests/typer/rich/dotenv) — no --extra needed.
exec uv run python -m tau2.flow.simulate run "$@"
