#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 {lora|base} {0.0|0.3|0.7}" >&2
  exit 2
fi

MODEL=$1
TEMP=$2
case "$MODEL" in
  lora) PORT=8000; SERVED_MODEL=swe-mini-lora ;;
  base) PORT=8001; SERVED_MODEL=swe-mini-base ;;
  *) echo "Model must be lora or base" >&2; exit 2 ;;
esac
case "$TEMP" in
  0.0|0.3|0.7) ;;
  *) echo "Temperature must be 0.0, 0.3, or 0.7" >&2; exit 2 ;;
esac

# Run the 0.7 LoRA arm on GPU 1 while the 0.3 arm continues on GPU 0.
if [[ "$MODEL" == "lora" && "$TEMP" == "0.7" ]]; then
  PORT=8001
  SERVED_MODEL=swe-mini-lora-gpu1
fi

ROOT=/data/results/swebench-eval/mini-verified5-paired-20260923
IDS=/data/configs/mini-sweagent/verified_eval/verified_5_ids.txt
CONFIG=/data/configs/mini-sweagent/verified_eval/mini_verified5_shared.yaml
OUT="$ROOT/$MODEL/temp-$TEMP"

export HF_ENDPOINT=https://hf-mirror.com
export HF_HOME=/data/huggingface
export PYTHONPATH=/data/configs/mini-sweagent${PYTHONPATH:+:$PYTHONPATH}
export MSWEA_COST_TRACKING=ignore_errors

if [[ $(wc -l < "$IDS") -ne 5 ]]; then
  echo "Expected five fixed Verified IDs in $IDS" >&2
  exit 1
fi

FILTER=$(tr -d '\r' < "$IDS" | paste -sd '|' -)
FILTER="^(${FILTER})$"
while IFS= read -r ID; do
  ID=${ID%$'\r'}
  if [[ ! $ID =~ $FILTER ]]; then
    echo "Filter failed to match fixed ID: $ID" >&2
    exit 1
  fi
done < "$IDS"

if ! curl -fsS --max-time 5 "http://127.0.0.1:$PORT/v1/models" | grep -q "$SERVED_MODEL"; then
  echo "Model endpoint $SERVED_MODEL on port $PORT is not ready" >&2
  exit 1
fi

mkdir -p "$ROOT" "$OUT"
if [[ "$MODEL" == "base" ]]; then
  LOCK="$ROOT/base.lock"
elif [[ "$TEMP" == "0.7" ]]; then
  LOCK="$ROOT/lora07.lock"
else
  LOCK="$ROOT/agent.lock"
fi
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "Another $MODEL Verified mini-agent job is already running" >&2
  exit 1
fi

echo "Starting model=$MODEL temperature=$TEMP output=$OUT at $(date -Is)"
/data/venvs/mini-sweagent/bin/mini-extra swebench \
  --subset verified \
  --split test \
  --filter "$FILTER" \
  --output "$OUT" \
  --workers 1 \
  --config swebench_backticks.yaml \
  --config "$CONFIG" \
  --config "model.model_name=openai/$SERVED_MODEL" \
  --config "model.model_kwargs.api_base=http://127.0.0.1:$PORT/v1" \
  --config "model.model_kwargs.temperature=$TEMP" \
  2>&1 | tee -a "$OUT/run.log"
echo "Finished model=$MODEL temperature=$TEMP at $(date -Is)"
