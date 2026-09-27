#!/usr/bin/env python3
"""Start one disjoint mini shard while the old worker finishes its current case."""

import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit("Usage: plan_early_mini_gpu0.py IDS PREDS CURRENT_ID OUT_DIR")
    ids_path, preds_path, current_id, out_path = (
        Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
    )
    ids = ids_path.read_text().splitlines()
    preds = json.loads(preds_path.read_text())
    if len(ids) != 50 or len(set(ids)) != 50 or not set(preds).issubset(ids):
        raise SystemExit("Frozen IDs or current predictions are invalid")
    if current_id not in ids:
        raise SystemExit("Current instance ID is not in the frozen list")

    candidates = [iid for iid in ids if iid not in preds and iid != current_id]
    gpu0_ids = candidates[::2]
    if not gpu0_ids or len(candidates) - len(gpu0_ids) < 1:
        raise SystemExit("Not enough disjoint instances to split")
    out_path.mkdir(parents=True, exist_ok=False)
    (out_path / "gpu0_ids.txt").write_text("\n".join(gpu0_ids) + "\n")
    (out_path / "early_plan.json").write_text(
        json.dumps(
            {"original_completed_at_plan": len(preds), "current_original_id": current_id,
             "gpu0": gpu0_ids},
            ensure_ascii=False, indent=2,
        ) + "\n"
    )
    print(f"Original completed at plan: {len(preds)}/50; "
          f"GPU 0 starts {len(gpu0_ids)} disjoint IDs; current original ID excluded")


if __name__ == "__main__":
    main()
