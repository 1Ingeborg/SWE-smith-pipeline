#!/usr/bin/env bash
set -euo pipefail

DATA_ROOT="${DATA_ROOT:-/data}"
DOCKER_REGISTRY_MIRROR="${DOCKER_REGISTRY_MIRROR:-}"

if ! mountpoint -q "$DATA_ROOT"; then
  echo "ERROR: $DATA_ROOT is not a mounted data disk." >&2
  echo "Mount the data disk first. This script never formats disks." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
sudo apt-get update
sudo apt-get install -y \
  build-essential ca-certificates curl docker.io docker-compose-v2 git git-lfs \
  htop jq python3-pip python3-venv rsync sysstat tmux unzip

sudo mkdir -p \
  /etc/docker /etc/containerd \
  "$DATA_ROOT/docker" "$DATA_ROOT/containerd" \
  "$DATA_ROOT/repos" "$DATA_ROOT/venvs" "$DATA_ROOT/datasets" \
  "$DATA_ROOT/tasks" "$DATA_ROOT/trajectories" "$DATA_ROOT/results" \
  "$DATA_ROOT/huggingface" "$DATA_ROOT/cache/pip" "$DATA_ROOT/tmp"

python3 - "$DATA_ROOT" "$DOCKER_REGISTRY_MIRROR" <<'PY' | sudo tee /etc/docker/daemon.json >/dev/null
import json
import sys

config = {
    "data-root": f"{sys.argv[1]}/docker",
    "log-driver": "json-file",
    "log-opts": {"max-size": "50m", "max-file": "3"},
}
if sys.argv[2]:
    config["registry-mirrors"] = [sys.argv[2]]
print(json.dumps(config, indent=2))
PY

sudo systemctl stop docker.service docker.socket containerd.service || true
containerd config default \
  | sed -E "s|^root = .*|root = '$DATA_ROOT/containerd'|" \
  | sudo tee /etc/containerd/config.toml >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now containerd docker
sudo usermod -aG docker "$USER"

echo "Host bootstrap complete. Reconnect SSH once to activate docker group membership."
