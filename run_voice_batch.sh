#!/usr/bin/env bash
# Deepgram-voiced half-duplex batch across domains for the audio column.
set -uo pipefail
cd /Users/karan/Desktop/work/whissle/tau2-integration
set -a; . ./.env; set +a
export WHISSLE_USER_TTS_PROVIDER=deepgram
export WHISSLE_VOICE_MAX_TURN_S=45 WHISSLE_VOICE_STOP_GRACE_S=0.6
N="${1:-5}"; STEPS="${2:-40}"
mkdir -p logs
for dom in retail airline appliance_care; do
  echo "===== VOICE $dom (Deepgram, N=$N) ===== $(date -u +%H:%M:%SZ)"
  uv run tau2 run --domain "$dom" --agent whissle_voice --user user_simulator --user-llm gpt-4o \
    --max-concurrency 1 --max-steps "$STEPS" --num-tasks "$N" \
    --save-to "results/whissle/voice_${dom}_n${N}.json" 2>&1 \
    | grep -vE "bot-audio-frame#|mic-frame#" | tee "logs/voice_${dom}.log" \
    | grep -E "Pass\^1|Average Reward|DB Match|Total Sim|Status:|===" | tail -6
  echo "----- $dom voice done $(date -u +%H:%M:%SZ) -----"
done
echo "VOICE BATCH DONE"
