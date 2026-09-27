#!/usr/bin/env bash
set -u

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 AGENT_SCRIPT IDS_FILE OUTPUT_DIR" >&2
  exit 2
fi

SCRIPT=$1
IDS=$2
OUT=$3
mkdir -p "$OUT"
date -Is > "$OUT/started_at"
bash "$SCRIPT" "$IDS" "$OUT" > "$OUT/run.log" 2>&1
STATUS=$?
printf '%s\n' "$STATUS" > "$OUT/exit_code"
date -Is > "$OUT/finished_at"
echo "Finished $(basename "$SCRIPT") with exit code $STATUS; log: $OUT/run.log"
exit "$STATUS"
