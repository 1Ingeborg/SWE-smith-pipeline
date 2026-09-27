#!/usr/bin/env bash
set -euo pipefail

LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SWESMITH_SRC="$LAB_ROOT/vendor/SWE-smith"
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
CORE_PYTHON="$SWE_LAB_DATA_ROOT/venvs/swesmith-lab-core/bin/python"
LLM_PYTHON="$SWE_LAB_DATA_ROOT/venvs/swesmith-lab-llm/bin/python"

cd "$SWESMITH_SRC"
"$CORE_PYTHON" - <<'PY'
import swebench
import swesmith

print(f"Python:     active")
print(f"SWE-smith: {swesmith.__version__}")
print(f"SWE-bench: {swebench.__version__}")
PY

docker info --format 'Docker root: {{.DockerRootDir}}; driver: {{.Driver}}'
"$CORE_PYTHON" -m swesmith.bug_gen.procedural.generate --help >/dev/null
"$LLM_PYTHON" -c 'import litellm, swesmith; print("LiteLLM environment: ready")'
"$CORE_PYTHON" -m pytest -q tests/bug_gen/procedural/python/test_py_control_flow.py
df -hT / "$SWE_LAB_DATA_ROOT"
