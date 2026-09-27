#!/usr/bin/env bash
# Host prerequisite check.
set -euo pipefail

DATA_ROOT="${DATA_ROOT:-/data}"
failed=0

check() {
  local label="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    printf '[OK]   %s\n' "$label"
  else
    printf '[FAIL] %s\n' "$label"
    failed=1
  fi
}

printf 'Host: %s\n' "$(hostname)"
printf 'OS:   %s\n' "$(. /etc/os-release && echo "$PRETTY_NAME")"
printf 'Arch: %s\n' "$(uname -m)"
printf 'CPU:  %s vCPU\n' "$(nproc)"
free -h | sed -n '1,2p'
df -hT / "$DATA_ROOT" 2>/dev/null || true

check 'x86_64 architecture' test "$(uname -m)" = x86_64
check "$DATA_ROOT is a mount point" mountpoint -q "$DATA_ROOT"
check 'at least 8 vCPU' test "$(nproc)" -ge 8
check 'Git installed' command -v git
check 'Python 3 installed' command -v python3
check 'Docker installed' command -v docker
check 'Docker daemon reachable' docker info

if (( failed )); then
  exit 1
fi
