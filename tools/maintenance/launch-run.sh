#!/usr/bin/env bash
# Maintenance helper: run a driver inside tmux and tee its output to a log file.
# Report the driver's
# own exit status rather than tee's, and keep the shell alive afterwards so the
# output stays readable.
#
#   tools/maintenance/launch-run.sh <log-file> <command> [args...]
#
# Example:
#   tools/maintenance/launch-run.sh /data/results/.../logs/00-driver.log \
#       bash scripts/run-agent.sh --config configs/rollout/smoke.yaml --stage prepare --dry-run

if [ $# -lt 2 ]; then
    echo "usage: $0 <log-file> <command> [args...]" >&2
    exit 64
fi

LOG="$1"
shift
mkdir -p "$(dirname "$LOG")"

"$@" 2>&1 | tee "$LOG"
echo "EXIT=${PIPESTATUS[0]}"
exec bash
