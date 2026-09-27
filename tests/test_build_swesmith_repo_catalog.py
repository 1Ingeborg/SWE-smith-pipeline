import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "src" / "swesmith_lab" / "pipeline" / "catalog.py"
SPEC = importlib.util.spec_from_file_location("repo_catalog", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_build_catalog_sorts_and_counts() -> None:
    rows = [
        {"instance_id": "Org__A.123.func_pm_remove_assign__one", "repo": "Org__A.123", "image_name": "image-a"},
        {"instance_id": "Org__B.456.func_pm_remove_cond__two", "repo": "Org__B.456", "image_name": "image-b"},
        {"instance_id": "Org__A.123.func_pm_remove_assign__three", "repo": "Org__A.123", "image_name": "image-a"},
    ]

    catalog = MODULE.build_catalog(rows, sample_size=1)

    assert catalog["total_rows"] == 3
    assert catalog["repository_count"] == 2
    assert catalog["repositories"][0]["repo"] == "Org__A.123"
    assert catalog["repositories"][0]["count"] == 2
    assert catalog["repositories"][0]["primary_image_name"] == "image-a"
    assert catalog["repositories"][0]["strategy_counts"] == {"func_pm_remove_assign": 2}
    assert len(catalog["repositories"][0]["sample_instance_ids"]) == 1
