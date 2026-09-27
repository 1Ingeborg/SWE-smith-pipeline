"""Report completed and empty-patch counts for the paired five-case pilot."""

import json
import sys
from collections import Counter
from pathlib import Path


root = Path(sys.argv[1])
ids = (root / "verified_5_ids.txt").read_text(encoding="utf-8").splitlines()
for model in ("lora", "base"):
    for temp in ("0.0", "0.3", "0.7"):
        path = root / model / f"temp-{temp}" / "preds.json"
        preds = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        completed = [instance_id for instance_id in ids if instance_id in preds]
        empty = [
            instance_id
            for instance_id in completed
            if not (preds[instance_id].get("model_patch") or "").strip()
        ]
        statuses = {}
        for instance_id in completed:
            traj = root / model / f"temp-{temp}" / instance_id / f"{instance_id}.traj.json"
            if traj.exists():
                statuses[instance_id] = json.loads(traj.read_text(encoding="utf-8")).get(
                    "info", {}
                ).get("exit_status", "unknown")
            else:
                statuses[instance_id] = "no_trajectory"
        print(
            f"{model:4} temp={temp}: completed={len(completed)}/5, "
            f"empty={len(empty)}, nonempty={len(completed) - len(empty)}, "
            f"pending={5 - len(completed)}"
        )
        if completed:
            print("  exit statuses:", dict(Counter(statuses.values())))
        if empty:
            print("  empty IDs/status:", ", ".join(f"{i}={statuses[i]}" for i in empty))
