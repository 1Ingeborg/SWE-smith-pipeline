#!/usr/bin/env python3
"""Freeze the unfinished Verified IDs after the original mini worker stops."""

import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("Usage: plan_mini_split.py IDS_FILE ORIGINAL_PREDS OUT_DIR")
    ids_path, preds_path, out_path = map(Path, sys.argv[1:])
    ids = ids_path.read_text().splitlines()
    preds = json.loads(preds_path.read_text())
    if len(ids) != 50 or len(set(ids)) != 50:
        raise SystemExit("Expected exactly 50 unique frozen IDs")
    if not isinstance(preds, dict) or not set(preds).issubset(ids):
        raise SystemExit("Original predictions contain unexpected IDs")

    remaining = [iid for iid in ids if iid not in preds]
    if not remaining:
        raise SystemExit("Nothing remains to split")
    groups = {"gpu0": remaining[::2], "gpu1": remaining[1::2]}
    if not all(groups.values()):
        raise SystemExit("At least two unfinished IDs are needed")

    out_path.mkdir(parents=True, exist_ok=False)
    for name, group in groups.items():
        (out_path / f"{name}_ids.txt").write_text("\n".join(group) + "\n")
    (out_path / "plan.json").write_text(
        json.dumps(
            {"completed_before_split": len(preds), "remaining": len(remaining),
             "gpu0": groups["gpu0"], "gpu1": groups["gpu1"]},
            ensure_ascii=False, indent=2,
        ) + "\n"
    )
    print(f"Original completed: {len(preds)}/50; GPU 0: {len(groups['gpu0'])}; "
          f"GPU 1: {len(groups['gpu1'])}")


if __name__ == "__main__":
    main()
