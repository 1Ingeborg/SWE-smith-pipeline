#!/usr/bin/env bash
set -u

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 IDS_FILE OUTPUT_DIR API_PORT" >&2
  exit 2
fi

IDS=$1
OUT=$2
PORT=$3
if [[ $PORT != 8000 && $PORT != 8001 ]]; then
  echo "API_PORT must be 8000 or 8001" >&2
  exit 2
fi

mkdir -p "$OUT"
date -Is > "$OUT/started_at"
bash /data/configs/verified50/run_mini_verified_port.sh "$IDS" "$OUT" "$PORT" > "$OUT/run.log" 2>&1
STATUS=$?
printf '%s\n' "$STATUS" > "$OUT/exit_code"
date -Is > "$OUT/finished_at"
echo "GPU mini worker on port $PORT finished with exit code $STATUS; log: $OUT/run.log"
exit "$STATUS"
