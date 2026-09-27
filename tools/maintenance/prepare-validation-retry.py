#!/usr/bin/env python3
"""Maintenance tool: create an isolated validation retry run."""

from __future__ import annotations

import argparse
import json
import shutil
from collections import defaultdict
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_run", type=Path)
    parser.add_argument("retry_run", type=Path)
    parser.add_argument("--repo", action="append", default=[])
    parser.add_argument("--instance-id", action="append", default=[])
    parser.add_argument("--reason", action="append", default=[])
    parser.add_argument("--limit-per-repo", type=int)
    args = parser.parse_args()

    if args.retry_run.exists():
        raise FileExistsError(args.retry_run)
    if args.limit_per_repo is not None and args.limit_per_repo < 1:
        raise ValueError("--limit-per-repo must be positive")

    candidates = read_jsonl(args.source_run / "candidates.jsonl")
    rejected = {
        row["instance_id"]: row
        for row in read_jsonl(args.source_run / "rejected.jsonl")
    }
    repositories = set(args.repo)
    instance_ids = set(args.instance_id)
    reasons = set(args.reason)

    selected: list[dict] = []
    per_repo: dict[str, int] = defaultdict(int)
    for candidate in candidates:
        instance_id = candidate["instance_id"]
        matches = (
            candidate["repo"] in repositories
            or instance_id in instance_ids
            or rejected.get(instance_id, {}).get("reason") in reasons
        )
        if not matches:
            continue
        if (
            args.limit_per_repo is not None
            and per_repo[candidate["repo"]] >= args.limit_per_repo
        ):
            continue
        row = dict(candidate)
        row["queue_status"] = "pending_validation"
        row.pop("rejection_reason", None)
        selected.append(row)
        per_repo[row["repo"]] += 1

    if not selected:
        raise RuntimeError("No candidates matched the retry selection")

    source_manifest = json.loads((args.source_run / "manifest.json").read_text())
    selected_repositories = {row["repo"] for row in selected}
    manifest = dict(source_manifest)
    manifest["status"] = "completed_candidates"
    manifest["candidate_count"] = len(selected)
    manifest["target_validated"] = len(selected)
    manifest["repositories"] = {
        name: value
        for name, value in source_manifest["repositories"].items()
        if name in selected_repositories
    }
    for key in (
        "validated_count",
        "target_reached",
        "validated_shortfall",
        "validation_progress",
        "validation_rejections",
        "error",
        "finished_at_unix",
    ):
        manifest.pop(key, None)
    manifest["retry_source_run"] = str(args.source_run.resolve())
    manifest["retry_selection"] = {
        "repositories": sorted(repositories),
        "instance_ids": sorted(instance_ids),
        "reasons": sorted(reasons),
        "limit_per_repo": args.limit_per_repo,
    }

    args.retry_run.mkdir(parents=True)
    shutil.copy2(
        args.source_run / "config.snapshot.yaml",
        args.retry_run / "config.snapshot.yaml",
    )
    write_jsonl(args.retry_run / "candidates.jsonl", selected)
    manifest["artifacts"] = {"candidates": str(args.retry_run / "candidates.jsonl")}
    write_json(args.retry_run / "manifest.json", manifest)
    print(f"Created {args.retry_run} with {len(selected)} candidates")
    for repo, count in sorted(per_repo.items()):
        print(f"- {repo}: {count}")


if __name__ == "__main__":
    main()
