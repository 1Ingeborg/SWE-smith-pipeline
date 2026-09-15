#!/usr/bin/env bash
set -euo pipefail

# SWE-agent has a separate Python requirement and dependency tree. Keep every
# mutable artifact on /data and do not modify /data/venvs/swesmith.
SWE_AGENT_VERSION="v1.1.0"
SWE_AGENT_ARCHIVE_SHA256="385d29b3916097ac2ecb7b891dbcfe09c01b6738d38257f153306f0beca68979"
SWE_AGENT_URL="https://codeload.github.com/SWE-agent/SWE-agent/tar.gz/refs/tags/${SWE_AGENT_VERSION}"
UV_VERSION="0.12.13"
UV_DEFAULT_INDEX="${SWE_AGENT_UV_INDEX_URL:-http://mirrors.tencentyun.com/pypi/simple}"
UV_INSECURE_HOST="${SWE_AGENT_UV_INSECURE_HOST:-mirrors.tencentyun.com}"

DATA_ROOT="${SWE_AGENT_DATA_ROOT:-/data}"
SOURCE_ROOT="${DATA_ROOT}/repos"
VERSIONED_SOURCE="${SOURCE_ROOT}/SWE-agent-${SWE_AGENT_VERSION#v}"
SOURCE_LINK="${SOURCE_ROOT}/SWE-agent"
VENV_ROOT="${DATA_ROOT}/venvs"
UV_BOOTSTRAP_VENV="${VENV_ROOT}/uv-bootstrap"
SWE_AGENT_VENV="${VENV_ROOT}/sweagent"
CACHE_ROOT="${DATA_ROOT}/cache"
PIP_CACHE_DIR="${CACHE_ROOT}/pip"
UV_CACHE_DIR="${CACHE_ROOT}/uv"
UV_PYTHON_INSTALL_DIR="${DATA_ROOT}/python/uv"
SOURCE_CACHE="${CACHE_ROOT}/sources"
ARCHIVE_PATH="${SOURCE_CACHE}/SWE-agent-${SWE_AGENT_VERSION}.tar.gz"
VERSION_ROOT="${DATA_ROOT}/versions"

export PIP_CACHE_DIR UV_CACHE_DIR UV_PYTHON_INSTALL_DIR

mkdir -p \
  "${SOURCE_ROOT}" \
  "${VENV_ROOT}" \
  "${PIP_CACHE_DIR}" \
  "${UV_CACHE_DIR}" \
  "${UV_PYTHON_INSTALL_DIR}" \
  "${SOURCE_CACHE}" \
  "${VERSION_ROOT}"

if [[ ! -x "${UV_BOOTSTRAP_VENV}/bin/python" ]]; then
  python3 -m venv "${UV_BOOTSTRAP_VENV}"
fi
"${UV_BOOTSTRAP_VENV}/bin/python" -m pip install \
  --disable-pip-version-check \
  "uv==${UV_VERSION}"
UV_BIN="${UV_BOOTSTRAP_VENV}/bin/uv"
UV_INDEX_ARGS=(--default-index "${UV_DEFAULT_INDEX}")
if [[ -n "${UV_INSECURE_HOST}" ]]; then
  UV_INDEX_ARGS+=(--allow-insecure-host "${UV_INSECURE_HOST}")
fi

echo "Installing or locating a managed Python 3.11 runtime under ${UV_PYTHON_INSTALL_DIR}"
"${UV_BIN}" python install 3.11
PYTHON311="$("${UV_BIN}" python find 3.11)"
"${PYTHON311}" -c 'import sys; assert sys.version_info >= (3, 11), sys.version'

if [[ -x "${SWE_AGENT_VENV}/bin/python" ]]; then
  if ! "${SWE_AGENT_VENV}/bin/python" -c 'import sys; assert sys.version_info >= (3, 11)' 2>/dev/null; then
    echo "Refusing to replace incompatible existing venv: ${SWE_AGENT_VENV}" >&2
    exit 1
  fi
else
  "${UV_BIN}" venv --python "${PYTHON311}" "${SWE_AGENT_VENV}"
fi

archive_ok=false
if [[ -f "${ARCHIVE_PATH}" ]]; then
  actual_sha="$(sha256sum "${ARCHIVE_PATH}" | awk '{print $1}')"
  if [[ "${actual_sha}" == "${SWE_AGENT_ARCHIVE_SHA256}" ]]; then
    archive_ok=true
  else
    echo "Cached archive has the wrong SHA256; refusing to use it: ${ARCHIVE_PATH}" >&2
    exit 1
  fi
fi

if [[ "${archive_ok}" != true ]]; then
  download_path="$(mktemp "${SOURCE_CACHE}/swe-agent-download.XXXXXX")"
  cleanup_download() {
    rm -f -- "${download_path}"
  }
  trap cleanup_download EXIT
  curl -fL --retry 5 --connect-timeout 20 "${SWE_AGENT_URL}" -o "${download_path}"
  echo "${SWE_AGENT_ARCHIVE_SHA256}  ${download_path}" | sha256sum --check --status
  mv -- "${download_path}" "${ARCHIVE_PATH}"
  trap - EXIT
fi

SOURCE_MARKER="${VERSIONED_SOURCE}/.swe-agent-source.sha256"
if [[ -d "${VERSIONED_SOURCE}" ]]; then
  if [[ ! -f "${SOURCE_MARKER}" ]] || \
     [[ "$(<"${SOURCE_MARKER}")" != "${SWE_AGENT_ARCHIVE_SHA256}" ]]; then
    echo "Refusing to overwrite an unrecognized source directory: ${VERSIONED_SOURCE}" >&2
    exit 1
  fi
else
  extract_dir="$(mktemp -d "${SOURCE_ROOT}/swe-agent-extract.XXXXXX")"
  cleanup_extract() {
    rm -rf -- "${extract_dir}"
  }
  trap cleanup_extract EXIT
  tar -xzf "${ARCHIVE_PATH}" --strip-components=1 -C "${extract_dir}"
  printf '%s\n' "${SWE_AGENT_ARCHIVE_SHA256}" > "${extract_dir}/.swe-agent-source.sha256"
  mv -- "${extract_dir}" "${VERSIONED_SOURCE}"
  trap - EXIT
fi

if [[ -L "${SOURCE_LINK}" ]]; then
  current_target="$(readlink -f "${SOURCE_LINK}")"
  if [[ "${current_target}" != "${VERSIONED_SOURCE}" ]]; then
    echo "Refusing to retarget existing symlink ${SOURCE_LINK} -> ${current_target}" >&2
    exit 1
  fi
elif [[ -e "${SOURCE_LINK}" ]]; then
  echo "Refusing to replace existing non-symlink path: ${SOURCE_LINK}" >&2
  exit 1
else
  ln -s "${VERSIONED_SOURCE}" "${SOURCE_LINK}"
fi

"${UV_BIN}" pip install \
  --python "${SWE_AGENT_VENV}/bin/python" \
  "${UV_INDEX_ARGS[@]}" \
  --editable "${VERSIONED_SOURCE}"

FREEZE_PATH="${VERSION_ROOT}/swe-agent-${SWE_AGENT_VERSION}-freeze.txt"
"${UV_BIN}" pip freeze --python "${SWE_AGENT_VENV}/bin/python" > "${FREEZE_PATH}"
"${SWE_AGENT_VENV}/bin/python" - <<'PY'
import sys
import sweagent
import swerex

print(f"Python: {sys.version.split()[0]}")
print(f"SWE-agent module: {sweagent.__file__}")
print(f"SWE-ReX module: {swerex.__file__}")
PY
"${SWE_AGENT_VENV}/bin/sweagent" --help >/dev/null

INSTALL_MANIFEST="${VERSION_ROOT}/swe-agent-${SWE_AGENT_VERSION}-install.txt"
{
  printf 'swe_agent_version=%s\n' "${SWE_AGENT_VERSION}"
  printf 'source_archive_sha256=%s\n' "${SWE_AGENT_ARCHIVE_SHA256}"
  printf 'source_dir=%s\n' "${VERSIONED_SOURCE}"
  printf 'source_link=%s\n' "${SOURCE_LINK}"
  printf 'venv=%s\n' "${SWE_AGENT_VENV}"
  printf 'python=%s\n' "$("${SWE_AGENT_VENV}/bin/python" --version 2>&1)"
  printf 'uv_version=%s\n' "$("${UV_BIN}" --version)"
  printf 'uv_default_index=%s\n' "${UV_DEFAULT_INDEX}"
  printf 'freeze_sha256=%s\n' "$(sha256sum "${FREEZE_PATH}" | awk '{print $1}')"
} > "${INSTALL_MANIFEST}"

echo "SWE-agent ${SWE_AGENT_VERSION} is ready."
echo "Executable: ${SWE_AGENT_VENV}/bin/sweagent"
echo "Source: ${SOURCE_LINK}"
echo "Freeze: ${FREEZE_PATH}"
echo "Manifest: ${INSTALL_MANIFEST}"
