from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).parents[1]
PIPELINE_CONFIGS = ROOT / "configs" / "task_generation"


@pytest.mark.parametrize(
    "name",
    ["full.yaml", "smoke.yaml", "multirepo-procedural.yaml", "multirepo-all-strategies.yaml"],
)
def test_new_production_configs_use_reviewed_deepseek(name):
    config = yaml.safe_load((PIPELINE_CONFIGS / name).read_text(encoding="utf-8"))
    assert config["paths"]["swesmith_repo"] == "vendor/SWE-smith"
    assert config["issue_generation"]["backend"] == "deepseek_reviewed"
    assert all(not str(value).startswith("/data/") for value in config["paths"].values())


def test_optional_lm_strategies_use_deepseek():
    config = yaml.safe_load((PIPELINE_CONFIGS / "multirepo-all-strategies.yaml").read_text(encoding="utf-8"))
    strategies = config["generation"]["strategies"]
    for name in ("lm_modify", "lm_rewrite", "pr_mirror"):
        assert strategies[name]["model"] == "deepseek/deepseek-flash"


def test_smoke_covers_single_and_issue_generation():
    config = yaml.safe_load((PIPELINE_CONFIGS / "smoke.yaml").read_text(encoding="utf-8"))
    assert config["repositories"]
    assert config["generation"]["selected_patches"] > 0
    assert config["generation"]["strategies"]["procedural"]["enabled"] is True
    assert config["issue_generation"]["enabled"] is True
    assert config["issue_generation"]["backend"] == "deepseek_reviewed"


def test_full_combine_matches_recorded_same_file_parameters():
    config = yaml.safe_load((PIPELINE_CONFIGS / "full.yaml").read_text(encoding="utf-8"))
    assert config["generation"]["combine_selected_patches"] == 15
    strategies = config["generation"]["strategies"]
    assert strategies["procedural"]["enabled"] is True
    assert strategies["combine_file"] == {
        "enabled": True,
        "num_patches": 2,
        "max_combos": 100,
        "limit_per_file": -1,
    }
    assert "combine_module" not in strategies


def test_official_fallback_and_agent_use_litellm_model_name():
    issue = yaml.safe_load((ROOT / "configs" / "issue_gen" / "ig_v2_deepseek.yaml").read_text(encoding="utf-8"))
    agent = yaml.safe_load((ROOT / "configs" / "agent" / "deepseek-flash.yaml").read_text(encoding="utf-8"))
    assert issue["model"] == "deepseek/deepseek-flash"
    assert agent["agent"]["model"]["name"] == "deepseek/deepseek-flash"
