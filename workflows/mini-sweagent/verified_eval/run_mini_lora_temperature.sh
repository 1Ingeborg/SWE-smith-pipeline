#!/usr/bin/env bash
set -euo pipefail

ROOT=/data/results/swebench-eval/mini-lora-temperature-verified5-20260923-v2
IDS=/data/configs/mini-sweagent/verified_eval/verified_5_ids.txt
CONFIG=/data/configs/mini-sweagent/verified_eval/mini_lora_temperature.yaml

export HF_ENDPOINT=https://hf-mirror.com
export HF_HOME=/data/huggingface
export PYTHONPATH=/data/configs/mini-sweagent${PYTHONPATH:+:$PYTHONPATH}
export MSWEA_COST_TRACKING=ignore_errors

if [[ $(wc -l < "$IDS") -ne 5 ]]; then
  echo "Expected exactly five fixed Verified instance IDs" >&2
  exit 1
fi

FILTER=$(tr -d '\r' < "$IDS" | paste -sd '|' -)
FILTER="^(${FILTER})$"
while IFS= read -r ID; do
  ID=${ID%$'\r'}
  if [[ ! $ID =~ $FILTER ]]; then
    echo "Filter failed to match fixed instance ID: $ID" >&2
    exit 1
  fi
done < "$IDS"
mkdir -p "$ROOT"

for TEMP in 0.0 0.3 0.7; do
  OUT="$ROOT/temp-$TEMP"
  mkdir -p "$OUT"
  echo "Starting temperature=$TEMP at $(date -Is)" | tee -a "$ROOT/progress.log"
  /data/venvs/mini-sweagent/bin/mini-extra swebench \
    --subset verified \
    --split test \
    --filter "$FILTER" \
    --output "$OUT" \
    --workers 1 \
    --config swebench_backticks.yaml \
    --config "$CONFIG" \
    --config "model.model_kwargs.temperature=$TEMP" \
    >> "$OUT/run.log" 2>&1
  echo "Finished temperature=$TEMP at $(date -Is)" | tee -a "$ROOT/progress.log"
done
