#!/usr/bin/env bash
# Run the LoRA SWE-bench Verified eval against the AutoDL endpoint.
#
# Env overrides: RUN_DIR (required), INSTANCE_FILTER, INSTANCE_SLICE,
# TEMPERATURE, TOP_P, CALL_LIMIT, and SWE_LOOP_GUARD=0 to disable the guard.
set -euo pipefail

run=${RUN_DIR:?set RUN_DIR}
filter=${INSTANCE_FILTER:-.*}
slice_=${INSTANCE_SLICE:-:10}
temp=${TEMPERATURE:-0.6}
top_p=${TOP_P:-0.95}
call_limit=${CALL_LIMIT:-75}

mkdir -p "$run"
cd /data/repos/SWE-agent-1.1.0
unset DOCKER_BUILDKIT
export HF_HUB_OFFLINE=1
export HF_DATASETS_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

echo "guard=${SWE_LOOP_GUARD:-1} temperature=$temp top_p=$top_p call_limit=$call_limit filter=$filter slice=$slice_"

exec /data/venvs/sweagent/bin/sweagent run-batch \
  --config /data/repos/SWE-smith/agent/swesmith_infer.yaml \
  --instances.path_override=/data/datasets/swebench-verified-local \
  --instances.filter="$filter" \
  --instances.slice="$slice_" \
  --instances.deployment.docker_args=--memory=10g \
  --agent.tools.env_variables='{"USE_FILEMAP":"true","GIT_PAGER":"cat","PAGER":"cat","TERM":"dumb"}' \
  --agent.model.name=openai/swe-lora \
  --agent.model.api_base=http://127.0.0.1:8000/v1 \
  --agent.model.api_key=swesmith \
  --agent.model.temperature="$temp" \
  --agent.model.top_p="$top_p" \
  --agent.model.per_instance_cost_limit=0 \
  --agent.model.total_cost_limit=0 \
  --agent.model.per_instance_call_limit="$call_limit" \
  --agent.model.max_input_tokens=16384 \
  --agent.model.max_output_tokens=4096 \
  --agent.model.completion_kwargs='{"max_tokens":4096}' \
  --num_workers=1 \
  --random_delay_multiplier=0 \
  --output_dir="$run"
