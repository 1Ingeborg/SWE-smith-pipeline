import os
import shutil
import subprocess
from pathlib import Path


BUNDLE = Path(__file__).parents[1] / "tools" / "agent" / "edit_anthropic"


def staged_bundle(tmp_path: Path) -> Path:
    target = tmp_path / "edit_anthropic"
    shutil.copytree(BUNDLE, target)
    return target


def test_editor_bundle_selects_one_python_for_install_and_commands(tmp_path):
    bundle = staged_bundle(tmp_path)
    fake_python = tmp_path / "fake-python"
    calls = tmp_path / "calls.txt"
    fake_python.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{calls}'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    environment = os.environ.copy()
    environment["SWE_LAB_EDITOR_PYTHON_CANDIDATES"] = str(fake_python)
    environment["SWE_LAB_EDITOR_PIP_INDEX_URL"] = "https://example.invalid/simple"
    subprocess.run(["bash", "-c", f"source '{bundle / 'install.sh'}'"],
                   env=environment, check=True)
    assert (bundle / "python-path").read_text(encoding="utf-8").strip() == str(fake_python)
    subprocess.run(["bash", str(bundle / "bin" / "str_replace_editor"), "view", "/testbed"],
                   check=True)
    assert "-m pip install --disable-pip-version-check --index-url https://example.invalid/simple tree-sitter==0.21.3 tree-sitter-languages" in calls.read_text(encoding="utf-8")
    assert f"{bundle / 'bin' / 'str_replace_editor.py'} view /testbed" in calls.read_text(encoding="utf-8")


def test_editor_bundle_fails_clearly_without_compatible_python(tmp_path):
    bundle = staged_bundle(tmp_path)
    environment = os.environ.copy()
    environment["SWE_LAB_EDITOR_PYTHON_CANDIDATES"] = str(tmp_path / "missing-python")
    result = subprocess.run(["bash", "-c", f"source '{bundle / 'install.sh'}'"],
                            env=environment, text=True, capture_output=True)
    assert result.returncode != 0
    assert "Python >=3.8 with pip" in result.stderr
    assert not (bundle / "python-path").exists()
