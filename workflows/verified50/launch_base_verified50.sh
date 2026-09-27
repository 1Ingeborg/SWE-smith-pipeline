#!/usr/bin/env bash
set -euo pipefail

CONFIG=/data/configs/verified50
IDS=$CONFIG/verified_50_ids.txt
BASE=/data/results/swebench-eval/verified50-dual-base-bf16-t07-20260925/formal
SWE_SESSION=verified50-base-swe
MINI_SESSION=verified50-base-mini
EVAL_SESSION=verified50-base-auto-eval

if [[ $(wc -l < "$IDS") -ne 50 || $(sort -u "$IDS" | wc -l) -ne 50 ]]; then
  echo "The frozen ID list must contain exactly 50 unique instances." >&2
  exit 1
fi

for PORT in 8000 8001; do
  if ! curl -fsS --max-time 8 -H 'Authorization: Bearer swesmith' \
    "http://127.0.0.1:$PORT/v1/models" >/dev/null; then
    echo "Base BF16 API or tunnel on port $PORT is unavailable." >&2
    exit 1
  fi
done

FREE_GB=$(df -BG --output=avail /data | tail -n 1 | tr -dc '0-9')
if [[ -z "$FREE_GB" || "$FREE_GB" -lt 15 ]]; then
  echo "Only ${FREE_GB:-unknown} GB free on /data; require at least 15 GB before starting." >&2
  exit 1
fi

for SESSION in "$SWE_SESSION" "$MINI_SESSION" "$EVAL_SESSION"; do
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    echo "tmux session $SESSION already exists; refusing duplicate launch." >&2
    exit 1
  fi
done

if [[ -e "$BASE/sweagent" || -e "$BASE/mini" || -e "$BASE/harness" ]]; then
  echo "Base output directories already exist; refusing to mix results." >&2
  exit 1
fi

mkdir -p "$BASE"
tmux new-session -d -s "$SWE_SESSION" \
  "bash $CONFIG/run_with_status.sh $CONFIG/run_sweagent_base_verified50.sh $IDS $BASE/sweagent"
tmux new-session -d -s "$MINI_SESSION" \
  "bash $CONFIG/run_with_status.sh $CONFIG/run_mini_base_verified50.sh $IDS $BASE/mini"
tmux new-session -d -s "$EVAL_SESSION" \
  "bash $CONFIG/auto_eval_base_verified50.sh > $BASE/auto_eval.log 2>&1"

echo "Started both Base BF16 50-instance runs, temperature 0.7, and automatic Harness evaluation."
echo "SWE-agent run log: $BASE/sweagent/run.log"
echo "mini-SWE-agent run log: $BASE/mini/run.log"
echo "Automatic evaluation log: $BASE/auto_eval.log"
