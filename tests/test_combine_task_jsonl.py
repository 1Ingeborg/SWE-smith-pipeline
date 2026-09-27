import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "src" / "swesmith_lab" / "pipeline" / "combine.py"
SPEC = importlib.util.spec_from_file_location("combine_task_jsonl", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_load_unique_rejects_duplicates(tmp_path: Path) -> None:
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first.write_text(json.dumps({"instance_id": "one"}) + "\n", encoding="utf-8")
    second.write_text(json.dumps({"instance_id": "one"}) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate instance_id"):
        MODULE.load_unique([first, second])
