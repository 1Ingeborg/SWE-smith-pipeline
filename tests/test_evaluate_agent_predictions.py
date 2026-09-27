import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "src" / "swesmith_lab" / "agent" / "evaluate.py"
SPEC = importlib.util.spec_from_file_location("evaluate_agent_predictions", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_normalize_predictions_supports_sweagent_mapping():
    predictions = MODULE.normalize_predictions(
        {
            "agent-task-1": {
                "model_name_or_path": "test-model",
                "model_patch": "diff --git a/a.py b/a.py\n",
            }
        }
    )

    assert predictions["agent-task-1"]["instance_id"] == "agent-task-1"
    assert predictions["agent-task-1"]["model_name_or_path"] == "test-model"


def test_normalize_predictions_rejects_key_id_mismatch():
    with pytest.raises(ValueError, match="mismatch"):
        MODULE.normalize_predictions(
            {
                "agent-task-1": {
                    "instance_id": "agent-task-2",
                    "model_patch": "patch",
                }
            }
        )


def test_gold_predictions_keep_private_mapping():
    tasks = {
        "opaque-1": {
            "_agent_instance_id": "opaque-1",
            "instance_id": "repo.strategy__secret",
            "patch": "bug patch",
        }
    }

    predictions = MODULE.gold_predictions(tasks)

    assert predictions == {
        "opaque-1": {
            "instance_id": "opaque-1",
            "model_patch": "bug patch",
            "model_name_or_path": "gold-reverse-mutation",
        }
    }


def test_summary_counts_resolution_and_infrastructure_status():
    summary = MODULE.summarize_reports(
        reports=[
            {"status": "completed", "resolved": True},
            {"status": "completed", "resolved": False},
            {"status": "patch_apply_error", "resolved": False},
        ],
        selected=4,
        missing_predictions=["opaque-4"],
        unexpected_predictions=[],
    )

    assert summary["evaluated"] == 3
    assert summary["resolved"] == 1
    assert summary["resolution_rate"] == pytest.approx(1 / 3)
    assert summary["status_counts"] == {
        "completed": 2,
        "patch_apply_error": 1,
    }


@pytest.mark.parametrize("value", ["/etc/passwd", "../x", "a/../../x", "", "."])
def test_safe_repo_path_rejects_traversal(value):
    with pytest.raises(ValueError):
        MODULE.safe_repo_path(value)
