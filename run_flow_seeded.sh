#!/usr/bin/env bash
# SEEDED end-to-end flow validation — the "true completion" run. Wraps the
# simulated-user engine (run_flow_sim.sh) with a per-agent SEEDING layer
# (src/tau2/flow/seed.py) so every tool has the real records it needs to SUCCEED:
# a customer bound to the agent (debt/CS), the auto-seeded fleet (car) and schedule
# (appt/dental), a KB doc + a data-lookup credential (CS). Each throwaway agent is
# seeded, driven, asserted (action-tool-actually-called, debt pre-verify-disclosure
# scan, CS resolve_done), then EVERY seeded record + the agent is torn down by id.
#
# It is the seeded counterpart of run_flow_sim.sh: same creds (.env WHISSLE_API_KEY
# + WHISSLE_BASE), same product surface, same clean-up guarantees. The seeding is
# best-effort per step — a key missing contacts:write / kb:write degrades those
# steps to skipped (the tool then fail-softs) while within-scope seeding still runs.
#
# Usage:
#   ./run_flow_seeded.sh --agent-type debt_collection --sessions 10
#   ./run_flow_seeded.sh --agent-type car_rental --sessions 10 --no-semantic
#   ./run_flow_seeded.sh all              # all 5 core types × 10 seeded sessions
#   ./run_flow_seeded.sh list             # list types + tasks
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then echo "MISSING .env — add WHISSLE_API_KEY" >&2; exit 1; fi
set -a; . ./.env; set +a
: "${WHISSLE_API_KEY:?set in .env}"
export WHISSLE_BASE="${WHISSLE_BASE:-https://aws-gateway-backend.whissle.ai/bot}"

CORE_TYPES=(dental_receptionist appointment_scheduling car_rental debt_collection customer_support)

if [ "${1:-}" = "list" ]; then
  exec uv run python -m tau2.flow.simulate list
fi

if [ "${1:-}" = "all" ]; then
  shift || true
  for t in "${CORE_TYPES[@]}"; do
    echo "════════ SEEDED $t ════════"
    uv run python -m tau2.flow.simulate run --agent-type "$t" --sessions 10 --seeded "$@" || true
  done
  exit 0
fi

echo "flow-seeded: base=$WHISSLE_BASE args=$*"
exec uv run python -m tau2.flow.simulate run --seeded "$@"
