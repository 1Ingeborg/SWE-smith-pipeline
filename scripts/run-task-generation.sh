#!/usr/bin/env bash
# Run pre-Agent task generation with the core environment of this checkout.
set -euo pipefail

LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

# Only read the data-root setting; never source credentials from .env into a shell.
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

SWE_LAB_DATA_ROOT="${SWE_LAB_DATA_ROOT:-$LAB_ROOT/.local}"
case "$SWE_LAB_DATA_ROOT" in
  /*) ;;
  *) SWE_LAB_DATA_ROOT="$LAB_ROOT/$SWE_LAB_DATA_ROOT" ;;
esac
export SWE_LAB_DATA_ROOT

CORE_PYTHON="$SWE_LAB_DATA_ROOT/venvs/swesmith-lab-core/bin/python"
if [[ ! -x "$CORE_PYTHON" ]]; then
  echo "ERROR: core Python environment not found: $CORE_PYTHON" >&2
  echo "Run: bash scripts/bootstrap-python.sh" >&2
  exit 1
fi

cd "$LAB_ROOT"
exec "$CORE_PYTHON" "$LAB_ROOT/scripts/run-task-generation.py" "$@"
