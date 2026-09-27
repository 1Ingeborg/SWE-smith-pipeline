#!/usr/bin/env python3
"""Maintenance tool: merge an isolated validation retry into its source run."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import Counter
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    )
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_run", type=Path)
    parser.add_argument("retry_run", type=Path)
    args = parser.parse_args()

    retry_manifest = json.loads((args.retry_run / "manifest.json").read_text())
    retry_progress = retry_manifest.get("validation_progress", {})
    if not retry_progress.get("complete"):
        raise RuntimeError("Retry validation is not complete")
    expected_source = Path(retry_manifest["retry_source_run"]).resolve()
    if expected_source != args.source_run.resolve():
        raise RuntimeError(
            f"Retry belongs to {expected_source}, not {args.source_run.resolve()}"
        )

    retry_candidates = read_jsonl(args.retry_run / "candidates.jsonl")
    retry_ids = {row["instance_id"] for row in retry_candidates}
    retry_valid = {
        row["instance_id"]: row for row in read_jsonl(args.retry_run / "validated.jsonl")
    }
    retry_rejected = {
        row["instance_id"]: row for row in read_jsonl(args.retry_run / "rejected.jsonl")
    }
    accounted = set(retry_valid) | set(retry_rejected)
    if accounted != retry_ids:
        raise RuntimeError(
            f"Retry result mismatch: expected {len(retry_ids)}, got {len(accounted)}"
        )

    backup = args.source_run / "retry-merge-backups" / args.retry_run.name
    if backup.exists():
        raise FileExistsError(backup)
    backup.mkdir(parents=True)
    for name in (
        "manifest.json",
        "candidates.jsonl",
        "validated.jsonl",
        "validated-single.jsonl",
        "rejected.jsonl",
        "rejected-single.jsonl",
        "rejected.summary.json",
        "rejected-single.summary.json",
    ):
        source = args.source_run / name
        if source.is_file():
            shutil.copy2(source, backup / name)

    old_valid = {
        row["instance_id"]: row for row in read_jsonl(args.source_run / "validated.jsonl")
    }
    old_rejected = {
        row["instance_id"]: row for row in read_jsonl(args.source_run / "rejected.jsonl")
    }
    for instance_id in retry_ids:
        old_valid.pop(instance_id, None)
        old_rejected.pop(instance_id, None)
    old_valid.update(retry_valid)
    old_rejected.update(retry_rejected)

    candidates = read_jsonl(args.source_run / "candidates.jsonl")
    candidate_by_id = {row["instance_id"]: row for row in candidates}
    ordered_valid = [
        old_valid[row["instance_id"]]
        for row in candidates
        if row["instance_id"] in old_valid
    ]
    ordered_rejected = [
        old_rejected[row["instance_id"]]
        for row in candidates
        if row["instance_id"] in old_rejected
    ]
    for instance_id, row in candidate_by_id.items():
        if instance_id in old_valid:
            row["queue_status"] = "validated"
            row.pop("rejection_reason", None)
        elif instance_id in old_rejected:
            row["queue_status"] = "rejected"
            row["rejection_reason"] = old_rejected[instance_id].get("reason")
        else:
            row["queue_status"] = "pending_validation"
            row.pop("rejection_reason", None)

    write_jsonl(args.source_run / "candidates.jsonl", candidates)
    write_jsonl(args.source_run / "validated-single.jsonl", ordered_valid)
    write_jsonl(args.source_run / "validated.jsonl", ordered_valid)
    write_jsonl(args.source_run / "rejected-single.jsonl", ordered_rejected)
    write_jsonl(args.source_run / "rejected.jsonl", ordered_rejected)
    reasons = Counter(str(row.get("reason") or "unknown") for row in ordered_rejected)
    rejection_summary = {
        "rejected_count": len(ordered_rejected),
        "reason_counts": dict(sorted(reasons.items())),
    }
    write_json(args.source_run / "rejected-single.summary.json", rejection_summary)
    write_json(args.source_run / "rejected.summary.json", rejection_summary)

    manifest_path = args.source_run / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    target = manifest.get("target_validated")
    pending = len(candidates) - len(ordered_valid) - len(ordered_rejected)
    manifest["validated_count"] = len(ordered_valid)
    manifest["target_reached"] = target is None or len(ordered_valid) >= target
    manifest["validated_shortfall"] = (
        max(target - len(ordered_valid), 0) if target is not None else 0
    )
    manifest["validation_progress"] = {
        "queue": "single",
        "validated_count": len(ordered_valid),
        "rejected_count": len(ordered_rejected),
        "pending_count": pending,
        "complete": pending == 0,
    }
    manifest["status"] = (
        "completed_validation"
        if manifest["target_reached"]
        else "completed_validation_with_shortfall"
    )
    manifest.setdefault("validation_retry_merges", []).append(
        {
            "retry_run": str(args.retry_run.resolve()),
            "candidate_count": len(retry_ids),
            "validated_count": len(retry_valid),
            "rejected_count": len(retry_rejected),
            "backup": str(backup.resolve()),
        }
    )
    write_json(manifest_path, manifest)
    print(
        f"Merged {len(retry_ids)} retry results: "
        f"valid={len(retry_valid)} rejected={len(retry_rejected)}"
    )
    print(
        f"Source totals: valid={len(ordered_valid)} "
        f"rejected={len(ordered_rejected)} pending={pending}"
    )
    print(f"Backup: {backup}")


if __name__ == "__main__":
    main()
