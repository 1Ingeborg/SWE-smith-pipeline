import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "run-task-generation.py"
SPEC = importlib.util.spec_from_file_location("run_multirepo_pipeline", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_data_root_environment_overrides_local_env(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, "LAB_REPO", tmp_path)
    (tmp_path / ".env").write_text("SWE_LAB_DATA_ROOT=/from-file\n", encoding="utf-8")
    monkeypatch.setenv("SWE_LAB_DATA_ROOT", str(tmp_path / "from-process"))
    assert MODULE.runtime_data_root() == (tmp_path / "from-process").resolve()
    assert MODULE.resolve_path("${SWE_LAB_DATA_ROOT}/results") == (tmp_path / "from-process" / "results").resolve()


def test_new_pipeline_defaults_to_checkout_results(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(MODULE, "LAB_REPO", tmp_path)
    config = {"paths": {}, "validation_workers": 1, "target_validated": None}
    plan = MODULE.RepoPlan("owner__one.abc", "swebench/one", None, 2, 42, 42, 2, 20, 30, 60)

    MODULE.print_plan(config, [plan], tmp_path / "config.yaml", "new-run", "issues")

    assert f"Run directory: {tmp_path / 'results' / 'new-run'}" in capsys.readouterr().out


def test_stage_paths_choose_new_or_existing_legacy_layout(tmp_path):
    fresh = MODULE.run_paths(tmp_path / "fresh")
    assert fresh.meta == tmp_path / "fresh" / "meta"
    assert fresh.single_workspace == tmp_path / "fresh" / "single" / "workspace"
    assert fresh.combine_workspace == tmp_path / "fresh" / "combine" / "workspace"
    assert fresh.candidate_queue == tmp_path / "fresh" / "single" / "candidates" / "candidates.jsonl"
    assert fresh.single_accepted == tmp_path / "fresh" / "single" / "validation" / "accepted.jsonl"
    assert fresh.combine_accepted == tmp_path / "fresh" / "combine" / "validation" / "accepted.jsonl"
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "manifest.json").write_text("{}", encoding="utf-8")
    assert MODULE.run_paths(legacy).legacy
    assert MODULE.run_paths(legacy).single_workspace == legacy / "mutation-workspace"
    assert MODULE.run_paths(legacy).candidate_queue == legacy / "candidates.jsonl"
    assert MODULE.run_paths(legacy).single_accepted == legacy / "validation" / "validated.jsonl"
    assert MODULE.run_paths(legacy).combine_accepted == legacy / "validation" / "validated.jsonl"
    newer = tmp_path / "newer"
    (newer / "meta").mkdir(parents=True)
    (newer / "meta" / "manifest.json").write_text("{}", encoding="utf-8")
    assert not MODULE.run_paths(newer).legacy


def test_issue_inputs_require_single_and_skip_incomplete_combine(tmp_path):
    single = tmp_path / "single.jsonl"
    combine = tmp_path / "combine.jsonl"
    single.write_text('{"instance_id":"s","repo":"r"}\n', encoding="utf-8")
    rows, summary = MODULE.issue_input_records(single, None, combine_expected=False, combine_stage_complete=False)
    assert [row["instance_id"] for row in rows] == ["s"]
    assert summary["combine_skip_reason"] == "combine_not_configured"
    rows, summary = MODULE.issue_input_records(single, combine, combine_expected=True, combine_stage_complete=False)
    assert len(rows) == 1 and summary["combine_skip_reason"] == "combine_stage_incomplete"
    rows, summary = MODULE.issue_input_records(single, combine, combine_expected=True, combine_stage_complete=True)
    assert len(rows) == 1 and summary["combine_status"] == "skipped"
    combine.write_text('{"instance_id":"c","repo":"r"}\n', encoding="utf-8")
    rows, summary = MODULE.issue_input_records(single, combine, combine_expected=True, combine_stage_complete=True)
    assert [row["instance_id"] for row in rows] == ["s", "c"]
    assert summary["single_count"] == summary["combine_count"] == 1
    combine.write_text('{"instance_id":"c","repo":"r"}\n{invalid\n', encoding="utf-8")
    rows, summary = MODULE.issue_input_records(single, combine, combine_expected=True, combine_stage_complete=True)
    assert len(rows) == 1 and summary["combine_skip_reason"].startswith("combine_unreadable")
    single.write_text("{invalid\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        MODULE.issue_input_records(single, combine, combine_expected=True, combine_stage_complete=True)


def test_issue_evidence_links_keep_stages_separate(tmp_path):
    single_root = tmp_path / "single"
    combine_root = tmp_path / "combine"
    for root, name in ((single_root, "s"), (combine_root, "c")):
        (root / "r" / name).mkdir(parents=True)
    records = [{"repo": "r", "instance_id": "s"}, {"repo": "r", "instance_id": "c"}]
    output = tmp_path / "issuegen" / "validation-evidence"
    MODULE.write_issue_evidence_links(output, records, 1, single_root, combine_root)
    assert (output / "r" / "s").resolve() == single_root / "r" / "s"
    assert (output / "r" / "c").resolve() == combine_root / "r" / "c"
    MODULE.write_issue_evidence_links(output, records, 1, single_root, combine_root)
    (combine_root / "r" / "c").rename(combine_root / "r" / "missing")
    with pytest.raises(FileNotFoundError):
        MODULE.write_issue_evidence_links(output, records, 1, single_root, combine_root)


def test_unknown_issue_backend_is_rejected_before_execution(tmp_path):
    path = write_config(
        tmp_path,
        """
schema_version: 1
issue_generation:
  backend: typo
repositories:
  - repo: owner__one.abc
    image_name: swebench/one
""",
    )
    with pytest.raises(ValueError, match="issue_generation.backend"):
        MODULE.load_config(path)


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


def test_target_result_reports_shortfall():
    assert MODULE.target_result(43, 45) == {
        "target_validated": 45,
        "target_reached": False,
        "validated_shortfall": 2,
    }


def test_target_result_reports_reached_or_unbounded():
    assert MODULE.target_result(46, 45)["target_reached"] is True
    assert MODULE.target_result(10, None) == {
        "target_validated": None,
        "target_reached": True,
        "validated_shortfall": 0,
    }


@pytest.mark.parametrize("run_id", ["batch-1", "qwen.v2", "run_20260914"])
def test_validate_run_id_accepts_safe_names(run_id):
    MODULE.validate_run_id(run_id)


@pytest.mark.parametrize("run_id", ["", "../escape", "has space", "/absolute"])
def test_validate_run_id_rejects_unsafe_names(run_id):
    with pytest.raises(ValueError):
        MODULE.validate_run_id(run_id)


def test_yaml_files_semantically_equal_ignores_comments_and_formatting(tmp_path):
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text("# old comment\nvalue: 1\nitems: [a, b]\n", encoding="utf-8")
    second.write_text("value: 1\nitems:\n  - a\n  - b\n", encoding="utf-8")

    assert MODULE.yaml_files_semantically_equal(first, second) is True


def test_yaml_files_semantically_equal_rejects_value_changes(tmp_path):
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    first.write_text("value: 1\n", encoding="utf-8")
    second.write_text("value: 2\n", encoding="utf-8")

    assert MODULE.yaml_files_semantically_equal(first, second) is False


def make_plan(**overrides):
    values = {
        "repo": "owner__repo.abc",
        "image_name": "swebench/repo",
        "source_image_name": None,
        "selected_patches": 5,
        "seed": 9,
        "selection_seed": 10,
        "max_bugs_per_modifier": 11,
        "max_entities": 120,
        "max_candidates": 70,
        "timeout_seconds": 300,
    }
    values.update(overrides)
    return MODULE.RepoPlan(**values)


# --- backwards compatibility: the old single-procedural path must not drift ---


def test_config_without_strategies_keeps_single_procedural(tmp_path):
    path = write_config(
        tmp_path,
        """
schema_version: 1
repositories:
  - repo: owner__repo.abc
    image_name: swebench/repo
""",
    )

    config, _ = MODULE.load_config(path)

    assert list(config["generation_strategies"]) == ["procedural"]
    assert config["generation_strategies"]["procedural"]["enabled"] is True


def test_dispatched_procedural_command_equals_legacy_command():
    plan = make_plan()

    legacy = MODULE.generation_command(Path("/venv/python"), plan)
    dispatched = MODULE.generation_commands(
        Path("/venv/python"), plan, {"procedural": {"enabled": True}}
    )

    assert dispatched == [("procedural", legacy)]


def test_procedural_interleave_can_be_switched_off():
    command = MODULE.generation_command(Path("/venv/python"), make_plan(), interleave=False)

    assert "--interleave" not in command
    assert command[-2:] == ["--timeout_seconds", "300"]


# --- strategy configuration parsing ---


def test_load_config_parses_multiple_strategies(tmp_path):
    path = write_config(
        tmp_path,
        """
schema_version: 1
generation:
  strategies:
    procedural:
      enabled: true
      interleave: false
    lm_rewrite:
      enabled: false
      config_file: /cfg/lm_rewrite.yml
    combine_file:
      enabled: true
      num_patches: 3
repositories:
  - repo: owner__repo.abc
    image_name: swebench/repo
""",
    )

    config, _ = MODULE.load_config(path)
    strategies = config["generation_strategies"]

    assert list(strategies) == ["procedural", "lm_rewrite", "combine_file"]
    assert strategies["procedural"]["interleave"] is False
    assert strategies["combine_file"]["num_patches"] == 3


def test_load_config_rejects_unknown_strategy(tmp_path):
    path = write_config(
        tmp_path,
        """
schema_version: 1
generation:
  strategies:
    telepathy:
      enabled: true
repositories:
  - repo: owner__repo.abc
    image_name: swebench/repo
""",
    )

    with pytest.raises(ValueError, match="is not a known strategy"):
        MODULE.load_config(path)


def test_load_config_rejects_non_boolean_enabled(tmp_path):
    path = write_config(
        tmp_path,
        """
schema_version: 1
generation:
  strategies:
    procedural:
      enabled: "yes please"
repositories:
  - repo: owner__repo.abc
    image_name: swebench/repo
""",
    )

    with pytest.raises(ValueError, match="must be a boolean"):
        MODULE.load_config(path)


def test_enabled_strategies_splits_by_stage():
    strategies = {
        "procedural": {"enabled": True},
        "lm_rewrite": {"enabled": False},
        "combine_file": {"enabled": True},
    }

    assert list(MODULE.enabled_strategies(strategies, "generate")) == ["procedural"]
    assert list(MODULE.enabled_strategies(strategies, "post_validate")) == [
        "combine_file"
    ]


# --- command construction ---


def test_llm_strategy_requires_an_explicit_interpreter():
    with pytest.raises(ValueError, match="paths.llm_python is required"):
        MODULE.generation_commands(
            Path("/venv/swesmith/bin/python"),
            make_plan(),
            {"lm_rewrite": {"enabled": True, "config_file": "/cfg.yml"}},
        )


def test_llm_strategy_runs_on_the_llm_interpreter():
    commands = MODULE.generation_commands(
        Path("/venv/swesmith/bin/python"),
        make_plan(),
        {"lm_rewrite": {"enabled": True, "config_file": "/cfg.yml", "n_workers": 2}},
        llm_python=Path("/venv/issuegen/bin/python"),
    )

    name, command = commands[0]
    assert name == "lm_rewrite"
    assert command[0] == "/venv/issuegen/bin/python"
    assert "swesmith.bug_gen.llm.rewrite" in command
    assert command[command.index("--config_file") + 1] == "/cfg.yml"


def test_lm_modify_requires_a_config_file():
    with pytest.raises(ValueError, match="config_file must be a non-empty string"):
        MODULE.lm_modify_command(Path("/venv/python"), make_plan(), {})


def test_combine_command_uses_a_relative_bug_gen_dir():
    # Upstream asserts bug_gen_dir.startswith("logs/bug_gen"), so an absolute
    # path is rejected before any work happens.
    command = MODULE.combine_command(
        Path("/venv/python"),
        "combine_file",
        {"num_patches": 3, "limit_per_file": 15, "max_combos": 40},
        "logs/bug_gen/owner__repo.abc",
    )

    assert command[3] == "logs/bug_gen/owner__repo.abc"
    assert command[3].startswith("logs/bug_gen")
    assert not Path(command[3]).is_absolute()
    assert command[command.index("--num_patches") + 1] == "3"
    assert command[command.index("--limit_per_file") + 1] == "15"
    assert command[command.index("--max_combos") + 1] == "40"


def test_combine_module_command_carries_depth_and_module_limit():
    command = MODULE.combine_command(
        Path("/venv/python"), "combine_module", {}, "logs/bug_gen/owner__repo.abc"
    )

    assert "swesmith.bug_gen.combine.same_module" in command
    assert command[command.index("--limit_per_module") + 1] == "-1"
    assert command[command.index("--depth") + 1] == "3"


def test_combine_limit_accepts_minus_one():
    command = MODULE.combine_command(
        Path("/venv/python"),
        "combine_file",
        {"limit_per_file": -1},
        "logs/bug_gen/owner__repo.abc",
    )

    assert command[command.index("--limit_per_file") + 1] == "-1"


def test_post_validate_strategy_is_not_a_generate_command():
    commands = MODULE.generation_commands(
        Path("/venv/python"),
        make_plan(),
        {"procedural": {"enabled": False}, "combine_file": {"enabled": True}},
    )

    assert commands == []


# --- combine gate and patch filtering ---


def test_write_validation_gate_matches_upstream_convention(tmp_path):
    validated = tmp_path / "validated.jsonl"
    validated.write_text(
        '{"instance_id": "owner__repo.abc.func_pm_op_swap__bbb", "patch": "x"}\n'
        '{"instance_id": "owner__repo.abc.func_pm_remove_cond__ccc"}\n',
        encoding="utf-8",
    )
    gate = tmp_path / "task_insts" / "owner__repo.abc.json"

    assert MODULE.write_validation_gate(validated, gate) == 2

    rows = json.loads(gate.read_text(encoding="utf-8"))
    assert isinstance(rows, list)
    # Upstream compares gate ids against `bug__<suffix>.diff` filenames.
    assert rows[0]["instance_id"].split(".")[-1] == "func_pm_op_swap__bbb"


def test_filter_patch_records_keys_on_instance_id(tmp_path):
    # Combined patches carry no `strategy` field in their metadata, so the
    # filter has to key on the instance-id suffix instead.
    source = tmp_path / "collected.json"
    source.write_text(
        json.dumps(
            [
                {"instance_id": "owner__repo.abc.combine_file__aaa", "patch": "p"},
                {"instance_id": "owner__repo.abc.func_pm_op_swap__bbb", "patch": "q"},
                {"instance_id": "owner__repo.abc.combine_module__ccc", "patch": "r"},
            ]
        ),
        encoding="utf-8",
    )
    destination = tmp_path / "picked.json"

    assert (
        MODULE.filter_patch_records(
            source, destination, ("combine_file", "combine_module")
        )
        == 2
    )

    kept = json.loads(destination.read_text(encoding="utf-8"))
    assert [row["instance_id"] for row in kept] == [
        "owner__repo.abc.combine_file__aaa",
        "owner__repo.abc.combine_module__ccc",
    ]


def test_filter_patch_records_rejects_non_array_input(tmp_path):
    source = tmp_path / "collected.json"
    source.write_text('{"instance_id": "x"}', encoding="utf-8")

    with pytest.raises(ValueError, match="Expected JSON array"):
        MODULE.filter_patch_records(source, tmp_path / "out.json", ("combine_file",))


def test_build_candidate_queue_deduplicates_normalized_patches(tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(
        json.dumps(
            [
                {"instance_id": "repo.op__one", "repo": "repo", "patch": "x  \n"},
                {"instance_id": "repo.op__two", "repo": "repo", "patch": "y\n"},
            ]
        ),
        encoding="utf-8",
    )
    second.write_text(
        json.dumps(
            [{"instance_id": "repo.op__three", "repo": "repo", "patch": "x\r\n"}]
        ),
        encoding="utf-8",
    )
    output = tmp_path / "candidates.jsonl"

    summary = MODULE.build_candidate_queue([first, second], output)

    rows = MODULE.read_jsonl(output)
    assert summary["input_count"] == 3
    assert summary["queued_count"] == 2
    assert summary["duplicate_patches"] == 1
    assert all(row["queue_status"] == "pending_validation" for row in rows)
    assert all(len(row["candidate_sha256"]) == 64 for row in rows)


def test_build_candidate_queue_keeps_same_patch_from_different_repositories(tmp_path):
    source = tmp_path / "candidates.json"
    source.write_text(
        json.dumps(
            [
                {"instance_id": "repo_a.op__one", "repo": "repo_a", "patch": "x\n"},
                {"instance_id": "repo_b.op__two", "repo": "repo_b", "patch": "x\n"},
            ]
        ),
        encoding="utf-8",
    )
    output = tmp_path / "candidates.jsonl"

    summary = MODULE.build_candidate_queue([source], output)

    assert summary["queued_count"] == 2
    assert summary["duplicate_patches"] == 0


def test_build_rejection_report_counts_reasons(tmp_path):
    summary_path = tmp_path / "repo.summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "rejected": [
                    {"instance_id": "one", "reason": "no_FAIL_TO_PASS"},
                    {"instance_id": "two", "reason": "missing_report"},
                    {"instance_id": "three", "reason": "no_FAIL_TO_PASS"},
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "rejected.jsonl"

    result = MODULE.build_rejection_report([summary_path], output)

    assert result["rejected_count"] == 3
    assert result["reason_counts"] == {
        "missing_report": 1,
        "no_FAIL_TO_PASS": 2,
    }
    assert len(MODULE.read_jsonl(output)) == 3


def test_record_stage_seconds_accumulates(monkeypatch):
    values = iter([12.5, 20.0])
    monkeypatch.setattr(MODULE.time, "monotonic", lambda: next(values))
    state = {}

    MODULE.record_stage_seconds(state, "generate", 10.0)
    MODULE.record_stage_seconds(state, "generate", 15.0)

    assert state["stage_seconds"]["generate"] == 7.5
