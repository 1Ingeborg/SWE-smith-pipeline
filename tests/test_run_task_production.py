import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "run-task-production.py"
SPEC = importlib.util.spec_from_file_location("run_task_production", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_load_records_accepts_json_and_jsonl(tmp_path):
    array_path = tmp_path / "records.json"
    array_path.write_text(json.dumps([{"instance_id": "one"}, {"instance_id": "two"}]))
    jsonl_path = tmp_path / "records.jsonl"
    jsonl_path.write_text(
        json.dumps({"instance_id": "one"})
        + "\n"
        + json.dumps({"instance_id": "two"})
        + "\n"
    )

    assert MODULE.load_records(array_path) == MODULE.load_records(jsonl_path)


@pytest.mark.parametrize("run_id", ["pilot-7", "qwen.v1", "run_2026_09_13"])
def test_validate_run_id_accepts_safe_names(run_id):
    MODULE.validate_run_id(run_id)


@pytest.mark.parametrize("run_id", ["", "../escape", "has space", "/absolute"])
def test_validate_run_id_rejects_unsafe_names(run_id):
    with pytest.raises(ValueError):
        MODULE.validate_run_id(run_id)


def test_validation_link_is_stable(tmp_path):
    workspace = tmp_path / "workspace"
    validation = tmp_path / "validation"
    validation.mkdir()

    MODULE.ensure_validation_link(workspace, validation)
    MODULE.ensure_validation_link(workspace, validation)

    link = workspace / "logs" / "run_validation"
    assert link.is_symlink()
    assert link.resolve() == validation.resolve()
