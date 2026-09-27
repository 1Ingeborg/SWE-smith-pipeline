#!/usr/bin/env bash
set -euo pipefail

CONFIG=/data/configs/verified50
BASE=/data/results/swebench-eval/verified50-dual-base-bf16-t07-20260925/formal
PLAN=$BASE/mini_split_plan
SPLIT=$BASE/mini_split
ORIGINAL_MINI_SESSION=verified50-base-mini
ORIGINAL_EVAL_SESSION=verified50-base-auto-eval
GPU0_SESSION=verified50-base-mini-gpu0
GPU1_SESSION=verified50-base-mini-gpu1

echo "$(date -Is) Waiting for SWE-agent generation to finish before splitting mini."
while [[ ! -f "$BASE/sweagent/exit_code" ]]; do
  sleep 30
done

if [[ $(<"$BASE/sweagent/exit_code") != 0 ]]; then
  echo "$(date -Is) SWE-agent failed; leaving the original mini and evaluator unchanged."
  exit 1
fi

if [[ -f "$BASE/mini/exit_code" ]]; then
  echo "$(date -Is) Original mini already finished; original evaluator will handle it."
  exit 0
fi

if [[ -e "$PLAN" || -e "$SPLIT" ]]; then
  echo "Split outputs already exist; refusing to overwrite them." >&2
  exit 1
fi
for SESSION in "$GPU0_SESSION" "$GPU1_SESSION"; do
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "Split worker $SESSION already exists; refusing a duplicate." >&2
    exit 1
  fi
done
for PORT in 8000 8001; do
  curl -fsS --max-time 8 -H 'Authorization: Bearer swesmith' \
    "http://127.0.0.1:$PORT/v1/models" >/dev/null
done

FREE_GB=$(df -BG --output=avail /data | tail -n 1 | tr -dc '0-9')
if [[ -z "$FREE_GB" || "$FREE_GB" -lt 12 ]]; then
  echo "Only ${FREE_GB:-unknown} GB free; leaving original mini and evaluator unchanged." >&2
  exit 1
fi

# With one worker, at most its current instance is absent from preds.json.
# Leave the normal run alone when fewer than three IDs remain.
DONE=$(jq length "$BASE/mini/preds.json")
PENDING=$((50 - DONE))
if [[ $PENDING -lt 3 ]]; then
  echo "$(date -Is) Only $PENDING mini IDs remain; leaving original run and evaluator unchanged."
  exit 0
fi

echo "$(date -Is) SWE-agent finished; mini has $DONE/50 predictions."
# The original evaluator must be stopped before mini writes its early exit_code;
# otherwise it would evaluate an incomplete preds.json.
if tmux has-session -t "$ORIGINAL_EVAL_SESSION" 2>/dev/null; then
  tmux kill-session -t "$ORIGINAL_EVAL_SESSION"
  echo "$(date -Is) Stopped the original evaluator, which was only waiting."
fi

if [[ ! -f "$BASE/mini/exit_code" ]]; then
  if tmux has-session -t "$ORIGINAL_MINI_SESSION" 2>/dev/null; then
    tmux send-keys -t "$ORIGINAL_MINI_SESSION" C-c
    echo "$(date -Is) Asked original mini to finish its current case and cancel pending cases."
  else
    echo "Original mini has no tmux session or exit_code; refusing to split." >&2
    exit 1
  fi
fi

while [[ ! -f "$BASE/mini/exit_code" ]]; do
  sleep 15
done

DONE=$(jq length "$BASE/mini/preds.json")
PENDING=$((50 - DONE))
echo "$(date -Is) Original mini stopped with $DONE/50 predictions; $PENDING remain."
if [[ $PENDING -eq 0 ]]; then
  echo "No split needed; running the normal evaluator."
  bash "$CONFIG/auto_eval_base_verified50.sh"
  exit $?
fi
if [[ $PENDING -lt 2 ]]; then
  echo "Only one ID remains after stopping mini; manual single-ID completion is required." >&2
  exit 1
fi

/data/venvs/mini-sweagent/bin/python "$CONFIG/plan_mini_split.py" \
  "$CONFIG/verified_50_ids.txt" "$BASE/mini/preds.json" "$PLAN"
mkdir -p "$SPLIT"

tmux new-session -d -s "$GPU0_SESSION" \
  "MINI_PORT=8000 bash $CONFIG/run_with_status.sh $CONFIG/run_mini_base_verified_port.sh $PLAN/gpu0_ids.txt $SPLIT/gpu0"
tmux new-session -d -s "$GPU1_SESSION" \
  "MINI_PORT=8001 bash $CONFIG/run_with_status.sh $CONFIG/run_mini_base_verified_port.sh $PLAN/gpu1_ids.txt $SPLIT/gpu1"
echo "$(date -Is) Started disjoint mini workers on GPU 0 and GPU 1."
echo "Split plan: $PLAN/plan.json"
echo "Worker logs: $SPLIT/{gpu0,gpu1}/run.log"

bash "$CONFIG/auto_eval_base_split_verified50.sh"
