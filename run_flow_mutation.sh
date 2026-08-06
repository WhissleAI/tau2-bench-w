#!/usr/bin/env bash
# Run the FLOW-EDIT SENSITIVITY suite: for each step of an agent type's
# conversation flow, make a targeted edit through the SAME API the flow-designer
# studio uses (PATCH ?target=draft → POST /publish), then probe a short live
# conversation and assert the runtime actually picked the change up. Proves the
# studio-edit → publish → runtime chain end-to-end; the draft/publish distinction
# is itself asserted (a draft must NOT change the live conversation, a publish
# MUST). Cleans up every throwaway flowsim-* agent it creates.
#
# Reads creds from .env (never from argv/stdout):
#   .env must define WHISSLE_API_KEY (a wsk_ secret key with agents:write).
#   Optional: WHISSLE_BASE (defaults to the prod gateway).
#
# Usage:
#   ./run_flow_mutation.sh plan  --agent-type headache_enrollment
#   ./run_flow_mutation.sh run   --agent-type headache_enrollment
#   ./run_flow_mutation.sh run   --agent-type headache_enrollment --mode voice
#   ./run_flow_mutation.sh run   --agent-type headache_enrollment --voice-spot-checks
#   ./run_flow_mutation.sh run   --agent-type headache_enrollment --mutation say_sentinel_greet
#
# Exit status: non-zero when any mutation FAILS — a failing row is a product bug
# (a published studio edit that did not reach the live conversation).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then echo "MISSING .env — add WHISSLE_API_KEY" >&2; exit 1; fi
set -a; . ./.env; set +a
: "${WHISSLE_API_KEY:?set in .env}"
export WHISSLE_BASE="${WHISSLE_BASE:-https://aws-gateway-backend.whissle.ai/bot}"

echo "flow-mutation: base=$WHISSLE_BASE args=$*"
# Base deps only (requests/typer/rich/dotenv); voice probes reuse the existing
# voice extra (LiveKit) exactly like run_flow_voice.sh.
exec uv run python -m tau2.flow.mutation_suite "$@"
