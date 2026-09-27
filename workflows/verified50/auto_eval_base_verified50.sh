#!/usr/bin/env bash
set -euo pipefail

CONFIG=/data/configs/verified50
BASE=/data/results/swebench-eval/verified50-dual-base-bf16-t07-20260925/formal
EVAL=$BASE/harness
IDS_FILE=$CONFIG/verified_50_ids.txt
PY=/data/venvs/swebench-eval/bin/python

mkdir -p "$EVAL"
echo "$(date -Is) Waiting for both Base-model generation runs to finish."
while [[ ! -f "$BASE/sweagent/exit_code" || ! -f "$BASE/mini/exit_code" ]]; do
  sleep 60
done

SWE_EXIT=$(<"$BASE/sweagent/exit_code")
MINI_EXIT=$(<"$BASE/mini/exit_code")
echo "$(date -Is) Generation exit codes: SWE-agent=$SWE_EXIT mini-SWE-agent=$MINI_EXIT"
if [[ $SWE_EXIT != 0 || $MINI_EXIT != 0 ]]; then
  echo "Generation failed; Harness evaluation was not started." | tee "$EVAL/BLOCKED"
  exit 1
fi

for PREDS in "$BASE/sweagent/preds.json" "$BASE/mini/preds.json"; do
  if [[ ! -s "$PREDS" ]]; then
    echo "Missing or empty $PREDS; Harness evaluation was not started." | tee "$EVAL/BLOCKED"
    exit 1
  fi
done

"$PY" - "$IDS_FILE" "$BASE/sweagent/preds.json" "$BASE/mini/preds.json" <<'PY'
import json
import sys
from pathlib import Path

ids = set(Path(sys.argv[1]).read_text().splitlines())
if len(ids) != 50:
    raise SystemExit("Frozen instance list does not contain 50 unique IDs")

for path in map(Path, sys.argv[2:]):
    preds = json.loads(path.read_text())
    if not isinstance(preds, dict):
        raise SystemExit(f"Expected a prediction object in {path}")
    extra = set(preds) - ids
    if extra:
        raise SystemExit(f"Unexpected instance IDs in {path}: {sorted(extra)}")
    malformed = [iid for iid, entry in preds.items()
                 if not isinstance(entry, dict)
                 or "model_patch" not in entry
                 or (entry["model_patch"] is not None
                     and not isinstance(entry["model_patch"], str))]
    if malformed:
        raise SystemExit(f"Malformed predictions in {path}: {malformed}")
    nonempty = sum(bool((entry["model_patch"] or "").strip())
                   for entry in preds.values())
    print(f"{path}: predictions={len(preds)}/50, nonempty={nonempty}, "
          f"empty={len(preds)-nonempty}, missing={50-len(preds)}", flush=True)
PY

free_gb() {
  df -BG --output=avail /data | tail -n 1 | tr -dc '0-9'
}

FREE_GB=$(free_gb)
if [[ -z "$FREE_GB" || "$FREE_GB" -lt 10 ]]; then
  echo "Only ${FREE_GB:-unknown} GB free on /data; Harness evaluation was not started." | tee "$EVAL/BLOCKED"
  exit 1
fi

mapfile -t IDS < <(tr -d '\r' < "$IDS_FILE")
export HF_HOME=/data/huggingface
export HF_HUB_OFFLINE=1
export HF_DATASETS_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

run_eval() {
  local agent=$1
  local run_id=$2
  local preds=$3
  local out=$EVAL/$agent
  local code
  mkdir -p "$out"
  echo "$(date -Is) Starting SWE-bench Harness for $agent ($FREE_GB GB free)."
  set +e
  "$PY" -m swebench.harness.run_evaluation \
    --dataset_name SWE-bench/SWE-bench_Verified \
    --split test \
    --instance_ids "${IDS[@]}" \
    --predictions_path "$preds" \
    --run_id "$run_id" \
    --max_workers 1 \
    --report_dir "$out" > "$out/eval.log" 2>&1
  code=$?
  set -e
  printf '%s\n' "$code" > "$out/exit_code"
  echo "$(date -Is) $agent Harness exit code=$code; log=$out/eval.log"
  return "$code"
}

SWE_EVAL=0
MINI_EVAL=0
run_eval sweagent verified50-swe-base-bf16-t07-20260925 "$BASE/sweagent/preds.json" || SWE_EVAL=$?
FREE_GB=$(free_gb)
if [[ -z "$FREE_GB" || "$FREE_GB" -lt 10 ]]; then
  echo "Only ${FREE_GB:-unknown} GB free before mini evaluation." | tee "$EVAL/BLOCKED"
  exit 1
fi
run_eval mini verified50-mini-base-bf16-t07-20260925 "$BASE/mini/preds.json" || MINI_EVAL=$?

if [[ $SWE_EVAL -ne 0 || $MINI_EVAL -ne 0 ]]; then
  echo "At least one Harness run failed; inspect its eval.log." | tee "$EVAL/FAILED"
  exit 1
fi

date -Is > "$EVAL/COMPLETED"
echo "Both Harness evaluations completed. Reports are in $EVAL/{sweagent,mini}."
