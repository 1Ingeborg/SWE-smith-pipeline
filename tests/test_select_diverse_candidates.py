import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "select-diverse-candidates.py"
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
