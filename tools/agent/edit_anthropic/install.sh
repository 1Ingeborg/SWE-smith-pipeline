#!/usr/bin/env bash
# Installed inside each task container. Use one Python for dependencies and tools.

bundle_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
selected_python=
if [ -n "${SWE_LAB_EDITOR_PYTHON_CANDIDATES:-}" ]; then
    read -r -a candidates <<< "$SWE_LAB_EDITOR_PYTHON_CANDIDATES"
else
    candidates=(/root/python3.11/bin/python3 /opt/miniconda3/bin/python3 /usr/local/bin/python3 /usr/bin/python3)
fi
for candidate in "${candidates[@]}"; do
    if [ -x "$candidate" ] &&
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' &&
       "$candidate" -m pip --version >/dev/null 2>&1; then
        selected_python=$candidate
        break
    fi
done

if [ -z "$selected_python" ]; then
    echo "Agent editor needs Python >=3.8 with pip inside the task container; no compatible interpreter was found" >&2
    return 1
fi

printf '%s\n' "$selected_python" > "$bundle_dir/python-path"
pip_options=(--disable-pip-version-check)
if [ -n "${SWE_LAB_EDITOR_PIP_INDEX_URL:-}" ]; then
    pip_options+=(--index-url "$SWE_LAB_EDITOR_PIP_INDEX_URL")
fi
"$selected_python" -m pip install "${pip_options[@]}" 'tree-sitter==0.21.3' 'tree-sitter-languages'
