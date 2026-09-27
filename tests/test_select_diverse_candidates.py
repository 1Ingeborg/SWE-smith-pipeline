import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "src" / "swesmith_lab" / "pipeline" / "select.py"
SPEC = importlib.util.spec_from_file_location("select_diverse", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_select_diverse_prioritizes_distinct_strategies() -> None:
    rows = [
        {"instance_id": "a1", "strategy": "a"},
        {"instance_id": "a2", "strategy": "a"},
        {"instance_id": "b1", "strategy": "b"},
        {"instance_id": "c1", "strategy": "c"},
    ]

    selected = MODULE.select_diverse(rows, limit=3, seed=7)

    assert len(selected) == 3
    assert {row["strategy"] for row in selected} == {"a", "b", "c"}
    assert MODULE.select_diverse(rows, limit=3, seed=7) == selected


def test_select_diverse_deduplicates_equivalent_patches() -> None:
    rows = [
        {"instance_id": "repo.modifier__one", "repo": "repo", "patch": "a  \n"},
        {"instance_id": "repo.modifier__two", "repo": "repo", "patch": "a\r\n"},
        {"instance_id": "repo.other__three", "repo": "repo", "patch": "b\n"},
    ]

    selected = MODULE.select_diverse(rows, limit=3, seed=42)

    assert len(selected) == 2
    assert {row["instance_id"] for row in selected} != {
        "repo.modifier__one",
        "repo.modifier__two",
    }
    assert all(len(row["candidate_sha256"]) == 64 for row in selected)


def test_strategy_name_falls_back_to_instance_id_modifier() -> None:
    assert MODULE.strategy_name({"instance_id": "owner__repo.hash.func_pm_remove__abc"}) == "func_pm_remove"
