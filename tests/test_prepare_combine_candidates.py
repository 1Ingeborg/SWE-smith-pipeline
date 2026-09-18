import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare-combine-candidates.py"
SPEC = importlib.util.spec_from_file_location("prepare_combine_candidates", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_single_validated_path_prefers_namespaced_output(tmp_path):
    legacy = tmp_path / "validated.jsonl"
    legacy.write_text("legacy\n", encoding="utf-8")
    assert MODULE.single_validated_path(tmp_path) == legacy

    staged = tmp_path / "validated-single.jsonl"
    staged.write_text("single\n", encoding="utf-8")
    assert MODULE.single_validated_path(tmp_path) == staged


def test_combine_specs_reuses_config_and_enables_requested_strategies():
    config = {
        "generation_strategies": {
            "combine_file": {"enabled": False, "num_patches": 3},
        }
    }

    result = MODULE.combine_specs(
        config,
        ["combine_file", "combine_module"],
        num_patches=2,
        max_combos=7,
    )

    assert result == {
        "combine_file": {"enabled": True, "num_patches": 2, "max_combos": 7},
        "combine_module": {"enabled": True, "num_patches": 2, "max_combos": 7},
    }
