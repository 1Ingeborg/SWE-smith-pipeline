import importlib.util
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("agent_sft", ROOT / "src/swesmith_lab/agent/sft.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("framework", ("swe-agent", "mini-swe-agent"))
def test_export_checks_ids_and_writes_training_data(tmp_path, monkeypatch, framework):
    rollout = tmp_path / "rollout"
    evaluation = tmp_path / "evaluation"
    instances = tmp_path / "instances.jsonl"
    output = tmp_path / "sft"
    rollout.mkdir()
    evaluation.mkdir()
    (evaluation / "one").mkdir()
    instances.write_text('{"instance_id":"one"}\n', encoding="utf-8")
    (rollout / "preds.json").write_text('{"one":{"model_patch":"patch"}}', encoding="utf-8")
    (evaluation / "report.json").write_text(
        '{"gold":false,"selected_tasks":1,"evaluated":1,"resolved":1,'
        '"missing_predictions":[],"unexpected_predictions":[]}', encoding="utf-8")
    (evaluation / "one" / "report.json").write_text(
        '{"status":"completed","resolved":true}', encoding="utf-8")
    row = {"instance_id": "one", "resolved": True, "patch": "patch", "messages": [
        {"role": "system", "content": "system"}, {"role": "user", "content": "issue"},
        {"role": "assistant", "content": "<function=bash>" if framework == "swe-agent" else "```mswea_bash_command"},
    ]}
    if framework == "swe-agent":
        monkeypatch.setattr(MODULE, "export_swe", lambda *_: [row])
    else:
        monkeypatch.setattr(MODULE, "export_mini", lambda *_: [row])
    argv = ["sft.py", "--framework", framework, "--rollout-dir", str(rollout),
            "--eval-dir", str(evaluation), "--output-dir", str(output),
            "--instances", str(instances), "--model", "test-model"]
    if framework == "mini-swe-agent":
        argv.extend(["--native-config", str(tmp_path / "native.yaml")])
    monkeypatch.setattr(sys, "argv", argv)
    MODULE.main()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["converted_all"] == manifest["converted_resolved"] == 1
    assert manifest["style"] == ("xml" if framework == "swe-agent" else "native")
    assert json.loads((output / "resolved_chat.jsonl").read_text(encoding="utf-8"))["messages"] == row["messages"]
    with pytest.raises(FileExistsError):
        MODULE.main()


def test_swe_collector_uses_last_valid_query_after_malformed_exit():
    from swesmith.train.traj_mgr.utils import get_messages

    messages = [{"role": "system", "content": "instructions"},
                {"role": "user", "content": "issue"}]
    trajectory = {"trajectory": [
        {"query": messages, "response": "attempt"},
        {"query": [{}], "response": "Exit due to cost limit"},
    ]}
    assert get_messages(trajectory) == messages
    with pytest.raises(ValueError, match="no non-empty model query"):
        get_messages({"trajectory": [{"query": [{}], "response": "failed"}]})


def test_mini_native_actions_are_not_rewritten_as_xml():
    path = ROOT / "workflows/mini-sweagent/convert_mini_trajs_to_sft.py"
    spec = importlib.util.spec_from_file_location("mini_native_conversion", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    trajectory = {"messages": [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "issue"},
        {"role": "assistant", "content": "think\n```mswea_bash_command\necho ok\n```",
         "extra": {"actions": [{"command": "echo ok"}]}},
        {"role": "user", "content": "OBSERVATION:\nok"},
    ]}
    messages, stats = module.convert_messages_native(trajectory)
    assert stats["valid_actions"] == 1
    assert "```mswea_bash_command" in messages[2]["content"]
    assert "<function=bash>" not in messages[2]["content"]


def test_mini_native_converter_cli_on_one_evaluated_trajectory(tmp_path):
    iid = "one"
    rollout = tmp_path / "rollout"
    evaluation = tmp_path / "evaluation"
    instances = tmp_path / "instances.jsonl"
    native = tmp_path / "native.yaml"
    work = tmp_path / "converted"
    (rollout / iid).mkdir(parents=True)
    (evaluation / iid).mkdir(parents=True)
    instances.write_text('{"instance_id":"one","problem_statement":"fix it"}\n', encoding="utf-8")
    (rollout / "preds.json").write_text('{"one":{"model_patch":"patch"}}', encoding="utf-8")
    (evaluation / iid / "report.json").write_text('{"resolved":true}', encoding="utf-8")
    native.write_text('agent: {step_limit: 250}\n', encoding="utf-8")
    trajectory = {"messages": [
        {"role": "system", "content": "system"}, {"role": "user", "content": "fix it"},
        {"role": "assistant", "content": "think\n```mswea_bash_command\necho ok\n```",
         "extra": {"actions": [{"command": "echo ok"}]}},
        {"role": "user", "content": "OBSERVATION:\nok"},
    ], "info": {"exit_status": "submitted"}}
    (rollout / iid / f"{iid}.traj.json").write_text(json.dumps(trajectory), encoding="utf-8")
    rows = MODULE.export_mini(rollout, evaluation, instances, native, "test-model", work)
    assert len(rows) == 1 and rows[0]["instance_id"] == iid
    assert rows[0]["resolved"] is True
    assert "```mswea_bash_command" in rows[0]["messages"][2]["content"]
