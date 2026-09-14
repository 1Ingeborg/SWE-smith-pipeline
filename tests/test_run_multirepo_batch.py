import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "run-multirepo-batch.py"
SPEC = importlib.util.spec_from_file_location("run_multirepo_batch", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def write_config(tmp_path, body):
    path = tmp_path / "config.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def test_load_config_applies_defaults_and_per_repo_overrides(tmp_path):
    path = write_config(
        tmp_path,
        """
schema_version: 1
validation_workers: 1
target_validated: 10
generation:
  selected_patches: 4
  max_bugs_per_modifier: 5
  max_entities: 100
  max_candidates: 50
  timeout_seconds: 60
  seed: 20
  selection_seed: 30
repositories:
  - repo: owner__one.abc
    image_name: swebench/one
  - repo: owner__two.def
    image_name: swebench/two
    selected_patches: 7
""",
    )

    config, plans = MODULE.load_config(path)

    assert config["validation_workers"] == 1
    assert config["target_validated"] == 10
    assert plans[0].selected_patches == 4
    assert plans[0].seed == 20
    assert plans[1].selected_patches == 7
    assert plans[1].seed == 20
    assert plans[1].selection_seed == 30


def test_load_config_rejects_duplicate_repositories(tmp_path):
    path = write_config(
        tmp_path,
        """
schema_version: 1
repositories:
  - repo: owner__repo.abc
    image_name: swebench/one
  - repo: owner__repo.abc
    image_name: swebench/two
""",
    )

    with pytest.raises(ValueError, match="Duplicate repository"):
        MODULE.load_config(path)


def test_generation_command_has_resource_bounds():
    plan = MODULE.RepoPlan(
        repo="owner__repo.abc",
        image_name="swebench/repo",
        source_image_name=None,
        selected_patches=5,
        seed=9,
        selection_seed=10,
        max_bugs_per_modifier=11,
        max_entities=120,
        max_candidates=70,
        timeout_seconds=300,
    )

    command = MODULE.generation_command(Path("/venv/python"), plan)

    assert command[-2:] == ["--timeout_seconds", "300"]
    assert command[command.index("--max_entities") + 1] == "120"
    assert command[command.index("--max_candidates") + 1] == "70"


def test_materialize_refuses_existing_non_git_directory(tmp_path):
    destination = tmp_path / "owner__repo.abc"
    destination.mkdir()

    with pytest.raises(RuntimeError, match="Refusing to replace"):
        MODULE.materialize_repo_from_image(
            "swebench/repo",
            destination,
            cwd=tmp_path,
            env={},
            log_path=tmp_path / "source.log",
        )


@pytest.mark.parametrize("run_id", ["batch-1", "qwen.v2", "run_20260914"])
def test_validate_run_id_accepts_safe_names(run_id):
    MODULE.validate_run_id(run_id)


@pytest.mark.parametrize("run_id", ["", "../escape", "has space", "/absolute"])
def test_validate_run_id_rejects_unsafe_names(run_id):
    with pytest.raises(ValueError):
        MODULE.validate_run_id(run_id)
