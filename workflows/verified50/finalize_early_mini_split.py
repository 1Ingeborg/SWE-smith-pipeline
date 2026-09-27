#!/usr/bin/env python3
"""Select the GPU 1 shard after the old worker finishes its current case."""

import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("Usage: finalize_early_mini_split.py IDS ORIGINAL_PREDS PLAN_DIR")
    ids_path, preds_path, plan_path = map(Path, sys.argv[1:])
    ids = ids_path.read_text().splitlines()
    original = json.loads(preds_path.read_text())
    gpu0_ids = (plan_path / "gpu0_ids.txt").read_text().splitlines()
    if len(ids) != 50 or len(set(ids)) != 50:
        raise SystemExit("Expected exactly 50 unique frozen IDs")
    if not set(original).issubset(ids) or not set(gpu0_ids).issubset(ids):
        raise SystemExit("Unexpected ID in original predictions or GPU 0 plan")
    if set(original) & set(gpu0_ids):
        raise SystemExit("Original worker duplicated a planned GPU 0 ID")
    gpu1_ids = [iid for iid in ids if iid not in original and iid not in gpu0_ids]
    if not gpu1_ids:
        raise SystemExit("No IDs remain for GPU 1")
    target = plan_path / "gpu1_ids.txt"
    if target.exists():
        raise SystemExit(f"Refusing to overwrite {target}")
    target.write_text("\n".join(gpu1_ids) + "\n")
    (plan_path / "final_plan.json").write_text(
        json.dumps(
            {"original_completed": len(original), "gpu0_count": len(gpu0_ids),
             "gpu1": gpu1_ids},
            ensure_ascii=False, indent=2,
        ) + "\n"
    )
    print(f"Original completed: {len(original)}/50; GPU 0: {len(gpu0_ids)}; "
          f"GPU 1: {len(gpu1_ids)}; total={len(original)+len(gpu0_ids)+len(gpu1_ids)}")


if __name__ == "__main__":
    main()
