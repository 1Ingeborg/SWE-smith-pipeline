#!/usr/bin/env bash
#
# Historical problem-statement generation only (no Agent execution).
#
#   tools/legacy/run-problem-statements.sh --config configs/issue_gen/<name>.conf
#
# All code and configuration live in this repository; RUN_DIR under
# /data/results holds only data.
#
# Changes from the first version:
#   * reads a filtered dataset and derives every expected count from it,
#     instead of hard-coding 1132 (six tasks whose only FAIL_TO_PASS entries
#     are non-Python files were removed upstream, see excluded-no-python-f2p.json)
#   * tolerates tasks skipped during preparation: the invariant checked is now
#     "prepared + skipped == dataset", not "prepared == 1132"
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PRESET_DATA_ROOT="${SWE_LAB_DATA_ROOT:-}"
PRESET_DEEPSEEK_KEY="${DEEPSEEK_API_KEY:-}"

CONFIG=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --config) CONFIG="$2"; shift 2 ;;
        --config=*) CONFIG="${1#*=}"; shift ;;
        -h|--help) echo "usage: $0 --config <file.conf>"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 64 ;;
    esac
done
[[ -n "$CONFIG" ]] || { echo "--config is required" >&2; exit 64; }
[[ -f "$CONFIG" ]] || CONFIG="$REPO/$CONFIG"
[[ -f "$CONFIG" ]] || { echo "config not found: $CONFIG" >&2; exit 66; }

# shellcheck disable=SC1090
source "$CONFIG"

FULL="$RUN_DIR"
ENV_FILE="$REPO/.env"
DATASET="$FULL/$DATASET_NAME"
PREPARED="$FULL/prepared.jsonl"
FAILURES="$FULL/prepared.jsonl.failures.jsonl"
OUTPUT="$FULL/problem-statements.jsonl"
SUMMARY="$FULL/problem-statement-summary.json"
LOG_DIR="$FULL/logs"

cd "$REPO"
mkdir -p "$LOG_DIR"

if [[ ! -f "$DATASET" ]]; then
    echo "Missing dataset: $DATASET" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
[[ -z "$PRESET_DATA_ROOT" ]] || SWE_LAB_DATA_ROOT="$PRESET_DATA_ROOT"
[[ -z "$PRESET_DEEPSEEK_KEY" ]] || DEEPSEEK_API_KEY="$PRESET_DEEPSEEK_KEY"
SWE_LAB_DATA_ROOT="${SWE_LAB_DATA_ROOT:-$REPO/.local}"
case "$SWE_LAB_DATA_ROOT" in /*) ;; *) SWE_LAB_DATA_ROOT="$REPO/$SWE_LAB_DATA_ROOT" ;; esac
PY="$SWE_LAB_DATA_ROOT/venvs/swesmith-lab-core/bin/python"
[[ -x "$PY" ]] || { echo "Missing core Python environment: $PY" >&2; exit 1; }

API_KEY_VALUE="${!DEEPSEEK_API_KEY_ENV:-}"
if [[ -z "$API_KEY_VALUE" ]]; then
    echo "Missing API key environment variable: $DEEPSEEK_API_KEY_ENV" >&2
    exit 1
fi
unset API_KEY_VALUE

count_jsonl() {
    awk 'NF { count += 1 } END { print count + 0 }' "$1"
}

count_failures() {
    if [[ -f "$FAILURES" ]]; then
        count_jsonl "$FAILURES"
    else
        echo 0
    fi
}

SOURCE_COUNT=$(count_jsonl "$DATASET")
if [[ "$SOURCE_COUNT" -lt 1 ]]; then
    echo "Dataset is empty: $DATASET" >&2
    exit 1
fi

echo "DeepSeek problem-statement run"
echo "Dataset: $DATASET ($SOURCE_COUNT tasks)"
echo "Model: $DEEPSEEK_MODEL"
echo "Base URL: $DEEPSEEK_BASE_URL"
echo "Thinking mode: $DEEPSEEK_THINKING_MODE"
echo "Workers: $PS_WORKERS"
echo "Max output tokens: $PS_MAX_OUTPUT_TOKENS"
echo "Max failing tests: $PS_MAX_FAILING_TESTS"
echo "Max rewrites: $PS_MAX_REWRITES"
echo "Agent execution: disabled"

if [[ -f "$PREPARED" ]]; then
    PREPARED_COUNT=$(count_jsonl "$PREPARED")
    FAILED_COUNT=$(count_failures)
    if [[ $((PREPARED_COUNT + FAILED_COUNT)) -ne "$SOURCE_COUNT" ]]; then
        echo "Prepared file is incomplete ($PREPARED_COUNT ok + $FAILED_COUNT skipped of $SOURCE_COUNT)." >&2
        echo "Preparation cannot resume safely; move the partial files aside and rerun." >&2
        exit 1
    fi
    echo "Reusing prepared input: $PREPARED ($PREPARED_COUNT tasks, $FAILED_COUNT skipped)"
else
    echo "Preparing private failure evidence..."
    "$PY" src/swesmith_lab/issuegen/generate.py \
        "$PREPARED" \
        --dataset "$DATASET" \
        --validation-dir "$VALIDATION_DIR" \
        --prepare-only \
        --max-failing-tests "$PS_MAX_FAILING_TESTS" \
        2>&1 | tee "$LOG_DIR/01-prepare-inputs.log"
fi

PREPARED_COUNT=$(count_jsonl "$PREPARED")
FAILED_COUNT=$(count_failures)
if [[ $((PREPARED_COUNT + FAILED_COUNT)) -ne "$SOURCE_COUNT" ]]; then
    echo "Prepared count mismatch: $PREPARED_COUNT ok + $FAILED_COUNT skipped != $SOURCE_COUNT" >&2
    exit 1
fi
if [[ "$FAILED_COUNT" -gt 0 ]]; then
    echo "WARNING: $FAILED_COUNT tasks were skipped during preparation:"
    cat "$FAILURES"
fi

# Generation targets the tasks that actually prepared, not the dataset size.
TARGET_COUNT="$PREPARED_COUNT"

GENERATION_ARGS=()
TEE_ARGS=()
if [[ -f "$OUTPUT" ]]; then
    OUTPUT_COUNT=$(count_jsonl "$OUTPUT")
    if [[ "$OUTPUT_COUNT" -eq "$TARGET_COUNT" ]]; then
        echo "Problem statements already complete: $OUTPUT_COUNT/$TARGET_COUNT"
    elif [[ "$OUTPUT_COUNT" -lt "$TARGET_COUNT" ]]; then
        echo "Resuming problem statements after $OUTPUT_COUNT completed tasks..."
        GENERATION_ARGS+=(--resume)
        TEE_ARGS+=(-a)
    else
        echo "Output contains more rows than prepared input: $OUTPUT_COUNT/$TARGET_COUNT" >&2
        exit 1
    fi
else
    OUTPUT_COUNT=0
fi

if [[ "$OUTPUT_COUNT" -ne "$TARGET_COUNT" ]]; then
    "$PY" src/swesmith_lab/issuegen/generate.py \
        "$OUTPUT" \
        --prepared-input "$PREPARED" \
        --generator-model "$DEEPSEEK_MODEL" \
        --leakage-reviewer-model "$DEEPSEEK_MODEL" \
        --factuality-reviewer-model "$DEEPSEEK_MODEL" \
        --base-url "$DEEPSEEK_BASE_URL" \
        --api-key-env "$DEEPSEEK_API_KEY_ENV" \
        --max-output-tokens "$PS_MAX_OUTPUT_TOKENS" \
        --max-failing-tests "$PS_MAX_FAILING_TESTS" \
        --max-rewrites "$PS_MAX_REWRITES" \
        --workers "$PS_WORKERS" \
        "${GENERATION_ARGS[@]}" \
        2>&1 | tee "${TEE_ARGS[@]}" "$LOG_DIR/02-generate-and-review.log"
fi

FINAL_COUNT=$(count_jsonl "$OUTPUT")
if [[ "$FINAL_COUNT" -ne "$TARGET_COUNT" ]]; then
    echo "Problem-statement count mismatch: $FINAL_COUNT/$TARGET_COUNT" >&2
    exit 1
fi

"$PY" - "$OUTPUT" "$SUMMARY" <<'PY'
import collections
import json
import pathlib
import sys

source = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
rows = [
    json.loads(line)
    for line in source.read_text(encoding="utf-8").splitlines()
    if line.strip()
]

statuses = collections.Counter(row["review_status"] for row in rows)
usages = []
for row in rows:
    for draft in row.get("drafts", []):
        if draft.get("usage"):
            usages.append(draft["usage"])
    for round_ in row.get("review_rounds", []):
        for reviewer in ("leakage", "factuality"):
            usage = (round_.get(reviewer) or {}).get("usage")
            if usage:
                usages.append(usage)

prompt_tokens = sum(int(item.get("prompt_tokens", 0) or 0) for item in usages)
completion_tokens = sum(int(item.get("completion_tokens", 0) or 0) for item in usages)
latency_seconds = sum(float(item.get("latency_seconds", 0) or 0) for item in usages)

# Conservative cache-miss estimates from the 2026-09-18 DeepSeek price table.
offpeak_usd = (prompt_tokens * 0.15 + completion_tokens * 0.60) / 1_000_000
peak_usd = (prompt_tokens * 0.30 + completion_tokens * 1.20) / 1_000_000
usd_to_cny = 6.758

summary = {
    "schema_version": 1,
    "tasks": len(rows),
    "review_status_counts": dict(sorted(statuses.items())),
    "strict_acceptance_rate": statuses.get("accepted", 0) / len(rows) if rows else 0,
    "accepted_or_needs_review_rate": (
        statuses.get("accepted", 0) + statuses.get("needs_review", 0)
    ) / len(rows) if rows else 0,
    "generation_attempts": sum(int(row.get("generation_attempts", 0)) for row in rows),
    "api_calls": len(usages),
    "prompt_tokens": prompt_tokens,
    "completion_tokens": completion_tokens,
    "total_tokens": prompt_tokens + completion_tokens,
    "summed_api_latency_seconds": round(latency_seconds, 3),
    "pricing_assumption": "all input tokens treated as cache misses",
    "estimated_cost_offpeak_usd": round(offpeak_usd, 6),
    "estimated_cost_peak_usd": round(peak_usd, 6),
    "estimated_cost_offpeak_cny": round(offpeak_usd * usd_to_cny, 4),
    "estimated_cost_peak_cny": round(peak_usd * usd_to_cny, 4),
    "usd_to_cny": usd_to_cny,
}

destination.write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(json.dumps(summary, ensure_ascii=False, indent=2))
PY

echo "Problem-statement generation completed."
echo "Output: $OUTPUT"
echo "Summary: $SUMMARY"
