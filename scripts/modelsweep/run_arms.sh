#!/usr/bin/env bash
# Drive one benchmark across every arm of the model sweep.
#
# Every arm runs the SAME cases in the SAME order with the SAME judge; the only
# thing that changes between arms is WHISSLE_MODEL. That is the whole design:
# any difference in the numbers has exactly one candidate explanation.
#
#   usage: run_arms.sh {medagent|agentclinic|pab} [arm ...]
set -uo pipefail

BENCH_REPO=/Users/karan/Desktop/work/whissle/live_assist/tau2-bench-w
SW="$(cd "$(dirname "$0")" && pwd)"

set -a
. "$BENCH_REPO/.env"                       # WHISSLE_API_KEY, WHISSLE_BASE
set +a
export WHISSLE_AGENT_ID=c2761ec7-d3f7-4bd7-b68c-40a87f1b1ab3   # bench-patient-services
export ANTHROPIC_API_KEY="$(cat "$SW/.akey")"                  # judge only
export MEDAGENTBENCH_FHIR_BASE=http://localhost:8090/fhir/

# The judge is pinned to one external model for every arm. On the default
# "whissle" route the judge is whatever /api/models/chat happens to route to —
# which is not nameable and could drift mid-sweep, and a judge that moves makes
# every score incomparable. So: Anthropic, named, constant.
JUDGE_ARGS=(--judge-provider anthropic --judge-model claude-sonnet-4-5)

# A case, not an associative array: macOS ships bash 3.2, which has no `declare -A`.
model_for() {
  case "$1" in
    haiku)   echo claude-haiku-4-5 ;;
    sonnet5) echo claude-sonnet-5 ;;
    opus5)   echo claude-opus-5 ;;
    fable5)  echo claude-fable-5 ;;
    g35f)    echo gemini-3.5-flash ;;
    g35fl)   echo gemini-3.5-flash-lite ;;
    g3fp)    echo gemini-3-flash-preview ;;
    *)       echo "" ;;
  esac
}
ORDER="haiku sonnet5 opus5 fable5 g35f g35fl g3fp"

BENCH="${1:?bench name}"; shift || true
ARMS="$*"; [ -z "$ARMS" ] && ARMS="$ORDER"

cd "$BENCH_REPO"
for arm in $ARMS; do
  M="$(model_for "$arm")"
  [ -z "$M" ] && { echo "unknown arm $arm"; continue; }
  echo "=============== $BENCH :: $arm ($M) :: $(date +%H:%M:%S) ==============="
  case "$BENCH" in
    medagent)
      # No judge here at all — grading is deterministic, which is why this is the
      # arm-comparison we trust most. --write-check execute is what turns the
      # integrity metric on: it POSTs to the FHIR container and reads back, so
      # "said" and "actually wrote" become separately observable.
      uv run python -m tau2.health.medagent.run run \
        --mode brain-parity --limit 25 --write-check execute \
        --max-round 8 --concurrency 4 --system-mode neutral \
        --model "$M" --run-name "sweep25_$arm" \
        > "$SW/log_medagent_$arm.txt" 2>&1
      ;;
    agentclinic)
      # --sample head is deterministic and ignores the seed, so every arm gets
      # the same first 25 MedQA scenarios. --prompt-mode override swaps in
      # AgentClinic's own doctor prompt, which is what isolates the brain from
      # our agent's persona.
      WHISSLE_MODEL="$M" uv run python -m tau2.health.agentclinic.run \
        --dataset MedQA --limit 25 --sample head --seed 42 \
        --prompt-mode override --protocol markers --history agentclinic \
        --total-inferences 20 --concurrency 4 \
        "${JUDGE_ARGS[@]}" --tag "sweep25_$arm" \
        > "$SW/log_agentclinic_$arm.txt" 2>&1
      ;;
    pab)
      # PatientAgentBench needs its own venv (it pins langchain 1.x against
      # tau2's 0.3.x). Same seeded 25-case sample for every arm.
      WHISSLE_MODEL="$M" \
      /Users/karan/Desktop/work/whissle/live_assist/pabvenv/bin/python \
        -m tau2.health.patientagent.cli run \
        --cases data/pab_cases_120.json --limit 25 --seed 42 \
        --mode harness --max-turns 15 --max-parallel 4 \
        --judge-provider anthropic --judge-model claude-sonnet-5-api \
        --jury claude-sonnet-5-api --patient-model claude-sonnet-5-api \
        --sandbox-model claude-sonnet-5-api \
        --output-dir output --name "sweep25_$arm" --label "$M" \
        > "$SW/log_pab_$arm.txt" 2>&1
      ;;
    *) echo "unknown bench $BENCH"; exit 2 ;;
  esac
  echo "   exit=$? $(date +%H:%M:%S)"
done
echo "ALL DONE $BENCH"
