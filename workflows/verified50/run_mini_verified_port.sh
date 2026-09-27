#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 IDS_FILE OUTPUT_DIR API_PORT" >&2
  exit 2
fi

IDS_FILE=$1
OUT=$2
PORT=$3
if [[ $PORT != 8000 && $PORT != 8001 ]]; then
  echo "API_PORT must be 8000 or 8001" >&2
  exit 2
fi
FILTER=$(tr -d '\r' < "$IDS_FILE" | paste -sd '|' -)
FILTER="^(${FILTER})$"
if [[ $FILTER == '^()$' ]]; then
  echo "Empty instance selection" >&2
  exit 1
fi
if ! curl -fsS --max-time 8 "http://127.0.0.1:$PORT/v1/models" | grep -q swe-mini-lora; then
  echo "Mini LoRA API on port $PORT is unavailable" >&2
  exit 1
fi

mkdir -p "$OUT"
export HF_HOME=/data/huggingface
export HF_HUB_OFFLINE=1
export HF_DATASETS_OFFLINE=1
export PYTHONPATH=/data/configs/mini-sweagent${PYTHONPATH:+:$PYTHONPATH}
export MSWEA_COST_TRACKING=ignore_errors

echo "mini-SWE-agent split: $(wc -l < "$IDS_FILE") IDs, port=$PORT, temperature=0.7"
/data/venvs/mini-sweagent/bin/mini-extra swebench \
  --subset verified \
  --split test \
  --filter "$FILTER" \
  --output "$OUT" \
  --workers 1 \
  --config swebench_backticks.yaml \
  --config /data/configs/mini-sweagent/verified_eval/mini_verified5_shared.yaml \
  --config model.model_name=openai/swe-mini-lora \
  --config "model.model_kwargs.api_base=http://127.0.0.1:$PORT/v1" \
  --config model.model_kwargs.temperature=0.7
