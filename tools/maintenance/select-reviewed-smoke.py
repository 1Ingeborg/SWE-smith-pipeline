#!/usr/bin/env python3
"""Maintenance tool: select locally runnable validated tasks for issuegen smoke tests."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    if args.limit < 1:
        raise ValueError("--limit must be positive")
    if args.output.exists():
        raise FileExistsError(args.output)
    rows = [json.loads(line) for line in args.input.read_text(encoding="utf-8").splitlines() if line.strip()]
    selected = []
    image_cache: dict[str, bool] = {}
    for row in sorted(rows, key=lambda value: value["instance_id"]):
        if not any(".py" in str(test) for test in row.get("FAIL_TO_PASS", [])):
            continue
        image = row.get("image_name")
        if not image or not row.get("patch"):
            continue
        if image not in image_cache:
            image_cache[image] = subprocess.run(
                ["docker", "image", "inspect", image],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode == 0
        if image_cache[image]:
            selected.append(row)
        if len(selected) == args.limit:
            break
    if len(selected) != args.limit:
        raise RuntimeError(f"Only {len(selected)} eligible tasks found; needed {args.limit}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected),
        encoding="utf-8",
    )
    for row in selected:
        print(row["instance_id"])


if __name__ == "__main__":
    main()
