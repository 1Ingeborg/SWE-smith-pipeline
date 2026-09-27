import os
import shutil
import subprocess
from pathlib import Path

import pytest


WRAPPER = Path(__file__).resolve().parents[1] / "scripts" / "run-task-generation.sh"


def fake_checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(WRAPPER, scripts / WRAPPER.name)
    (scripts / "run-task-generation.py").write_text("# placeholder\n", encoding="utf-8")
    return repo


def fake_python(data_root: Path, marker: str) -> None:
    python = data_root / "venvs" / "swesmith-lab-core" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(
        f"#!/bin/sh\nprintf 'MARKER={marker}\\n'\nprintf 'PWD=%s\\n' \"$PWD\"\nprintf 'ARG=%s\\n' \"$@\"\n",
        encoding="utf-8",
    )
    python.chmod(0o755)


def run_wrapper(repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    if not shutil.which("bash"):
        pytest.skip("bash is required")
    return subprocess.run(
        ["bash", str(repo / "scripts" / "run-task-generation.sh"), "--config", "name with spaces.yaml", "--dry-run"],
        cwd=repo.parent,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("SWE_LAB_DATA_ROOT", None)
    return env


def test_environment_variable_takes_precedence_over_env_file(tmp_path: Path) -> None:
    repo = fake_checkout(tmp_path)
    file_root = tmp_path / "from-file"
    override_root = tmp_path / "from-process"
    fake_python(file_root, "file")
    fake_python(override_root, "process")
    (repo / ".env").write_text(f"SWE_LAB_DATA_ROOT={file_root}\n", encoding="utf-8")
    env = clean_env()
    env["SWE_LAB_DATA_ROOT"] = str(override_root)

    result = run_wrapper(repo, env)

    assert result.returncode == 0, result.stderr
    assert "MARKER=process" in result.stdout
    assert f"PWD={repo}" in result.stdout
    assert "ARG=name with spaces.yaml" in result.stdout
    assert "ARG=--dry-run" in result.stdout


def test_relative_env_file_path_is_resolved_from_checkout(tmp_path: Path) -> None:
    repo = fake_checkout(tmp_path)
    fake_python(repo / "portable-data", "env-file")
    (repo / ".env").write_text('SWE_LAB_DATA_ROOT="portable-data"\n', encoding="utf-8")

    result = run_wrapper(repo, clean_env())

    assert result.returncode == 0, result.stderr
    assert "MARKER=env-file" in result.stdout


def test_default_local_environment_and_missing_environment(tmp_path: Path) -> None:
    repo = fake_checkout(tmp_path)
    result = run_wrapper(repo, clean_env())
    assert result.returncode == 1
    assert "bootstrap-python.sh" in result.stderr

    fake_python(repo / ".local", "default")
    result = run_wrapper(repo, clean_env())
    assert result.returncode == 0, result.stderr
    assert "MARKER=default" in result.stdout
