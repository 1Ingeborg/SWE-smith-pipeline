#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 IDS_FILE OUTPUT_DIR" >&2
  exit 2
fi

IDS_FILE=$1
OUT=$2
FILTER=$(tr -d '\r' < "$IDS_FILE" | paste -sd '|' -)
FILTER="^(${FILTER})$"
if [[ -z "$FILTER" || "$FILTER" == '^()$' ]]; then
  echo "Empty instance selection" >&2
  exit 1
fi

if ! curl -fsS --max-time 5 -H 'Authorization: Bearer swesmith' \
  http://127.0.0.1:8000/v1/models > /dev/null; then
  echo "SWE-agent LoRA API on port 8000 is unavailable" >&2
  exit 1
fi

mkdir -p "$OUT"
cd /data/repos/SWE-agent-1.1.0
unset DOCKER_BUILDKIT
export HF_HUB_OFFLINE=1
export HF_DATASETS_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export SWE_LOOP_GUARD=1

echo "SWE-agent: $(wc -l < "$IDS_FILE") fixed Verified IDs, temperature=0.7, output=$OUT"
/data/venvs/sweagent/bin/sweagent run-batch \
  --config /data/repos/SWE-smith/agent/swesmith_infer.yaml \
  --instances.subset=verified \
  --instances.split=test \
  --instances.path_override=/data/datasets/swebench-verified-local \
  --instances.filter="$FILTER" \
  --instances.deployment.docker_args=--memory=4g \
  --agent.tools.env_variables='{"USE_FILEMAP":"true","GIT_PAGER":"cat","PAGER":"cat","TERM":"dumb"}' \
  --agent.model.name=openai/swe-lora \
  --agent.model.api_base=http://127.0.0.1:8000/v1 \
  --agent.model.api_key=swesmith \
  --agent.model.temperature=0.7 \
  --agent.model.per_instance_cost_limit=0 \
  --agent.model.total_cost_limit=0 \
  --agent.model.per_instance_call_limit=75 \
  --agent.model.max_input_tokens=16384 \
  --agent.model.max_output_tokens=4096 \
  --agent.model.completion_kwargs='{"max_tokens":4096}' \
  --num_workers=1 \
  --random_delay_multiplier=0 \
  --output_dir="$OUT"
