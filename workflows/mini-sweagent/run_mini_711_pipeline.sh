#!/usr/bin/env bash
set -euo pipefail

run=${MINI_RUN_DIR:-/data/results/full-runs/deepseek-mini-711-250step-20260923}
source_run=/data/results/full-runs/deepseek-agent-741-20260918
workers=${MINI_WORKERS:-2}
eval_workers=${MINI_EVAL_WORKERS:-2}

mkdir -p "$run" "$run/config" "$run/rollout" "$run/evaluation" "$run/training/native"
cp /data/configs/mini-sweagent/deepseek_mini_override.yaml "$run/config/deepseek_mini_override.yaml"
cp /data/configs/mini-sweagent/convert_mini_trajs_to_sft.py "$run/config/convert_mini_trajs_to_sft.py"

set -a
source /data/repos/swe-smith-lab/.env
set +a
unset MSWEA_GLOBAL_COST_LIMIT
unset MSWEA_GLOBAL_CALL_LIMIT

source /data/venvs/mini-sweagent/bin/activate
mini-extra swebench \
  --subset "$source_run/agent-task-prep/accepted-741/public" \
  --split train \
  --output "$run/rollout" \
  --workers "$workers" \
  --config swebench_backticks.yaml \
  --config "$run/config/deepseek_mini_override.yaml"

/data/venvs/swesmith/bin/python /data/repos/swe-smith-lab/src/swesmith_lab/agent/evaluate.py \
  --dataset "$source_run/agent-task-prep/accepted-741/private/selected.jsonl" \
  --predictions "$run/rollout/preds.json" \
  --run-id mini-eval \
  --output-root "$run/evaluation" \
  --workers "$eval_workers" \
  --memory-limit 4g \
  --timeout-seconds 120 \
  --require-all \
  --resume

/data/venvs/swesmith/bin/python "$run/config/convert_mini_trajs_to_sft.py" \
  --traj-dir "$run/rollout" \
  --eval-dir "$run/evaluation/mini-eval" \
  --instances "$source_run/agent-task-prep/accepted-741/public/instances.jsonl" \
  --agent-config /data/repos/SWE-smith/agent/swesmith_infer.yaml \
  --style native \
  --out-dir "$run/training/native"
