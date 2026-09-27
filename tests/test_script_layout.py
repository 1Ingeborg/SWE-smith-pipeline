from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_public_script_entry_points_only() -> None:
    assert {path.name for path in (ROOT / "scripts").iterdir() if path.is_file()} == {
        "bootstrap-python.sh",
        "check-install.sh",
        "run-agent.sh",
        "run-task-generation.py",
        "run-task-generation.sh",
        "run-reviewed-issuegen.py",
    }


def test_internal_script_groups_exist() -> None:
    for subdir in ("pipeline", "issuegen", "agent"):
        assert (ROOT / "src" / "swesmith_lab" / subdir / "__init__.py").is_file()
    for subdir in ("setup", "maintenance", "legacy"):
        assert (ROOT / "tools" / subdir).is_dir()


def test_run_configs_are_grouped_by_stage() -> None:
    assert not (ROOT / "configs" / "experiments").exists()
    for subdir in ("task_generation", "rollout", "issue_gen"):
        assert (ROOT / "configs" / subdir).is_dir()
    assert not (ROOT / "configs" / "pipeline").exists()
    assert (ROOT / "tools" / "legacy" / "run-agent-rollout.sh").is_file()
