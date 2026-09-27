import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "run-reviewed-issuegen.py"
SPEC = importlib.util.spec_from_file_location("run_reviewed_issuegen", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_classify_accepts_only_reviewed_nonempty_statements():
    inputs = [
        {"instance_id": "one", "patch": "one patch"},
        {"instance_id": "two", "patch": "two patch"},
        {"instance_id": "three", "patch": "three patch"},
        {"instance_id": "four", "patch": "four patch"},
    ]
    generated = [
        {"instance_id": "one", "review_status": "accepted", "problem_statement": "A real issue"},
        {"instance_id": "two", "review_status": "accepted", "problem_statement": " "},
        {"instance_id": "three", "review_status": "rejected_by_reviewers", "problem_statement": ""},
    ]
    failures = [{"instance_id": "four", "error": "Missing test source"}]

    accepted, quarantine, counts = MODULE.classify(inputs, generated, failures)

    assert [row["instance_id"] for row in accepted] == ["one"]
    assert {row["instance_id"] for row in quarantine} == {"two", "three", "four"}
    assert accepted[0]["patch"] == "one patch"
    assert counts == {"accepted": 2, "preparation_failed": 1, "rejected_by_reviewers": 1}


def test_read_records_accepts_json_and_jsonl(tmp_path):
    array = tmp_path / "input.json"
    jsonl = tmp_path / "input.jsonl"
    rows = [{"instance_id": "one"}, {"instance_id": "two"}]
    array.write_text(json.dumps(rows), encoding="utf-8")
    jsonl.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    assert MODULE.read_records(array) == MODULE.read_records(jsonl) == rows


def test_key_environment_wins_and_missing_key_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, "LAB_ROOT", tmp_path)
    (tmp_path / ".env").write_text("DEEPSEEK_API_KEY=file-value\n", encoding="utf-8")
    env = {"DEEPSEEK_API_KEY": "process-value"}
    MODULE.load_key(env)
    assert env["DEEPSEEK_API_KEY"] == "process-value"

    (tmp_path / ".env").write_text("SWE_LAB_DATA_ROOT=/data\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY is missing"):
        MODULE.load_key({})
