#!/usr/bin/env python3
"""Validate every pending candidate in an existing queue."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import yaml


SCRIPT_DIR = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--workers", type=int)
    parser.add_argument(
        "--queue",
        choices=("single", "combine"),
        default="single",
        help="Validate ordinary candidates or candidates produced by combine.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def run_command(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    """Run a validation command with output attached to the current terminal."""
    print("+ " + " ".join(command), flush=True)
    process = subprocess.run(command, cwd=cwd, env=env)
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, command)


def rebuild_validation_report_index(run_dir: Path) -> int:
    """Expose validation reports through one issue-generation-compatible tree."""
    index_root = run_dir / "validation-reports"
    index_root.mkdir(parents=True, exist_ok=True)
    linked = 0
    report_patterns = (
        "validation-workspace/workspace/logs/run_validation/*/*/report.json",
        "combine-validation-workspace/workspace/logs/run_validation/*/*/report.json",
        # Read-only compatibility for experiments created by the old interface.
        "validation-batches/batch-*/workspace/logs/run_validation/*/*/report.json",
        "combine-validation-batches/batch-*/workspace/logs/run_validation/*/*/report.json",
    )
    for pattern in report_patterns:
        for report in run_dir.glob(pattern):
            instance_dir = report.parent
            repo = instance_dir.parent.name
            destination = index_root / repo / instance_dir.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() or destination.is_symlink():
                continue
            destination.symlink_to(instance_dir.resolve(), target_is_directory=True)
            linked += 1
    return linked


def aggregate_results(
    run_dir: Path,
    candidates: list[dict[str, Any]],
    queue: str = "single",
) -> dict[str, Any]:
    is_combine = queue == "combine"
    validation_root = run_dir / (
        "combine-validation-workspace" if is_combine else "validation-workspace"
    )
    valid_by_id: dict[str, dict[str, Any]] = {}
    rejected_by_id: dict[str, dict[str, Any]] = {}
    reason_counts: Counter[str] = Counter()

    for path in sorted((validation_root / "validated").glob("*.jsonl")):
        for row in read_jsonl(path):
            valid_by_id[row["instance_id"]] = row
    for path in sorted((validation_root / "validated").glob("*.summary.json")):
        summary = json.loads(path.read_text(encoding="utf-8"))
        for row in summary.get("rejected", []):
            rejected_by_id[row["instance_id"]] = row

    for row in rejected_by_id.values():
        reason_counts[str(row.get("reason") or "unknown")] += 1

    status_rows: list[dict[str, Any]] = []
    for candidate in candidates:
        row = dict(candidate)
        instance_id = row["instance_id"]
        if instance_id in valid_by_id:
            row["queue_status"] = "validated"
        elif instance_id in rejected_by_id:
            row["queue_status"] = "rejected"
            row["rejection_reason"] = rejected_by_id[instance_id].get("reason")
        else:
            row["queue_status"] = "pending_validation"
        status_rows.append(row)

    queue_path = run_dir / ("combine-candidates.jsonl" if is_combine else "candidates.jsonl")
    stage_validated = run_dir / (
        "combine-validated.jsonl" if is_combine else "validated-single.jsonl"
    )
    stage_rejected = run_dir / (
        "combine-rejected.jsonl" if is_combine else "rejected-single.jsonl"
    )
    write_jsonl(queue_path, status_rows)
    write_jsonl(stage_validated, list(valid_by_id.values()))
    write_jsonl(stage_rejected, list(rejected_by_id.values()))
    write_json(
        stage_rejected.with_suffix(".summary.json"),
        {
            "rejected_count": len(rejected_by_id),
            "reason_counts": dict(sorted(reason_counts.items())),
        },
    )
    pending_count = len(candidates) - len(valid_by_id) - len(rejected_by_id)
    final_validated: dict[str, dict[str, Any]] = {}
    final_rejected: dict[str, dict[str, Any]] = {}
    for path in (run_dir / "validated-single.jsonl", run_dir / "combine-validated.jsonl"):
        if path.exists():
            for row in read_jsonl(path):
                final_validated[row["instance_id"]] = row
    for path in (run_dir / "rejected-single.jsonl", run_dir / "combine-rejected.jsonl"):
        if path.exists():
            for row in read_jsonl(path):
                final_rejected[row["instance_id"]] = row
    write_jsonl(run_dir / "validated.jsonl", list(final_validated.values()))
    write_jsonl(run_dir / "rejected.jsonl", list(final_rejected.values()))
    all_reasons = Counter(str(row.get("reason") or "unknown") for row in final_rejected.values())
    write_json(
        run_dir / "rejected.summary.json",
        {
            "rejected_count": len(final_rejected),
            "reason_counts": dict(sorted(all_reasons.items())),
        },
    )
    rebuild_validation_report_index(run_dir)
    validation_manifest = validation_root / "manifest.json"
    validation_finished = False
    if validation_manifest.is_file():
        validation_finished = (
            json.loads(validation_manifest.read_text(encoding="utf-8")).get("status")
            == "completed"
        )
    return {
        "queue": queue,
        "validated_count": len(valid_by_id),
        "rejected_count": len(rejected_by_id),
        "pending_count": pending_count,
        "complete": validation_finished and pending_count == 0,
    }


def update_root_manifest(
    manifest: dict[str, Any],
    run_dir: Path,
    candidate_path: Path,
    aggregate: dict[str, Any],
    queue: str,
) -> None:
    is_combine = queue == "combine"
    progress_key = "combine_validation_progress" if is_combine else "validation_progress"
    manifest[progress_key] = aggregate
    total_validated = len(read_jsonl(run_dir / "validated.jsonl"))
    manifest["validated_count"] = total_validated
    target = manifest.get("target_validated")
    shortfall = max(target - total_validated, 0) if target else 0
    manifest["target_reached"] = target is None or total_validated >= target
    manifest["validated_shortfall"] = shortfall
    if aggregate["complete"]:
        manifest["status"] = (
            "completed_validation"
            if manifest["target_reached"]
            else "completed_validation_with_shortfall"
        )
    else:
        manifest["status"] = (
            "combine_validation_in_progress" if is_combine else "validation_in_progress"
        )
    artifacts = manifest.setdefault("artifacts", {})
    artifacts["combine_candidates" if is_combine else "candidates"] = str(candidate_path)
    artifacts["validated"] = str(run_dir / "validated.jsonl")
    artifacts["rejected"] = str(run_dir / "rejected.jsonl")


def main() -> None:
    args = parse_args()
    run_dir = args.run_dir.resolve()
    is_combine = args.queue == "combine"
    candidate_path = run_dir / (
        "combine-candidates.jsonl" if is_combine else "candidates.jsonl"
    )
    manifest_path = run_dir / "manifest.json"
    config_path = run_dir / "config.snapshot.yaml"
    for path in (candidate_path, manifest_path, config_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    candidates = read_jsonl(candidate_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    paths = config.get("paths", {})
    python = Path(paths.get("swesmith_python", "/data/venvs/swesmith/bin/python"))
    swesmith_repo = Path(paths.get("swesmith_repo", "/data/repos/SWE-smith"))
    workers = args.workers or int(config.get("validation_workers", 1))
    if workers < 1:
        raise ValueError("workers must be a positive integer")

    repositories = manifest.get("repositories", {})
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        grouped[candidate["repo"]].append(candidate)
    unknown = sorted(set(grouped) - set(repositories))
    if unknown:
        raise ValueError(f"Candidates reference unknown repositories: {unknown}")

    print(f"Run directory: {run_dir}")
    print(f"Queue: {args.queue}")
    print(f"Candidates: {len(candidates)}")
    print(f"Workers: {workers}")
    for repo, rows in grouped.items():
        print(f"- {repo}: {len(rows)}")
    if args.dry_run:
        print("Dry run completed; Docker validation was not executed.")
        return

    validation_dir = run_dir / (
        "combine-validation-workspace" if is_combine else "validation-workspace"
    )
    validation_manifest_path = validation_dir / "manifest.json"
    if validation_manifest_path.exists():
        validation_manifest = json.loads(
            validation_manifest_path.read_text(encoding="utf-8")
        )
        if validation_manifest.get("candidate_count") != len(candidates):
            raise RuntimeError(
                "Existing validation manifest does not match the candidate queue"
            )
        if validation_manifest.get("status") == "completed":
            print("Validation already completed; rebuilding aggregate files only.")
            validation_manifest.pop("error", None)
            write_json(validation_manifest_path, validation_manifest)
            aggregate = aggregate_results(run_dir, candidates, args.queue)
            update_root_manifest(
                manifest, run_dir, candidate_path, aggregate, args.queue
            )
            write_json(manifest_path, manifest)
            return
    else:
        validation_manifest = {
            "candidate_count": len(candidates),
            "status": "pending",
            "repositories": {},
        }

    env = os.environ.copy()
    prior_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(swesmith_repo)
        if not prior_pythonpath
        else f"{swesmith_repo}{os.pathsep}{prior_pythonpath}"
    )
    env["HF_HUB_OFFLINE"] = "1"
    env["HF_DATASETS_OFFLINE"] = "1"
    workspace = validation_dir / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    validation_manifest["status"] = "running"
    validation_manifest["started_at_unix"] = time.time()
    validation_manifest.pop("error", None)
    validation_manifest.pop("finished_at_unix", None)
    write_json(validation_manifest_path, validation_manifest)

    try:
        for repo, rows in grouped.items():
            state = validation_manifest["repositories"].setdefault(repo, {})
            slug = repo.replace("/", "__")
            input_path = validation_dir / "candidates" / f"{slug}.json"
            output_path = validation_dir / "validated" / f"{slug}.jsonl"
            image_name = repositories[repo]["image_name"]
            write_json(input_path, rows)
            if state.get("status") == "completed" and output_path.exists():
                continue
            repo_started = time.monotonic()
            run_command(
                [
                    str(python),
                    "-m",
                    "swesmith.harness.valid",
                    str(input_path),
                    "--workers",
                    str(workers),
                ],
                cwd=workspace,
                env=env,
            )
            run_command(
                [
                    str(python),
                    str(SCRIPT_DIR / "export-valid-local.py"),
                    str(input_path),
                    str(workspace / "logs" / "run_validation" / repo),
                    str(output_path),
                    "--image-name",
                    image_name,
                ],
                cwd=SCRIPT_DIR.parent,
                env=env,
            )
            summary = json.loads(
                output_path.with_suffix(".summary.json").read_text(encoding="utf-8")
            )
            state.update(
                {
                    "status": "completed",
                    "candidate_count": len(rows),
                    "valid_count": summary["valid"],
                    "rejected_count": summary["rejected_count"],
                    "elapsed_seconds": round(time.monotonic() - repo_started, 3),
                }
            )
            write_json(validation_manifest_path, validation_manifest)

        validation_manifest["status"] = "completed"
        validation_manifest.pop("error", None)
        validation_manifest["finished_at_unix"] = time.time()
        validation_manifest["elapsed_seconds"] = round(
            validation_manifest["finished_at_unix"]
            - validation_manifest["started_at_unix"],
            3,
        )
        write_json(validation_manifest_path, validation_manifest)
        aggregate = aggregate_results(run_dir, candidates, args.queue)
        update_root_manifest(
            manifest, run_dir, candidate_path, aggregate, args.queue
        )
        write_json(manifest_path, manifest)
        print(json.dumps(aggregate, ensure_ascii=False, indent=2))
    except BaseException as error:
        validation_manifest["status"] = "failed"
        validation_manifest["error"] = f"{type(error).__name__}: {error}"
        validation_manifest["finished_at_unix"] = time.time()
        write_json(validation_manifest_path, validation_manifest)
        raise


if __name__ == "__main__":
    main()
