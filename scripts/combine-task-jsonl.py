#!/usr/bin/env python3
"""Combine task JSONL files with duplicate protection and a summary."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def load_unique(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in paths:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            row = json.loads(line)
            instance_id = row.get("instance_id")
            if not isinstance(instance_id, str) or not instance_id:
                raise ValueError(f"Missing instance_id in {path}:{line_number}")
            if instance_id in seen:
                raise ValueError(f"Duplicate instance_id: {instance_id}")
            seen.add(instance_id)
            records.append(row)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    records = load_unique(args.inputs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in records:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(args.output)

    summary = {
        "inputs": [str(path) for path in args.inputs],
        "output": str(args.output),
        "total": len(records),
        "repo_counts": dict(Counter(row.get("repo") for row in records)),
        "strategy_counts": dict(Counter(row.get("strategy") for row in records)),
    }
    args.output.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
