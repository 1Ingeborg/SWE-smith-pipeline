#!/usr/bin/env bash
# Install the vendored SWE-agent 1.1.0 source in a separate host-local venv.
set -euo pipefail

LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
SOURCE_ROOT="$LAB_ROOT/vendor/SWE-agent"
if [[ ! -f "$SOURCE_ROOT/pyproject.toml" || ! -f "$SOURCE_ROOT/sweagent/run/run_batch.py" ]]; then
  echo "ERROR: vendored SWE-agent source is missing: $SOURCE_ROOT" >&2
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
mkdir -p "$DATA_ROOT/venvs" "$DATA_ROOT/cache/pip" "$DATA_ROOT/cache/uv" "$DATA_ROOT/python/uv" "$DATA_ROOT/versions"
export PIP_CACHE_DIR="$DATA_ROOT/cache/pip"
export UV_CACHE_DIR="$DATA_ROOT/cache/uv"
export UV_PYTHON_INSTALL_DIR="$DATA_ROOT/python/uv"

UV_BOOTSTRAP_VENV="$DATA_ROOT/venvs/uv-bootstrap"
if [[ ! -x "$UV_BOOTSTRAP_VENV/bin/uv" ]]; then
  if [[ ! -x "$UV_BOOTSTRAP_VENV/bin/python" ]]; then
    python3 -m venv "$UV_BOOTSTRAP_VENV"
  fi
  "$UV_BOOTSTRAP_VENV/bin/python" -m pip install --disable-pip-version-check 'uv==0.12.13'
fi
UV_BIN="$UV_BOOTSTRAP_VENV/bin/uv"
"$UV_BIN" python install 3.11
PYTHON311="$("$UV_BIN" python find 3.11)"
VENV="$DATA_ROOT/venvs/sweagent"
if [[ -x "$VENV/bin/python" ]]; then
  "$VENV/bin/python" -c 'import sys; assert sys.version_info >= (3, 11)' || {
    echo "ERROR: existing SWE-agent venv does not use Python 3.11+: $VENV" >&2
    exit 1
  }
else
  "$UV_BIN" venv --python "$PYTHON311" "$VENV"
fi

UV_INDEX_ARGS=(--default-index "${SWE_AGENT_UV_INDEX_URL:-https://pypi.org/simple}")
if [[ -n "${SWE_AGENT_UV_INSECURE_HOST:-}" ]]; then
  UV_INDEX_ARGS+=(--allow-insecure-host "$SWE_AGENT_UV_INSECURE_HOST")
fi
"$UV_BIN" pip install --python "$VENV/bin/python" "${UV_INDEX_ARGS[@]}" --editable "$SOURCE_ROOT"
"$UV_BIN" pip check --python "$VENV/bin/python"
"$VENV/bin/python" -c 'import sweagent, swerex; print("SWE-agent:", sweagent.__file__); print("SWE-ReX:", swerex.__file__)'
"$VENV/bin/sweagent" --help >/dev/null
"$UV_BIN" pip freeze --python "$VENV/bin/python" > "$DATA_ROOT/versions/swe-agent-v1.1.0-freeze.txt"
echo "SWE-agent ready: $VENV/bin/sweagent (source: $SOURCE_ROOT)"
