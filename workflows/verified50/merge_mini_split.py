#!/usr/bin/env python3
"""Merge disjoint mini predictions without modifying any source run."""

import json
import os
import sys
import tempfile
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 6:
        raise SystemExit("Usage: merge_mini_split.py IDS_FILE ORIGINAL GPU0 GPU1 OUTPUT")
    ids_path, *prediction_paths, output_path = map(Path, sys.argv[1:])
    ids = set(ids_path.read_text().splitlines())
    if len(ids) != 50:
        raise SystemExit("Expected exactly 50 frozen IDs")

    combined = {}
    for path in prediction_paths:
        preds = json.loads(path.read_text())
        if not isinstance(preds, dict):
            raise SystemExit(f"Expected a JSON object in {path}")
        overlap = set(preds) & set(combined)
        if overlap:
            raise SystemExit(f"Overlapping predictions in {path}: {sorted(overlap)}")
        extra = set(preds) - ids
        if extra:
            raise SystemExit(f"Unexpected IDs in {path}: {sorted(extra)}")
        for iid, entry in preds.items():
            if not isinstance(entry, dict) or entry.get("instance_id") != iid:
                raise SystemExit(f"Malformed prediction for {iid} in {path}")
            if "model_patch" not in entry or not isinstance(entry["model_patch"], (str, type(None))):
                raise SystemExit(f"Malformed patch for {iid} in {path}")
        combined.update(preds)

    if set(combined) != ids:
        raise SystemExit(f"Merged prediction set incomplete: {len(combined)}/50; "
                         f"missing={sorted(ids - set(combined))}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=output_path.parent, prefix=".merge-", delete=False) as temp:
        json.dump(combined, temp, ensure_ascii=False, indent=2)
        temp.write("\n")
        temp_path = Path(temp.name)
    os.replace(temp_path, output_path)
    nonempty = sum(bool((entry["model_patch"] or "").strip()) for entry in combined.values())
    print(f"Merged 50 predictions: nonempty={nonempty}, empty={50-nonempty}; {output_path}")


if __name__ == "__main__":
    main()
