#!/usr/bin/env bash
set -euo pipefail

DATA_ROOT="${DATA_ROOT:-/data}"
SWESMITH_SRC="${SWESMITH_SRC:-$DATA_ROOT/repos/SWE-smith}"
SWESMITH_VENV="${SWESMITH_VENV:-$DATA_ROOT/venvs/swesmith}"
SWESMITH_COMMIT="${SWESMITH_COMMIT:-9b74ac08118a85c39c356802f7961893af73e07f}"
LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! mountpoint -q "$DATA_ROOT"; then
  echo "ERROR: $DATA_ROOT is not a mounted data disk." >&2
  exit 1
fi

if [[ ! -d "$SWESMITH_SRC/.git" ]]; then
  echo "Cloning SWE-smith into $SWESMITH_SRC"
  git clone https://github.com/SWE-bench/SWE-smith.git "$SWESMITH_SRC"
fi

if [[ -n "$(git -C "$SWESMITH_SRC" status --porcelain)" ]]; then
  echo "ERROR: $SWESMITH_SRC has local changes; refusing to change its commit." >&2
  exit 1
fi

git -C "$SWESMITH_SRC" fetch origin "$SWESMITH_COMMIT" || true
git -C "$SWESMITH_SRC" checkout --detach "$SWESMITH_COMMIT"

python3 -m venv "$SWESMITH_VENV"
. "$SWESMITH_VENV/bin/activate"
export PIP_CACHE_DIR="$DATA_ROOT/cache/pip"

python -m pip install --upgrade pip setuptools wheel
cd "$SWESMITH_SRC"
python -m pip install -e '.[generate,validate,test]' \
  -c "$LAB_ROOT/requirements/constraints.txt"
python -m pip install \
  -r "$LAB_ROOT/requirements/cpu-extra.txt"

ln -sfn "$SWESMITH_VENV" "$SWESMITH_SRC/.venv"
python -m pip freeze > "$LAB_ROOT/requirements/current-cpu-freeze.txt"

echo "Python environment ready: $SWESMITH_VENV"
