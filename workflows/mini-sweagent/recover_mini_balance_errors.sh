#!/usr/bin/env bash
set -euo pipefail

run=/data/results/full-runs/deepseek-mini-711-250step-20260923
source_run=/data/results/full-runs/deepseek-agent-741-20260918
rollout="$run/rollout"
recovery="$run/recovery"
original_pipeline_pid=697094

while [[ -r "/proc/$original_pipeline_pid/cmdline" ]] &&
  tr '\0' ' ' < "/proc/$original_pipeline_pid/cmdline" | grep -Fq 'run_mini_711_pipeline.sh'; do
  sleep 30
done

mkdir -p "$recovery/subset" "$recovery/original_trajs"

/data/venvs/mini-sweagent/bin/python - "$run" "$source_run" <<'PY'
import json
import shutil
import sys
from pathlib import Path

run, source_run = map(Path, sys.argv[1:])
rollout = run / 'rollout'
recovery = run / 'recovery'
source_instances = source_run / 'agent-task-prep/accepted-741/public/instances.jsonl'
instances = [json.loads(line) for line in source_instances.read_text(encoding='utf-8').splitlines() if line.strip()]
preds_path = rollout / 'preds.json'
preds = json.loads(preds_path.read_text(encoding='utf-8')) if preds_path.exists() else {}
selected = []
reasons = {}
for instance in instances:
    iid = instance['instance_id']
    traj_path = rollout / iid / f'{iid}.traj.json'
    if not traj_path.exists() or iid not in preds:
        reason = 'missing_result'
    else:
        info = json.loads(traj_path.read_text(encoding='utf-8')).get('info') or {}
        reason = 'BadRequestError' if info.get('exit_status') == 'BadRequestError' else None
    if reason:
        selected.append(instance)
        reasons[iid] = reason
        if traj_path.exists():
            shutil.copy2(traj_path, recovery / 'original_trajs' / f'{iid}.traj.json')

shutil.copy2(preds_path, recovery / 'preds.before_recovery.json')
(recovery / 'subset/instances.jsonl').write_text(
    ''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in selected), encoding='utf-8'
)
(recovery / 'selected.json').write_text(
    json.dumps({'selected_count': len(selected), 'reasons': reasons}, ensure_ascii=False, indent=2) + '\n',
    encoding='utf-8',
)
print(f'Selected {len(selected)} of {len(instances)} instances for recovery', flush=True)
PY

selected_count=$(/data/venvs/mini-sweagent/bin/python -c \
  "import json; print(json.load(open('$recovery/selected.json'))['selected_count'])")
if (( selected_count > 0 )); then
  set -a
  source /data/repos/swe-smith-lab/.env
  set +a
  unset MSWEA_GLOBAL_COST_LIMIT MSWEA_GLOBAL_CALL_LIMIT
  source /data/venvs/mini-sweagent/bin/activate
  mini-extra swebench \
    --subset "$recovery/subset" \
    --split train \
    --output "$rollout" \
    --workers 6 \
    --redo-existing \
    --config swebench_backticks.yaml \
    --config "$run/config/deepseek_mini_override.yaml"
fi

/data/venvs/mini-sweagent/bin/python - "$run" "$source_run" <<'PY'
import json
import sys
from collections import Counter
from pathlib import Path

run, source_run = map(Path, sys.argv[1:])
expected = [json.loads(line)['instance_id'] for line in
            (source_run / 'agent-task-prep/accepted-741/public/instances.jsonl').read_text(encoding='utf-8').splitlines()
            if line.strip()]
selected = json.loads((run / 'recovery/selected.json').read_text(encoding='utf-8'))['reasons']
preds = json.loads((run / 'rollout/preds.json').read_text(encoding='utf-8'))
statuses = Counter()
still_failed = []
for iid in expected:
    path = run / 'rollout' / iid / f'{iid}.traj.json'
    if iid not in preds or not path.exists():
        status = 'missing_result'
    else:
        status = str((json.loads(path.read_text(encoding='utf-8')).get('info') or {}).get('exit_status'))
    statuses[status] += 1
    if iid in selected and status in {'BadRequestError', 'missing_result'}:
        still_failed.append(iid)
summary = {'expected': len(expected), 'predictions': len(preds), 'statuses': dict(statuses),
           'selected_for_recovery': len(selected), 'still_failed': still_failed}
(run / 'recovery/status.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
print(json.dumps(summary, indent=2), flush=True)
if len(preds) != len(expected) or still_failed:
    raise SystemExit('Recovery incomplete; evaluation has not been restarted')
PY

/data/venvs/swesmith/bin/python /data/repos/swe-smith-lab/src/swesmith_lab/agent/evaluate.py \
  --dataset "$source_run/agent-task-prep/accepted-741/private/selected.jsonl" \
  --predictions "$rollout/preds.json" \
  --run-id mini-eval-recovered \
  --output-root "$run/evaluation" \
  --workers 4 \
  --memory-limit 4g \
  --timeout-seconds 120 \
  --require-all \
  --resume

/data/venvs/swesmith/bin/python "$run/config/convert_mini_trajs_to_sft.py" \
  --traj-dir "$rollout" \
  --eval-dir "$run/evaluation/mini-eval-recovered" \
  --instances "$source_run/agent-task-prep/accepted-741/public/instances.jsonl" \
  --agent-config /data/repos/SWE-smith/agent/swesmith_infer.yaml \
  --style native \
  --out-dir "$run/training/native-recovered"
