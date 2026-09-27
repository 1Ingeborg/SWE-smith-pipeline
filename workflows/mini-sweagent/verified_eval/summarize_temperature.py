"""Count model patches and errors for the fixed five-case temperature pilot."""

import json
import sys
from pathlib import Path


root = Path(sys.argv[1])
ids = (root / "verified_5_ids.txt").read_text(encoding="utf-8").splitlines()

for temperature in ("0.0", "0.3", "0.7"):
    preds_path = root / f"temp-{temperature}" / "preds.json"
    preds = json.loads(preds_path.read_text(encoding="utf-8")) if preds_path.exists() else {}
    empty = []
    nonempty = []
    pending = []
    for instance_id in ids:
        if instance_id not in preds:
            pending.append(instance_id)
        elif not (preds[instance_id].get("model_patch") or "").strip():
            empty.append(instance_id)
        else:
            nonempty.append(instance_id)
    print(
        f"temp={temperature}: completed={len(empty) + len(nonempty)}/5 "
        f"empty={len(empty)} nonempty={len(nonempty)} pending={len(pending)}"
    )
    if empty:
        print("  empty:", ", ".join(empty))
    if pending:
        print("  pending:", ", ".join(pending))
