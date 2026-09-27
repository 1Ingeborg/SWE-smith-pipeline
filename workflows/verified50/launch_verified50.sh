#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 1 || ( $# -eq 1 && $1 != --check-only ) ]]; then
  echo "Usage: $0 [--check-only]" >&2
  exit 2
fi

CONFIG=/data/configs/verified50
IDS=$CONFIG/verified_50_ids.txt
BASE=/data/results/swebench-eval/verified50-dual-lora-t07-20260924/formal
SWE_SESSION=verified50-swe
MINI_SESSION=verified50-mini

if [[ ! -f "$CONFIG/preflight_approved.ok" ]]; then
  echo "Preflight has not been approved; formal run was not started." >&2
  exit 1
fi

if [[ $(wc -l < "$IDS") -ne 50 || $(sort -u "$IDS" | wc -l) -ne 50 ]]; then
  echo "The frozen ID list must contain exactly 50 unique instances." >&2
  exit 1
fi

if ! curl -fsS --max-time 8 -H 'Authorization: Bearer swesmith' \
  http://127.0.0.1:8000/v1/models >/dev/null; then
  echo "SWE-agent LoRA API or tunnel on port 8000 is unavailable." >&2
  exit 1
fi

if ! curl -fsS --max-time 8 http://127.0.0.1:8001/v1/models | grep -q swe-mini-lora; then
  echo "mini-SWE-agent LoRA API or tunnel on port 8001 is unavailable." >&2
  exit 1
fi

FREE_GB=$(df -BG --output=avail /data | tail -n 1 | tr -dc '0-9')
if [[ -z "$FREE_GB" || "$FREE_GB" -lt 50 ]]; then
  echo "Only ${FREE_GB:-unknown} GB free on /data; require at least 50 GB before starting." >&2
  exit 1
fi

if tmux has-session -t "$SWE_SESSION" 2>/dev/null ||
   tmux has-session -t "$MINI_SESSION" 2>/dev/null ||
   tmux has-session -t verified50-auto-eval 2>/dev/null; then
  echo "A formal verified50 tmux session already exists; refusing a duplicate launch." >&2
  exit 1
fi

if [[ -e "$BASE/sweagent" || -e "$BASE/mini" ]]; then
  echo "Formal output directories already exist; refusing to mix results." >&2
  exit 1
fi

if [[ ${1:-} == --check-only ]]; then
  echo "Ready: 50 unique IDs, both APIs reachable, ${FREE_GB} GB free, no duplicate sessions or outputs."
  exit 0
fi

mkdir -p "$BASE"
tmux new-session -d -s "$SWE_SESSION" \
  "bash $CONFIG/run_with_status.sh $CONFIG/run_sweagent_verified.sh $IDS $BASE/sweagent"
tmux new-session -d -s "$MINI_SESSION" \
  "bash $CONFIG/run_with_status.sh $CONFIG/run_mini_verified.sh $IDS $BASE/mini"
tmux new-session -d -s verified50-auto-eval \
  "bash $CONFIG/auto_eval_verified50.sh > $BASE/auto_eval.log 2>&1"

echo "Started both 50-instance runs at temperature 0.7."
echo "SWE-agent: tmux attach -t $SWE_SESSION"
echo "mini-SWE-agent: tmux attach -t $MINI_SESSION"
echo "Logs: $BASE/{sweagent,mini}/run.log"
echo "Exit codes after completion: $BASE/{sweagent,mini}/exit_code"
echo "Automatic Harness evaluation: tmux attach -t verified50-auto-eval"
echo "Evaluation log: $BASE/auto_eval.log"
echo "Keep the Windows PC and both SSH tunnels online throughout the runs."
