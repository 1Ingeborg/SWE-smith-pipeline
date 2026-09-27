#!/usr/bin/env bash
set -euo pipefail

CONFIG=/data/configs/verified50
BASE=/data/results/swebench-eval/verified50-dual-lora-t07-20260924/formal
PLAN=$CONFIG/split_20260924
SPLIT=$BASE/mini_split

if [[ ! -f "$BASE/mini/exit_code" ]]; then
  echo "Original mini process has not exited; refusing to split a live preds.json." >&2
  exit 1
fi
if [[ $(<"$BASE/sweagent/exit_code") != 0 ]]; then
  echo "SWE-agent generation did not complete successfully." >&2
  exit 1
fi
if [[ -e "$PLAN" || -e "$SPLIT" ]]; then
  echo "A split plan or output directory already exists; refusing to overwrite it." >&2
  exit 1
fi
for PORT in 8000 8001; do
  if ! curl -fsS --max-time 8 "http://127.0.0.1:$PORT/v1/models" | grep -q swe-mini-lora; then
    echo "Mini LoRA API on port $PORT is unavailable." >&2
    exit 1
  fi
done
FREE_GB=$(df -BG --output=avail /data | tail -n 1 | tr -dc '0-9')
if [[ -z "$FREE_GB" || "$FREE_GB" -lt 10 ]]; then
  echo "Only ${FREE_GB:-unknown} GB free on /data; require at least 10 GB." >&2
  exit 1
fi
for SESSION in verified50-mini-gpu0 verified50-mini-gpu1 verified50-auto-eval; do
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "tmux session $SESSION already exists; refusing a duplicate launch." >&2
    exit 1
  fi
done

/data/venvs/mini-sweagent/bin/python "$CONFIG/plan_mini_split.py" \
  "$CONFIG/verified_50_ids.txt" "$BASE/mini/preds.json" "$PLAN"
mkdir -p "$SPLIT"
if [[ -f "$BASE/auto_eval.log" ]]; then
  cp -p "$BASE/auto_eval.log" "$BASE/auto_eval_before_split.log"
fi

tmux new-session -d -s verified50-mini-gpu0 \
  "bash $CONFIG/run_mini_split_worker.sh $PLAN/gpu0_ids.txt $SPLIT/gpu0 8000"
tmux new-session -d -s verified50-mini-gpu1 \
  "bash $CONFIG/run_mini_split_worker.sh $PLAN/gpu1_ids.txt $SPLIT/gpu1 8001"
tmux new-session -d -s verified50-auto-eval \
  "MINI_SPLIT_MODE=1 bash $CONFIG/auto_eval_verified50.sh > $BASE/auto_eval.log 2>&1"

echo "Two disjoint mini workers started on ports 8000 and 8001."
echo "Split plan: $PLAN/plan.json"
echo "Worker logs: $SPLIT/{gpu0,gpu1}/run.log"
echo "Automatic Harness log: $BASE/auto_eval.log"
