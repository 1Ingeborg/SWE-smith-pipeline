#!/usr/bin/env bash
# Drive the three Agent rollout stages: prepare -> agent -> eval.
#
#   scripts/run-agent-rollout.sh --config configs/experiments/<name>.conf [stage]
#
# stage is all (default), prepare, agent or eval. Every stage is restartable:
# rerun the same command and it resumes from where it stopped.
#
# All code and configuration live in this repository; RUN_DIR under /data/results
# holds only data (inputs, images manifest, trajectories, reports, logs).
set -euo pipefail

LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY=/data/venvs/swesmith/bin/python

CONFIG=""
STAGE="all"
while [ $# -gt 0 ]; do
    case "$1" in
        --config) CONFIG="$2"; shift 2 ;;
        --config=*) CONFIG="${1#*=}"; shift ;;
        all|prepare|agent|eval) STAGE="$1"; shift ;;
        -h|--help)
            echo "usage: $0 --config <file.conf> [all|prepare|agent|eval]"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 64 ;;
    esac
done
[ -n "$CONFIG" ] || { echo "--config is required" >&2; exit 64; }
[ -f "$CONFIG" ] || CONFIG="$LAB/$CONFIG"
[ -f "$CONFIG" ] || { echo "config not found: $CONFIG" >&2; exit 66; }

# shellcheck disable=SC1090
. "$CONFIG"

# Resolve repository-relative config paths.
case "$PREPARE_CONFIG" in /*) ;; *) PREPARE_CONFIG="$LAB/$PREPARE_CONFIG" ;; esac
case "$AGENT_CONFIG" in /*) ;; *) AGENT_CONFIG="$LAB/$AGENT_CONFIG" ;; esac

PREP_DIR="$RUN_DIR/agent-task-prep/$PREP_ID"
INSTANCES="$PREP_DIR/public/instances.jsonl"
SELECTED="$PREP_DIR/private/selected.jsonl"
ROLLOUT_DIR="$RUN_DIR/agent-runs/$ROLLOUT_ID"
PREDS="$ROLLOUT_DIR/preds.json"
LOGS="$RUN_DIR/logs"
mkdir -p "$LOGS"

stamp() { date "+%Y-%m-%d %H:%M:%S"; }
has_files() { [ -d "$1" ] && [ -n "$(ls -A "$1" 2>/dev/null)" ]; }

# DeepSeek charges half price from 00:30 to 08:30 Asia/Shanghai.
now_minutes() { echo $((10#$(date +%H) * 60 + 10#$(date +%M))); }
in_offpeak() {
    local minutes; minutes=$(now_minutes)
    [ "$minutes" -ge 30 ] && [ "$minutes" -lt 510 ]
}
minutes_until_offpeak() { echo $(( (1470 - $(now_minutes)) % 1440 )); }

wait_for_offpeak() {
    local wait_minutes; wait_minutes=$(minutes_until_offpeak)
    if [ "$wait_minutes" -gt "$MAX_OFFPEAK_WAIT_MINUTES" ]; then
        echo "[$(stamp)] off-peak is ${wait_minutes} min away, over the ${MAX_OFFPEAK_WAIT_MINUTES} min limit." >&2
        echo "  Start the agent stage yourself after 00:30, or override with FORCE_PEAK=1." >&2
        return 2
    fi
    echo "[$(stamp)] peak pricing now; sleeping ${wait_minutes} min until the 00:30 off-peak window"
    sleep $(( wait_minutes * 60 + 60 ))
}

stage_prepare() {
    local extra=()
    if [ -f "$PREP_DIR/private/run-metadata.json" ]; then
        extra+=(--resume)
        echo "[$(stamp)] prepare: resuming existing selection"
    else
        echo "[$(stamp)] prepare: fresh start ($EXPECTED_TASKS tasks, est. 2.2-3.3h)"
    fi
    "$PY" "$LAB/scripts/prepare-agent-pilot.py" \
        --config "$PREPARE_CONFIG" \
        --run-id "$PREP_ID" \
        "${extra[@]}" 2>&1 | tee -a "$LOGS/01-prepare.log"

    local ready; ready=$(wc -l < "$INSTANCES")
    echo "[$(stamp)] prepare done: $ready public instances"
    if [ "$ready" -lt "$EXPECTED_TASKS" ]; then
        echo "WARNING: only $ready/$EXPECTED_TASKS images ready. Rerun 'prepare' to continue." >&2
        return 1
    fi
}

stage_agent() {
    local allow_wait="${1:-0}"
    [ -f "$INSTANCES" ] || { echo "Missing $INSTANCES; run prepare first." >&2; return 1; }

    if [ "${FORCE_PEAK:-0}" != "1" ] && ! in_offpeak; then
        if [ "$allow_wait" = "1" ]; then
            wait_for_offpeak || return 2
        else
            cat >&2 <<MSG
[$(stamp)] REFUSING TO START: currently DeepSeek peak pricing (double rate).
  Off-peak window: 00:30-08:30 Asia/Shanghai ($(minutes_until_offpeak) min away)
  Estimated cost for $EXPECTED_TASKS tasks: ~CNY 21 off-peak / ~CNY 42 peak

  To override deliberately:  FORCE_PEAK=1 $0 --config $CONFIG agent
MSG
            return 2
        fi
    fi

    local extra=()
    if has_files "$ROLLOUT_DIR"; then
        extra+=(--resume)
        echo "[$(stamp)] agent: resuming existing run"
    fi
    echo "[$(stamp)] agent: starting $(wc -l < "$INSTANCES") instances, $WORKERS_AGENT workers"
    "$PY" "$LAB/scripts/run-agent-pilot.py" \
        --instances "$INSTANCES" \
        --run-id "$ROLLOUT_ID" \
        --output-root "$RUN_DIR/agent-runs" \
        --sweagent-root "$SWEAGENT_ROOT" \
        --sweagent-executable "$SWEAGENT_BIN" \
        --mode model \
        --allow-api-calls \
        --agent-config "$AGENT_CONFIG" \
        --model-name "$MODEL_NAME" \
        --api-base "$API_BASE" \
        --api-key-env "$API_KEY_ENV" \
        --env-file "$LAB/.env" \
        --workers "$WORKERS_AGENT" \
        --per-instance-call-limit "$PER_INSTANCE_CALL_LIMIT" \
        --per-instance-cost-limit "$PER_INSTANCE_COST_LIMIT" \
        --total-cost-limit "$TOTAL_COST_LIMIT" \
        "${extra[@]}" 2>&1 | tee -a "$LOGS/02-agent.log"
    echo "[$(stamp)] agent done -> $PREDS"
}

stage_eval() {
    [ -f "$PREDS" ] || { echo "Missing $PREDS; run agent first." >&2; return 1; }
    local extra=()
    if has_files "$RUN_DIR/evaluations/$EVAL_ID"; then
        extra+=(--resume)
        echo "[$(stamp)] eval: resuming existing evaluation"
    fi
    echo "[$(stamp)] eval: starting, $WORKERS_EVAL workers (no API cost)"
    "$PY" "$LAB/scripts/evaluate-agent-predictions.py" \
        --dataset "$SELECTED" \
        --predictions "$PREDS" \
        --run-id "$EVAL_ID" \
        --output-root "$RUN_DIR/evaluations" \
        --workers "$WORKERS_EVAL" \
        "${extra[@]}" 2>&1 | tee -a "$LOGS/03-eval.log"
    echo "[$(stamp)] eval done -> $RUN_DIR/evaluations/$EVAL_ID/report.json"
}

case "$STAGE" in
    prepare) stage_prepare ;;
    agent)   stage_agent 0 ;;
    eval)    stage_eval ;;
    all)     stage_prepare && stage_agent 1 && stage_eval ;;
esac
