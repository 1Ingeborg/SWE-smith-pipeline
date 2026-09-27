import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "src" / "swesmith_lab" / "pipeline" / "validation.py"
SPEC = importlib.util.spec_from_file_location("run_validation", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_aggregate_results_updates_queue(tmp_path):
    candidates = [
        {"instance_id": "repo.a", "repo": "repo", "queue_status": "pending_validation"},
        {"instance_id": "repo.b", "repo": "repo", "queue_status": "pending_validation"},
        {"instance_id": "repo.c", "repo": "repo", "queue_status": "pending_validation"},
    ]
    first = tmp_path / "validation-workspace"
    write_json(first / "manifest.json", {"status": "running"})
    write_jsonl(first / "validated" / "repo.jsonl", [{"instance_id": "repo.a"}])
    write_json(
        first / "validated" / "repo.summary.json",
        {"rejected": [{"instance_id": "repo.b", "reason": "no_FAIL_TO_PASS"}]},
    )

    result = MODULE.aggregate_results(tmp_path, candidates)

    assert result == {
        "queue": "single",
        "validated_count": 1,
        "rejected_count": 1,
        "pending_count": 1,
        "complete": False,
    }
    queue = MODULE.read_jsonl(tmp_path / "candidates.jsonl")
    assert [row["queue_status"] for row in queue] == [
        "validated",
        "rejected",
        "pending_validation",
    ]
    assert queue[1]["rejection_reason"] == "no_FAIL_TO_PASS"


def test_aggregate_is_complete_when_validation_finished(tmp_path):
    candidates = [{"instance_id": str(index), "repo": "repo"} for index in range(3)]
    directory = tmp_path / "validation-workspace"
    write_json(directory / "manifest.json", {"status": "completed"})
    write_json(
        directory / "validated" / "repo.summary.json",
        {
            "rejected": [
                {"instance_id": instance_id, "reason": "no_FAIL_TO_PASS"}
                for instance_id in ("0", "1", "2")
            ]
        },
    )

    result = MODULE.aggregate_results(tmp_path, candidates)

    assert result["complete"] is True
    assert result["pending_count"] == 0


def test_combine_aggregate_keeps_single_and_combined_validated_rows(tmp_path):
    candidates = [{"instance_id": "combined", "repo": "repo"}]
    write_jsonl(tmp_path / "validated-single.jsonl", [{"instance_id": "single"}])
    directory = tmp_path / "combine-validation-workspace"
    write_json(directory / "manifest.json", {"status": "completed"})
    write_jsonl(directory / "validated" / "repo.jsonl", [{"instance_id": "combined"}])

    result = MODULE.aggregate_results(
        tmp_path, candidates, queue="combine"
    )

    assert result["queue"] == "combine"
    assert result["complete"] is True
    assert [
        row["instance_id"] for row in MODULE.read_jsonl(tmp_path / "validated.jsonl")
    ] == ["single", "combined"]


def test_update_root_manifest_counts_single_and_combine_outputs(tmp_path):
    write_jsonl(
        tmp_path / "validated.jsonl",
        [{"instance_id": str(index)} for index in range(4)],
    )
    manifest = {"target_validated": 5, "artifacts": {"candidates": "single"}}
    aggregate = {"complete": True, "validated_count": 1}
    candidate_path = tmp_path / "combine-candidates.jsonl"

    MODULE.update_root_manifest(
        manifest, tmp_path, candidate_path, aggregate, "combine"
    )

    assert manifest["validated_count"] == 4
    assert manifest["validated_shortfall"] == 1
    assert manifest["status"] == "completed_validation_with_shortfall"
    assert manifest["artifacts"]["candidates"] == "single"
    assert manifest["artifacts"]["combine_candidates"] == str(candidate_path)


def test_rebuild_validation_report_index_links_single_and_combine_reports(tmp_path):
    for root_name, instance_id in (
        ("validation-workspace", "single"),
        ("combine-validation-workspace", "combined"),
    ):
        report = (
            tmp_path
            / root_name
            / "workspace"
            / "logs"
            / "run_validation"
            / "repo"
            / instance_id
            / "report.json"
        )
        write_json(report, {"ok": True})

    assert MODULE.rebuild_validation_report_index(tmp_path) == 2
    assert (tmp_path / "validation-reports" / "repo" / "single" / "report.json").is_file()
    assert (tmp_path / "validation-reports" / "repo" / "combined" / "report.json").is_file()


def test_running_validation_is_not_complete(tmp_path):
    candidates = [{"instance_id": "0", "repo": "repo"}]
    directory = tmp_path / "validation-workspace"
    write_json(directory / "manifest.json", {"status": "running"})
    write_json(
        directory / "validated" / "repo.summary.json",
        {"rejected": [{"instance_id": "0", "reason": "no_FAIL_TO_PASS"}]},
    )

    result = MODULE.aggregate_results(tmp_path, candidates)

    assert result["complete"] is False
