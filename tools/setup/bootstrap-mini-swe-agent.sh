#!/usr/bin/env bash
# Install the vendored mini-swe-agent 2.4.6 source in a separate host-local venv.
set -euo pipefail

LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SOURCE_ROOT="$LAB_ROOT/vendor/mini-swe-agent"
if [[ ! -f "$SOURCE_ROOT/pyproject.toml" || ! -d "$SOURCE_ROOT/src/minisweagent" ]]; then
  echo "ERROR: vendored mini-swe-agent source is missing: $SOURCE_ROOT" >&2
  exit 1
fi

if [[ -z "${SWE_LAB_DATA_ROOT:-}" && -f "$LAB_ROOT/.env" ]]; then
  while IFS='=' read -r key value; do
    [[ "$key" == "SWE_LAB_DATA_ROOT" ]] || continue
    SWE_LAB_DATA_ROOT="${value%\"}"
    SWE_LAB_DATA_ROOT="${SWE_LAB_DATA_ROOT#\"}"
    SWE_LAB_DATA_ROOT="${SWE_LAB_DATA_ROOT%\'}"
    SWE_LAB_DATA_ROOT="${SWE_LAB_DATA_ROOT#\'}"
    break
  done < "$LAB_ROOT/.env"
fi
DATA_ROOT="${SWE_LAB_DATA_ROOT:-$LAB_ROOT/.local}"
case "$DATA_ROOT" in /*) ;; *) DATA_ROOT="$LAB_ROOT/$DATA_ROOT" ;; esac
mkdir -p "$DATA_ROOT/venvs" "$DATA_ROOT/cache/pip" "$DATA_ROOT/versions"
export PIP_CACHE_DIR="$DATA_ROOT/cache/pip"
VENV="$DATA_ROOT/venvs/mini-sweagent"
PYTHON="${MINI_SWE_AGENT_PYTHON:-python3}"
if [[ -x "$VENV/bin/python" ]]; then
  "$VENV/bin/python" -c 'import sys; assert sys.version_info >= (3, 10)' || {
    echo "ERROR: existing mini-swe-agent venv does not use Python 3.10+: $VENV" >&2
    exit 1
  }
else
  "$PYTHON" -c 'import sys; assert sys.version_info >= (3, 10)' || {
    echo "ERROR: mini-swe-agent requires Python 3.10+; set MINI_SWE_AGENT_PYTHON" >&2
    exit 1
  }
  "$PYTHON" -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --disable-pip-version-check --editable "$SOURCE_ROOT"
"$VENV/bin/python" -m pip check
"$VENV/bin/python" -c 'import minisweagent; print("mini-swe-agent:", minisweagent.__file__)'
"$VENV/bin/mini-extra" --help >/dev/null
"$VENV/bin/python" -m pip freeze > "$DATA_ROOT/versions/mini-swe-agent-v2.4.6-freeze.txt"
echo "mini-swe-agent ready: $VENV/bin/mini-extra (source: $SOURCE_ROOT)"
