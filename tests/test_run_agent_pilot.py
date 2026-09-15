import argparse
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "run-agent-pilot.py"
SPEC = importlib.util.spec_from_file_location("run_agent_pilot", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def make_args(**overrides):
    values = {
        "run_id": "pilot-1",
        "workers": 1,
        "limit": None,
        "per_instance_call_limit": 30,
        "per_instance_cost_limit": 2.0,
        "total_cost_limit": 12.0,
        "mode": "smoke",
        "allow_api_calls": False,
        "model_name": None,
        "api_base": None,
        "api_key_env": "DASHSCOPE_API_KEY",
        "sweagent_executable": Path("/venv/sweagent"),
        "instances": Path("instances.jsonl"),
        "use_standalone_python": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_model_mode_requires_explicit_api_gate():
    args = make_args(
        mode="model",
        model_name="openai/test",
        api_base="https://example.invalid/v1",
    )
    with pytest.raises(ValueError, match="allow-api-calls"):
        MODULE.validate_args(args)


def test_limit_must_be_positive():
    with pytest.raises(ValueError, match="--limit must be positive"):
        MODULE.validate_args(make_args(limit=0))


def test_smoke_command_uses_no_external_model_and_disables_runtime_image(tmp_path):
    args = make_args(instances=tmp_path / "instances.jsonl")
    command = MODULE.build_command(
        args,
        config_path=Path("/sweagent/config/default_backticks.yaml"),
        output_dir=Path("/output"),
    )

    assert "instant_empty_submit" in command
    assert "--instances.deployment.python_standalone_dir=" in command
    assert not any("api_key" in part for part in command)


def test_model_command_records_env_reference_not_secret(tmp_path):
    args = make_args(
        mode="model",
        allow_api_calls=True,
        model_name="openai/qwen",
        api_base="https://example.invalid/v1",
        instances=tmp_path / "instances.jsonl",
    )
    MODULE.validate_args(args)
    command = MODULE.build_command(
        args,
        config_path=Path("/sweagent/config/default.yaml"),
        output_dir=Path("/output"),
    )

    assert "$DASHSCOPE_API_KEY" in command
    assert "openai/qwen" in command


def test_load_public_instances_rejects_private_fields(tmp_path):
    path = tmp_path / "instances.jsonl"
    path.write_text(
        json.dumps(
            {
                "instance_id": "opaque",
                "image_name": "local/task:one",
                "problem_statement": "Observed behavior",
                "repo_name": "testbed",
                "base_commit": "HEAD",
                "patch": "secret",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="five Agent-visible fields"):
        MODULE.load_public_instances(path)


def test_summary_checks_prediction_coverage(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "preds.json").write_text(
        json.dumps(
            {
                "opaque-1": {
                    "instance_id": "opaque-1",
                    "model_patch": "patch",
                }
            }
        ),
        encoding="utf-8",
    )

    summary = MODULE.summarize_run(
        output,
        expected_ids={"opaque-1", "opaque-2"},
        process_return_code=0,
    )

    assert summary["prediction_count"] == 1
    assert summary["missing_ids"] == ["opaque-2"]
