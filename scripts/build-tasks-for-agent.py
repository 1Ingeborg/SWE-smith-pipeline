#!/usr/bin/env python3
"""Join reviewed problem statements back onto validated tasks for Agent rollout.

Reads the validated task records and the generated problem statements, keeps only
the requested review statuses, and writes task rows whose ``problem_statement``
field carries the reviewed candidate text.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

REQUIRED_FIELDS = (
    "instance_id",
    "repo",
    "image_name",
    "patch",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as error:
            raise ValueError(f"{path}:{number}: {error}") from error
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validated", type=Path, required=True)
    parser.add_argument("--problem-statements", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--status",
        nargs="+",
        default=["accepted"],
        help="review_status values to keep (default: accepted)",
    )
    args = parser.parse_args()

    keep = set(args.status)
    statements: dict[str, dict[str, Any]] = {}
    status_counts: Counter[str] = Counter()
    for row in read_jsonl(args.problem_statements):
        status = row.get("review_status")
        status_counts[str(status)] += 1
        if status not in keep:
            continue
        instance_id = row.get("instance_id")
        if not isinstance(instance_id, str) or not instance_id:
            raise ValueError("problem statement row missing instance_id")
        if instance_id in statements:
            raise ValueError(f"Duplicate instance_id in statements: {instance_id}")
        statements[instance_id] = row

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    empty_statement: list[str] = []
    for task in read_jsonl(args.validated):
        instance_id = task.get("instance_id")
        statement = statements.pop(instance_id, None)
        if statement is None:
            continue
        for field in REQUIRED_FIELDS:
            if field not in task:
                raise ValueError(f"{instance_id}: validated record missing {field}")
        text = (statement.get("candidate_problem_statement") or "").strip()
        if not text:
            empty_statement.append(instance_id)
            continue
        if instance_id in seen:
            raise ValueError(f"Duplicate instance_id in validated: {instance_id}")
        seen.add(instance_id)
        row = dict(task)
        row["problem_statement"] = text
        rows.append(row)

    if statements:
        raise ValueError(
            f"{len(statements)} kept statements have no validated record, e.g. "
            f"{sorted(statements)[:3]}"
        )
    if empty_statement:
        raise ValueError(
            f"{len(empty_statement)} kept statements are empty, e.g. "
            f"{empty_statement[:3]}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(args.output)

    summary = {
        "schema_version": 1,
        "validated": str(args.validated),
        "problem_statements": str(args.problem_statements),
        "output": str(args.output),
        "kept_status": sorted(keep),
        "source_status_counts": dict(sorted(status_counts.items())),
        "total": len(rows),
        "repo_counts": dict(sorted(Counter(r.get("repo") for r in rows).items())),
        "strategy_counts": dict(
            sorted(Counter(str(r.get("strategy")) for r in rows).items())
        ),
    }
    args.output.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(rows)} tasks -> {args.output}")
    print(f"repositories: {len(summary['repo_counts'])}")
    print(f"strategies:   {len(summary['strategy_counts'])}")


if __name__ == "__main__":
    main()
