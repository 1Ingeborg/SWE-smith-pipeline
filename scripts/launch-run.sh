#!/usr/bin/env bash
# Run a driver inside tmux: tee its output to a log file, report the driver's
# own exit status rather than tee's, and keep the shell alive afterwards so the
# output stays readable.
#
#   scripts/launch-run.sh <log-file> <command> [args...]
#
# Example:
#   scripts/launch-run.sh /data/results/.../logs/00-driver.log \
#       ./scripts/run-agent-rollout.sh --config configs/experiments/x.conf all

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
