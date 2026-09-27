#!/usr/bin/env bash
set -euo pipefail

LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SWESMITH_SRC="$LAB_ROOT/vendor/SWE-smith"

# The shell environment wins; .env supplies a host-local default.
if [[ -z "${SWE_LAB_DATA_ROOT:-}" && -f "$LAB_ROOT/.env" ]]; then
  while IFS='=' read -r key value; do
    if [[ "$key" == "SWE_LAB_DATA_ROOT" ]]; then
      SWE_LAB_DATA_ROOT="${value%\"}"
      SWE_LAB_DATA_ROOT="${SWE_LAB_DATA_ROOT#\"}"
      break
    fi
  done < "$LAB_ROOT/.env"
fi
SWE_LAB_DATA_ROOT="${SWE_LAB_DATA_ROOT:-$LAB_ROOT/.local}"
case "$SWE_LAB_DATA_ROOT" in /*) ;; *) SWE_LAB_DATA_ROOT="$LAB_ROOT/$SWE_LAB_DATA_ROOT" ;; esac
SWE_LAB_DATA_ROOT="$(mkdir -p "$SWE_LAB_DATA_ROOT" && cd "$SWE_LAB_DATA_ROOT" && pwd)"
CORE_VENV="$SWE_LAB_DATA_ROOT/venvs/swesmith-lab-core"
LLM_VENV="$SWE_LAB_DATA_ROOT/venvs/swesmith-lab-llm"
export PIP_CACHE_DIR="$SWE_LAB_DATA_ROOT/cache/pip"

if [[ ! -f "$SWESMITH_SRC/pyproject.toml" || ! -d "$SWESMITH_SRC/swesmith" ]]; then
  echo "ERROR: bundled SWE-smith source is missing from $SWESMITH_SRC." >&2
  echo "Clone the complete SWE-smith-pipeline repository first." >&2
  exit 1
fi

for profile in core llm; do
  if [[ "$profile" == core ]]; then
    venv="$CORE_VENV"
    extras="$LAB_ROOT/requirements/cpu-extra.txt"
  else
    venv="$LLM_VENV"
    extras="$LAB_ROOT/requirements/llm-extra.txt"
  fi
  if [[ ! -e "$venv/bin/python" ]]; then
    python3 -m venv "$venv"
  fi
  "$venv/bin/python" -m pip install --upgrade pip setuptools wheel
  "$venv/bin/python" -m pip install -e "$SWESMITH_SRC[generate,validate,test]" \
    -c "$LAB_ROOT/requirements/constraints.txt"
  "$venv/bin/python" -m pip install -r "$extras" \
    -c "$LAB_ROOT/requirements/constraints.txt"
  "$venv/bin/python" -m pip check
  mkdir -p "$SWE_LAB_DATA_ROOT/results/env-manifests"
  "$venv/bin/python" -m pip freeze > "$SWE_LAB_DATA_ROOT/results/env-manifests/swesmith-lab-$profile.txt"
  echo "Python environment ready: $venv"
done
