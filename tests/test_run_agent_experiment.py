import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml


SCRIPT = Path(__file__).parents[1] / "src" / "swesmith_lab" / "agent" / "experiment.py"
SPEC = importlib.util.spec_from_file_location("run_agent_experiment", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def sample_run(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(MODULE, "LAB_ROOT", tmp_path)
    monkeypatch.setattr(MODULE, "RESULTS_ROOT", tmp_path / "results")
    agent_config = tmp_path / "configs" / "agent" / "deepseek-flash.yaml"
    agent_config.parent.mkdir(parents=True, exist_ok=True)
    agent_config.write_text("agent: {tools: {bundles: []}}\n", encoding="utf-8")
    run_dir = tmp_path / "results" / "production-one"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({"run_id": "production-one"}), encoding="utf-8")
    return run_dir


def args(**overrides):
    values = {
        "framework": "swe-agent", "rollout_id": "deepseek-t0", "stage": "agent",
        "dry_run": True, "resume": False, "allow_api_calls": False,
        "workers": 2, "eval_workers": 3, "eval_memory_limit": "4g",
        "eval_timeout_seconds": 120, "model_name": "deepseek/deepseek-flash",
        "api_base": "https://api.deepseek.com", "api_key_env": "DEEPSEEK_API_KEY",
        "agent_config": Path("configs/agent/deepseek-flash.yaml"),
        "mini_config": Path("configs/agent/mini-swe-agent-deepseek-flash.yaml"),
        "sweagent_root": None, "sweagent_executable": None, "mini_executable": None,
        "per_instance_call_limit": 30, "per_instance_cost_limit": 2.0,
        "total_cost_limit": 12.0, "prepare_config": Path("configs/rollout/shared-tasks.yaml"),
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def prepared(run_dir: Path) -> None:
    prep = run_dir / "task-prep" / run_dir.name
    (prep / "public").mkdir(parents=True)
    (prep / "private").mkdir()
    (prep / "public" / "instances.jsonl").write_text('{"instance_id":"one"}\n', encoding="utf-8")
    (prep / "private" / "selected.jsonl").write_text('{"_agent_instance_id":"one"}\n', encoding="utf-8")
    (prep / "summary.json").write_text('{"image_preparation_complete":true}', encoding="utf-8")


def test_preparation_complete_requires_matching_nonempty_records(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    assert not MODULE.preparation_complete(run_dir, verify_images=False)
    prepared(run_dir)
    assert not MODULE.preparation_complete(run_dir, verify_images=False)
    public = run_dir / "task-prep" / run_dir.name / "public" / "instances.jsonl"
    public.write_text('{"instance_id":"one","image_name":"task-image:one"}\n', encoding="utf-8")
    assert MODULE.preparation_complete(run_dir, verify_images=False)
    public.write_text('{"instance_id":"other","image_name":"task-image:one"}\n', encoding="utf-8")
    assert not MODULE.preparation_complete(run_dir, verify_images=False)


def rollout_config(tmp_path: Path, run_dir: Path) -> Path:
    native = tmp_path / "native.yaml"
    native.write_text("model: {model_name: old-model}\n", encoding="utf-8")
    template = tmp_path / "tasks.yaml"
    template.write_text("schema_version: 1\nsource: {}\n", encoding="utf-8")
    config = {
        "schema_version": 1,
        "run_dir": f"results/{run_dir.name}",
        "prepare": {"template": str(template)},
        "gold": {"run_id": "gold-one", "evaluation": {
            "workers": 1, "memory_limit": "4g", "timeout_seconds": 120,
        }},
        "experiments": {
            "mini-swe-agent": {
                "rollout_id": "deepseek-flash-t07", "native_config": str(native),
                "model_name": "deepseek/deepseek-flash", "api_base": "https://api.deepseek.com",
                "api_key_env": "DEEPSEEK_API_KEY", "temperature": 0.7,
                "workers": 1, "step_limit": 250, "cost_limit": 0.0,
                "max_output_tokens": 4096,
                "evaluation": {"workers": 1, "memory_limit": "4g", "timeout_seconds": 120},
            },
            "swe-agent": {
                "rollout_id": "deepseek-flash-t07", "native_config": str(native),
                "model_name": "deepseek/deepseek-flash", "api_base": "https://api.deepseek.com",
                "api_key_env": "DEEPSEEK_API_KEY", "temperature": 0.7,
                "workers": 1, "per_instance_call_limit": 30,
                "per_instance_cost_limit": 2.0, "total_cost_limit": 12.0,
                "evaluation": {"workers": 1, "memory_limit": "4g", "timeout_seconds": 120},
            },
        },
    }
    path = tmp_path / "rollout.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def test_run_dir_is_one_level_below_real_results(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    assert MODULE.resolve_run_dir(Path("results/production-one")) == run_dir
    with pytest.raises(ValueError, match="results/<run-id>"):
        MODULE.resolve_run_dir(run_dir / "nested")
    with pytest.raises(ValueError, match="results/<run-id>"):
        MODULE.resolve_run_dir(tmp_path / "historical" / "production-one")


def test_run_dir_accepts_new_meta_manifest(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    (run_dir / "meta").mkdir()
    (run_dir / "meta" / "manifest.json").write_text(
        json.dumps({"run_id": "production-one"}), encoding="utf-8"
    )
    (run_dir / "manifest.json").unlink()
    assert MODULE.resolve_run_dir(run_dir) == run_dir
    issuegen = run_dir / "issuegen"
    issuegen.mkdir()
    (issuegen / "accepted.jsonl").write_text('{"instance_id":"one"}\n', encoding="utf-8")
    template = tmp_path / "template.yaml"
    template.write_text('schema_version: 1\nsource: {}\nselection: {}\n', encoding="utf-8")
    config = MODULE.prepare_config(run_dir, template, dry_run=False)
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["source"]["dataset"] == str(issuegen / "accepted.jsonl")


def test_results_symlink_is_rejected(tmp_path, monkeypatch):
    sample_run(tmp_path, monkeypatch)
    (tmp_path / "results").rename(tmp_path / "real-results")
    (tmp_path / "results").symlink_to(tmp_path / "real-results", target_is_directory=True)
    with pytest.raises(RuntimeError, match="real directory"):
        MODULE.resolve_run_dir(tmp_path / "results" / "production-one")


def test_missing_manifest_and_inputs_fail_clearly(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    (run_dir / "manifest.json").unlink()
    with pytest.raises(FileNotFoundError, match="Task-generation manifest"):
        MODULE.resolve_run_dir(run_dir)
    (run_dir / "manifest.json").write_text('{"run_id":"production-one"}', encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="Prepared public instances"):
        MODULE.build_command(args(), run_dir)


def test_prepare_uses_accepted_issues_without_writing_on_dry_run(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    issuegen = run_dir / "issuegen"
    issuegen.mkdir()
    (issuegen / "accepted.jsonl").write_text('{"instance_id":"one"}\n', encoding="utf-8")
    template = tmp_path / "template.yaml"
    template.write_text('schema_version: 1\nsource: {}\nselection: {}\n', encoding="utf-8")
    command, _ = MODULE.build_command(args(stage="prepare", prepare_config=template), run_dir)
    assert command[-1] == "--dry-run"
    assert command[command.index("--run-id") + 1] == run_dir.name
    assert not (run_dir / "task-prep").exists()
    config = MODULE.prepare_config(run_dir, template, dry_run=False)
    assert yaml.safe_load(config.read_text(encoding="utf-8"))["source"]["dataset"] == str(issuegen / "accepted.jsonl")
    assert MODULE.prepare_config(run_dir, template, dry_run=False) == config


def test_frameworks_and_evaluations_are_isolated(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    prepared(run_dir)
    mini_bin = tmp_path / "mini-extra"
    mini_bin.touch()
    mini_config = tmp_path / "mini.yaml"
    mini_config.write_text('model: {}\n', encoding="utf-8")
    swe_command, _ = MODULE.build_command(args(), run_dir)
    mini_command, _ = MODULE.build_command(
        args(framework="mini-swe-agent", mini_executable=mini_bin, mini_config=mini_config), run_dir
    )
    assert str(run_dir / "rollouts" / "swe-agent") in swe_command
    assert swe_command[swe_command.index("--sweagent-root") + 1] == str(tmp_path / "vendor" / "SWE-agent")
    assert str(run_dir / "rollouts" / "mini-swe-agent" / "deepseek-t0") in mini_command
    assert swe_command[swe_command.index("--run-id") + 1] == "deepseek-t0"
    for framework in ("swe-agent", "mini-swe-agent"):
        rollout, evaluation = MODULE.output_paths(run_dir, framework, "deepseek-t0")
        rollout.mkdir(parents=True)
        (rollout / "preds.json").write_text('{"one":{"model_patch":""}}', encoding="utf-8")
        command, _ = MODULE.build_command(args(framework=framework, stage="eval"), run_dir)
        assert command[command.index("--predictions") + 1] == str(rollout / "preds.json")
        assert command[command.index("--output-root") + 1] == str(evaluation.parent)
        assert command[command.index("--run-id") + 1] == "deepseek-t0"


def test_existing_rollout_requires_resume(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    prepared(run_dir)
    rollout, _ = MODULE.output_paths(run_dir, "swe-agent", "deepseek-t0")
    rollout.mkdir(parents=True)
    (rollout / "marker").touch()
    with pytest.raises(RuntimeError, match="--resume"):
        MODULE.build_command(args(), run_dir)
    command, _ = MODULE.build_command(args(resume=True), run_dir)
    assert "--resume" in command


def test_evaluation_requires_predictions_and_resumes_in_matching_directory(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    prepared(run_dir)
    rollout, evaluation = MODULE.output_paths(run_dir, "mini-swe-agent", "model-t07")
    with pytest.raises(FileNotFoundError, match="Agent predictions"):
        MODULE.build_command(args(framework="mini-swe-agent", rollout_id="model-t07", stage="eval"), run_dir)
    rollout.mkdir(parents=True)
    (rollout / "preds.json").write_text("{}", encoding="utf-8")
    evaluation.mkdir(parents=True)
    (evaluation / "marker").touch()
    with pytest.raises(RuntimeError, match="--resume"):
        MODULE.build_command(args(framework="mini-swe-agent", rollout_id="model-t07", stage="eval"), run_dir)
    command, _ = MODULE.build_command(
        args(framework="mini-swe-agent", rollout_id="model-t07", stage="eval", resume=True), run_dir
    )
    assert command[command.index("--output-root") + 1] == str(evaluation.parent)
    assert "--resume" in command


def test_config_mode_rejects_mixed_legacy_options_and_missing_experiment(tmp_path):
    path = tmp_path / "rollout.yaml"
    with pytest.raises(SystemExit):
        MODULE.parse_args(["--config", str(path), "--stage", "agent"])
    with pytest.raises(SystemExit):
        MODULE.parse_args(["--config", str(path), "--stage", "prepare", "--run-dir", "results/one"])
    with pytest.raises(SystemExit):
        MODULE.parse_args(["--config", str(path), "--stage", "gold", "--experiment", "mini-swe-agent"])
    with pytest.raises(SystemExit):
        MODULE.parse_args(["--run-dir", "results/one", "--stage", "gold"])


def test_configured_commands_route_both_frameworks_and_gold(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    path = rollout_config(tmp_path, run_dir)
    prepared(run_dir)
    mini_bin = tmp_path / "mini-extra"
    mini_bin.touch()
    for experiment in ("mini-swe-agent", "swe-agent"):
        cli = MODULE.parse_args(["--config", str(path), "--stage", "agent",
                                 "--experiment", experiment, "--dry-run"])
        selected, resolved, snapshot, content = MODULE.configured_args(cli)
        selected.mini_executable = mini_bin
        command, environment = MODULE.build_command(selected, resolved)
        assert environment is None
        assert selected.framework == experiment
        assert selected.rollout_id == "deepseek-flash-t07"
        assert experiment in str(snapshot)
        assert "deepseek-flash-t07" in str(snapshot)
        assert "DEEPSEEK_API_KEY" in content
        assert "--workers" in command
        if experiment == "mini-swe-agent":
            assert "model.model_kwargs.temperature=0.7" in command
            assert "agent.step_limit=250" in command
            assert "model.model_kwargs.max_tokens=4096" in command
        else:
            assert "--temperature" in command
            assert command[command.index("--temperature") + 1] == "0.7"
            assert command[command.index("--per-instance-call-limit") + 1] == "30"
        rollout = run_dir / "rollouts" / experiment / "deepseek-flash-t07"
        rollout.mkdir(parents=True)
        (rollout / "preds.json").write_text("{}", encoding="utf-8")
        eval_cli = MODULE.parse_args(["--config", str(path), "--stage", "eval",
                                      "--experiment", experiment, "--dry-run"])
        eval_args, _, _, _ = MODULE.configured_args(eval_cli)
        eval_command, _ = MODULE.build_command(eval_args, run_dir)
        assert eval_command[eval_command.index("--predictions") + 1] == str(rollout / "preds.json")
        assert eval_command[eval_command.index("--workers") + 1] == "1"

    gold_cli = MODULE.parse_args(["--config", str(path), "--stage", "gold", "--dry-run"])
    gold_args, _, _, _ = MODULE.configured_args(gold_cli)
    gold_command, _ = MODULE.build_command(gold_args, run_dir)
    assert gold_command[gold_command.index("--predictions") + 1] == "gold"
    assert gold_command[gold_command.index("--output-root") + 1] == str(run_dir / "evaluations" / "gold")


def test_configured_dry_run_writes_nothing_and_agent_needs_paid_gate(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    path = rollout_config(tmp_path, run_dir)
    monkeypatch.setattr(MODULE.subprocess, "run", lambda *args, **kwargs: pytest.fail("dry-run called subprocess"))
    issuegen = run_dir / "issuegen"
    issuegen.mkdir()
    (issuegen / "accepted.jsonl").write_text('{"instance_id":"one"}\n', encoding="utf-8")
    MODULE.main(["--config", str(path), "--stage", "prepare", "--dry-run"])
    assert not (run_dir / "task-prep").exists()
    assert not (run_dir / "meta").exists()
    prepared(run_dir)
    with pytest.raises(RuntimeError, match="allow-api-calls"):
        MODULE.main(["--config", str(path), "--stage", "agent", "--experiment", "swe-agent"])
    assert not (run_dir / "meta").exists()


@pytest.mark.parametrize("framework", ("mini-swe-agent", "swe-agent"))
def test_agent_auto_prepares_only_selected_framework(tmp_path, monkeypatch, framework):
    run_dir = sample_run(tmp_path, monkeypatch)
    config = rollout_config(tmp_path, run_dir)
    monkeypatch.setattr(MODULE, "api_environment", lambda _: {})
    state = {"prepared": False}
    stages = []

    def command(selected, directory):
        assert directory == run_dir
        stages.append((selected.stage, selected.framework))
        return [selected.stage], None

    def run(command, **_):
        if command == ["prepare"]:
            state["prepared"] = True
        return None

    monkeypatch.setattr(MODULE, "build_command", command)
    monkeypatch.setattr(MODULE, "preparation_complete", lambda *_a, **_k: state["prepared"])
    monkeypatch.setattr(MODULE.subprocess, "run", run)
    MODULE.main(["--config", str(config), "--stage", "rollout", "--experiment", framework,
                 "--allow-api-calls"])
    assert [stage for stage, _ in stages] == ["prepare", "rollout"]
    assert stages[-1] == ("rollout", framework)
    assert (run_dir / "meta" / "rollout-configs" / "prepare.yaml").is_file()


def test_agent_skips_completed_preparation(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    prepared(run_dir)
    config = rollout_config(tmp_path, run_dir)
    monkeypatch.setattr(MODULE, "api_environment", lambda _: {})
    monkeypatch.setattr(MODULE, "preparation_complete", lambda *_a, **_k: True)
    stages = []
    monkeypatch.setattr(MODULE, "build_command", lambda selected, _: (stages.append(selected.stage) or
                                                              [selected.stage], None))
    monkeypatch.setattr(MODULE.subprocess, "run", lambda command, **_: None)
    MODULE.main(["--config", str(config), "--stage", "rollout", "--experiment", "swe-agent",
                 "--allow-api-calls"])
    assert stages == ["rollout"]
    assert not (run_dir / "meta" / "rollout-configs" / "prepare.yaml").exists()


def test_agent_resumes_partial_preparation(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    config = rollout_config(tmp_path, run_dir)
    metadata = run_dir / "task-prep" / run_dir.name / "private" / "run-metadata.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(MODULE, "api_environment", lambda _: {})
    state = {"prepared": False}
    stages = []

    def command(selected, _):
        stages.append((selected.stage, selected.resume))
        return [selected.stage], None

    def run(command, **_):
        if command == ["prepare"]:
            state["prepared"] = True

    monkeypatch.setattr(MODULE, "build_command", command)
    monkeypatch.setattr(MODULE, "preparation_complete", lambda *_a, **_k: state["prepared"])
    monkeypatch.setattr(MODULE.subprocess, "run", run)
    MODULE.main(["--config", str(config), "--stage", "rollout", "--experiment", "mini-swe-agent",
                 "--allow-api-calls"])
    assert stages == [("prepare", True), ("rollout", False)]


def test_agent_stops_if_preparation_does_not_finish(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    config = rollout_config(tmp_path, run_dir)
    monkeypatch.setattr(MODULE, "api_environment", lambda _: {})
    monkeypatch.setattr(MODULE, "preparation_complete", lambda *_a, **_k: False)
    stages = []
    monkeypatch.setattr(MODULE, "build_command", lambda selected, _: (stages.append(selected.stage) or
                                                              [selected.stage], None))
    monkeypatch.setattr(MODULE.subprocess, "run", lambda command, **_: None)
    with pytest.raises(RuntimeError, match="did not complete"):
        MODULE.main(["--config", str(config), "--stage", "rollout", "--experiment", "swe-agent",
                     "--allow-api-calls"])
    assert stages == ["prepare"]


def test_agent_dry_run_plans_prepare_without_running_it(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    config = rollout_config(tmp_path, run_dir)
    issuegen = run_dir / "issuegen"
    issuegen.mkdir()
    (issuegen / "accepted.jsonl").write_text('{"instance_id":"one"}\n', encoding="utf-8")
    monkeypatch.setattr(MODULE.subprocess, "run", lambda *_a, **_k: pytest.fail("dry-run launched work"))
    MODULE.main(["--config", str(config), "--stage", "agent", "--experiment", "swe-agent",
                 "--dry-run"])
    assert not (run_dir / "task-prep").exists()
    assert not (run_dir / "meta").exists()


def test_configured_snapshot_blocks_changed_resume(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    path = rollout_config(tmp_path, run_dir)
    cli = MODULE.parse_args(["--config", str(path), "--stage", "agent",
                             "--experiment", "swe-agent", "--resume", "--dry-run"])
    _, _, snapshot, content = MODULE.configured_args(cli)
    MODULE.save_snapshot(snapshot, content)
    MODULE.check_snapshot(snapshot, content)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["experiments"]["swe-agent"]["temperature"] = 0.1
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    _, _, _, changed = MODULE.configured_args(cli)
    with pytest.raises(RuntimeError, match="new rollout_id"):
        MODULE.check_snapshot(snapshot, changed)
    data["experiments"]["swe-agent"]["rollout_id"] = "deepseek-flash-t01"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    _, _, new_snapshot, new_content = MODULE.configured_args(cli)
    assert new_snapshot != snapshot
    MODULE.check_snapshot(new_snapshot, new_content)


def test_configured_gold_requires_selection(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    path = rollout_config(tmp_path, run_dir)
    cli = MODULE.parse_args(["--config", str(path), "--stage", "gold", "--dry-run"])
    gold_args, _, _, _ = MODULE.configured_args(cli)
    with pytest.raises(FileNotFoundError, match="Prepared private selection"):
        MODULE.build_command(gold_args, run_dir)


def test_configured_gold_requires_resume_for_existing_result(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    path = rollout_config(tmp_path, run_dir)
    prepared(run_dir)
    destination = run_dir / "evaluations" / "gold" / "gold-one"
    destination.mkdir(parents=True)
    (destination / "report.json").write_text("{}", encoding="utf-8")
    cli = MODULE.parse_args(["--config", str(path), "--stage", "gold", "--dry-run"])
    gold_args, _, _, _ = MODULE.configured_args(cli)
    with pytest.raises(RuntimeError, match="--resume"):
        MODULE.build_command(gold_args, run_dir)
    gold_args.resume = True
    command, _ = MODULE.build_command(gold_args, run_dir)
    assert "--resume" in command


def test_effective_snapshot_overrides_native_values_and_redacts_secrets(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    path = rollout_config(tmp_path, run_dir)
    (tmp_path / "native.yaml").write_text(
        "model:\n  model_name: old-model\n  model_kwargs:\n    temperature: 1\n    api_key: literal-secret\n",
        encoding="utf-8",
    )
    cli = MODULE.parse_args(["--config", str(path), "--stage", "agent",
                             "--experiment", "mini-swe-agent", "--dry-run"])
    _, _, _, snapshot = MODULE.configured_args(cli)
    effective = yaml.safe_load(snapshot)["effective_framework_config"]
    assert effective["model"]["model_name"] == "deepseek/deepseek-flash"
    assert effective["model"]["model_kwargs"]["temperature"] == 0.7
    assert effective["agent"]["step_limit"] == 250
    assert effective["model"]["model_kwargs"]["max_tokens"] == 4096
    assert "literal-secret" not in snapshot


def test_rollout_config_rejects_unknown_parameters(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    path = rollout_config(tmp_path, run_dir)
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config["experiments"]["mini-swe-agent"]["api_key"] = "should-not-be-here"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown mini-swe-agent keys"):
        MODULE.load_rollout_config(path)


def test_repository_bundle_is_resolved_and_snapshotted(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    prepared(run_dir)
    config_path = rollout_config(tmp_path, run_dir)
    native_path = tmp_path / "native.yaml"
    native_path.write_text(
        "agent:\n  tools:\n    bundles:\n    - path: lab:tools/agent/edit_anthropic\n",
        encoding="utf-8",
    )
    bundle = tmp_path / "tools" / "agent" / "edit_anthropic"
    bundle.mkdir(parents=True)
    (bundle / "config.yaml").write_text("tools: {}\n", encoding="utf-8")
    tool = bundle / "install.sh"
    tool.write_text("echo original\n", encoding="utf-8")
    cli = MODULE.parse_args(["--config", str(config_path), "--stage", "agent",
                             "--experiment", "swe-agent", "--dry-run"])
    selected, _, snapshot_path, content = MODULE.configured_args(cli)
    assert yaml.safe_load(content)["lab_bundle_digests"]
    command, _ = MODULE.build_command(selected, run_dir)
    resolved = Path(command[command.index("--agent-config") + 1])
    assert resolved != native_path
    assert not resolved.exists()
    selected.dry_run = False
    selected.allow_api_calls = True
    MODULE.build_command(selected, run_dir)
    rendered = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    assert rendered["agent"]["tools"]["bundles"][0]["path"] == str(bundle)
    MODULE.save_snapshot(snapshot_path, content)
    tool.write_text("echo changed\n", encoding="utf-8")
    _, _, _, changed = MODULE.configured_args(cli)
    with pytest.raises(RuntimeError, match="new rollout_id"):
        MODULE.check_snapshot(snapshot_path, changed)


def test_swe_agent_single_instance_limit(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    prepared(run_dir)
    config_path = rollout_config(tmp_path, run_dir)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["experiments"]["swe-agent"]["max_instances"] = 1
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    cli = MODULE.parse_args(["--config", str(config_path), "--stage", "agent",
                             "--experiment", "swe-agent", "--dry-run"])
    selected, _, _, _ = MODULE.configured_args(cli)
    command, _ = MODULE.build_command(selected, run_dir)
    assert command[command.index("--limit") + 1] == "1"
    data["experiments"]["swe-agent"]["max_instances"] = 0
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError, match="positive integer"):
        MODULE.load_rollout_config(config_path)


def test_repository_bundle_rejects_escaping_path(tmp_path, monkeypatch):
    sample_run(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="Invalid repository-relative"):
        MODULE.lab_bundle_path("lab:../outside")


def test_smoke_native_configs_share_directory():
    root = Path(__file__).parents[1]
    config = yaml.safe_load((root / "configs" / "rollout" / "smoke.yaml").read_text(encoding="utf-8"))
    assert set(config["experiments"]) == {"mini-swe-agent", "swe-agent"}
    for framework, settings in config["experiments"].items():
        assert MODULE.SAFE_ID.fullmatch(settings["rollout_id"])
        native = Path(settings["native_config"])
        assert native.parent == Path("configs/agent")
        assert (root / native).is_file(), framework


def test_full_rollout_config_matches_recorded_run_parameters():
    root = Path(__file__).parents[1]
    config = MODULE.load_rollout_config(root / "configs" / "rollout" / "full.yaml")
    assert config["run_dir"] == "results/full"
    assert config["prepare"]["template"] == "configs/rollout/shared-tasks.yaml"
    assert config["gold"]["evaluation"] == {
        "workers": 4, "memory_limit": "4g", "timeout_seconds": 120,
    }
    swe = config["experiments"]["swe-agent"]
    assert (swe["workers"], swe["per_instance_call_limit"],
            swe["per_instance_cost_limit"], swe["total_cost_limit"]) == (2, 30, 2.0, 10.0)
    assert swe["evaluation"] == {"workers": 4, "memory_limit": "4g", "timeout_seconds": 120}
    mini = config["experiments"]["mini-swe-agent"]
    assert (mini["workers"], mini["step_limit"], mini["evaluation"]["workers"]) == (10, 250, 6)
    for item in (swe, mini):
        assert item["model_name"] == "deepseek/deepseek-flash"
        assert item["temperature"] == 0
        assert (root / item["native_config"]).is_file()


def test_config_stage_defaults_to_full_agent_and_gold_is_explicit(tmp_path):
    config = str(tmp_path / "rollout.yaml")
    cli = MODULE.parse_args(["--config", config, "--experiment", "mini-swe-agent"])
    assert cli.stage == "agent" and not cli.with_gold
    assert MODULE.parse_args(["--config", config, "--experiment", "swe-agent",
                              "--with-gold"]).with_gold
    with pytest.raises(SystemExit):
        MODULE.parse_args(["--config", config])
    with pytest.raises(SystemExit):
        MODULE.parse_args(["--config", config, "--experiment", "swe-agent",
                           "--stage", "eval", "--with-gold"])
    with pytest.raises(SystemExit):
        MODULE.parse_args(["--run-dir", "results/one", "--framework", "swe-agent",
                           "--rollout-id", "one"])


def test_optional_gold_config_is_checked_before_any_rollout(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    config = rollout_config(tmp_path, run_dir)
    value = yaml.safe_load(config.read_text(encoding="utf-8"))
    value.pop("gold")
    config.write_text(yaml.safe_dump(value), encoding="utf-8")
    MODULE.load_rollout_config(config)  # Gold configuration is optional by default.
    with pytest.raises(ValueError, match="no gold section"):
        MODULE.main(["--config", str(config), "--experiment", "mini-swe-agent",
                     "--with-gold", "--dry-run"])


@pytest.mark.parametrize("framework", ("mini-swe-agent", "swe-agent"))
@pytest.mark.parametrize("with_gold", (False, True))
def test_full_agent_runs_only_missing_stages(tmp_path, monkeypatch, framework, with_gold):
    run_dir = sample_run(tmp_path, monkeypatch)
    config = rollout_config(tmp_path, run_dir)
    state = {"rollout": False, "eval": False, "gold": False, "sft": False}
    seen = []
    monkeypatch.setattr(MODULE, "preparation_complete", lambda *_a, **_k: True)
    monkeypatch.setattr(MODULE, "expected_ids", lambda *_a, **_k: ["one"])
    monkeypatch.setattr(MODULE, "rollout_complete", lambda *_a, **_k: state["rollout"])
    monkeypatch.setattr(MODULE, "evaluation_complete", lambda _d, _i, *, gold: state["gold" if gold else "eval"])
    monkeypatch.setattr(MODULE, "sft_complete", lambda *_a, **_k: state["sft"])
    monkeypatch.setattr(MODULE, "ensure_prepared", lambda *_a, **_k: True)
    monkeypatch.setattr(MODULE, "api_environment", lambda *_a: {})
    monkeypatch.setattr(MODULE, "build_command", lambda selected, _directory: ([selected.stage], None))

    def run(command, **_kwargs):
        seen.append(command[0])
        state[command[0]] = True

    monkeypatch.setattr(MODULE.subprocess, "run", run)
    argv = ["--config", str(config), "--experiment", framework, "--allow-api-calls"]
    if with_gold:
        argv.append("--with-gold")
    MODULE.main(argv)
    assert seen == (["rollout", "eval", "gold", "sft"] if with_gold
                    else ["rollout", "eval", "sft"])
    seen.clear()
    MODULE.main(["--config", str(config), "--experiment", framework] +
                (["--with-gold"] if with_gold else []))
    assert seen == []  # Completed rollout needs no paid-call flag.
    state["eval"] = False
    state["sft"] = False
    MODULE.main(["--config", str(config), "--experiment", framework] +
                (["--with-gold"] if with_gold else []))
    assert seen == ["eval", "sft"]  # Fill only the missing post-rollout stages.


def test_full_agent_requires_resume_for_partial_rollout(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    config = rollout_config(tmp_path, run_dir)
    prepared(run_dir)
    rollout = run_dir / "rollouts" / "swe-agent" / "deepseek-flash-t07"
    rollout.mkdir(parents=True)
    (rollout / "marker").touch()
    with pytest.raises(RuntimeError, match="--resume"):
        MODULE.main(["--config", str(config), "--experiment", "swe-agent", "--dry-run"])


def test_sft_completeness_requires_all_ids_and_training_files(tmp_path, monkeypatch):
    run_dir = sample_run(tmp_path, monkeypatch)
    _, evaluation = MODULE.output_paths(run_dir, "swe-agent", "one")
    evaluation.mkdir(parents=True)
    (evaluation / "report.json").write_text('{"resolved":1}', encoding="utf-8")
    (evaluation / "one").mkdir()
    (evaluation / "one" / "report.json").write_text('{"resolved":true}', encoding="utf-8")
    out = run_dir / "sft" / "swe-agent" / "one"
    out.mkdir(parents=True)
    (out / "manifest.json").write_text('{"source_ids":["one"],"evaluation_resolved":1}', encoding="utf-8")
    (out / "all.jsonl").write_text('{"instance_id":"one","resolved":true}\n', encoding="utf-8")
    (out / "resolved.jsonl").write_text('{"instance_id":"one","resolved":true}\n', encoding="utf-8")
    assert not MODULE.sft_complete(out, evaluation, ["one"])
    (out / "resolved_chat.jsonl").write_text('{"messages":[]}', encoding="utf-8")
    (out / "dataset_info.json").write_text('{}', encoding="utf-8")
    assert MODULE.sft_complete(out, evaluation, ["one"])
