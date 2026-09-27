#!/usr/bin/env bash
# Restore optional legacy path links on this host.
set -euo pipefail

LAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"

link_if_absent() {
  local target="$1"
  local link="$2"

  if [[ ! -e "$target" ]]; then
    echo "Missing target: $target" >&2
    return 1
  fi

  mkdir -p "$(dirname "$link")"
  if [[ -L "$link" ]]; then
    if [[ "$(readlink -f "$link")" != "$(readlink -f "$target")" ]]; then
      echo "Conflicting link: $link" >&2
      return 1
    fi
    return 0
  fi
  if [[ -e "$link" ]]; then
    echo "Refusing to replace existing path: $link" >&2
    return 1
  fi
  ln -s "$target" "$link"
}

for workflow in mini-sweagent verified50 swebench-eval; do
  link_if_absent "$LAB_ROOT/workflows/$workflow" "/data/configs/$workflow"
done

if [[ -f "$LAB_ROOT/vendor/SWE-smith/pyproject.toml" && -d "$LAB_ROOT/vendor/SWE-smith/swesmith" ]]; then
  link_if_absent "$LAB_ROOT/vendor/SWE-smith" /data/repos/SWE-smith
else
  echo "Bundled SWE-smith source absent; fetch the complete lab repository." >&2
fi

echo "Legacy workflow links checked."
