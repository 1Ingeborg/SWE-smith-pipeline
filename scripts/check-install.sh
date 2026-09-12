#!/usr/bin/env bash
set -euo pipefail

DATA_ROOT="${DATA_ROOT:-/data}"
SWESMITH_SRC="${SWESMITH_SRC:-$DATA_ROOT/repos/SWE-smith}"
SWESMITH_VENV="${SWESMITH_VENV:-$DATA_ROOT/venvs/swesmith}"

cd "$SWESMITH_SRC"
. "$SWESMITH_VENV/bin/activate"

python - <<'PY'
import swebench
import swesmith

print(f"Python:     active")
print(f"SWE-smith: {swesmith.__version__}")
print(f"SWE-bench: {swebench.__version__}")
PY

docker info --format 'Docker root: {{.DockerRootDir}}; driver: {{.Driver}}'
python -m swesmith.bug_gen.procedural.generate --help >/dev/null
pytest -q tests/bug_gen/procedural/python/test_py_control_flow.py
df -hT / "$DATA_ROOT"
