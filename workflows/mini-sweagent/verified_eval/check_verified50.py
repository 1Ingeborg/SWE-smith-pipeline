"""Validate the frozen 50 IDs against both local SWE-bench data sources."""

import json
from pathlib import Path


ids_path = Path("/data/configs/mini-sweagent/verified_eval/verified_50_ids.txt")
ids = [line.strip() for line in ids_path.read_text(encoding="utf-8").splitlines()]
assert len(ids) == len(set(ids)) == 50, "Manifest must contain 50 unique IDs"

local_path = Path("/data/datasets/swebench-verified-local/test.jsonl")
local = {
    row["instance_id"]
    for line in local_path.read_text(encoding="utf-8").splitlines()
    if (row := json.loads(line))
}
assert len(local) == 500, f"Local SWE-agent dataset has {len(local)} instances"
assert set(ids) <= local, "SWE-agent dataset is missing selected IDs"

from datasets import load_dataset

mini = {row["instance_id"] for row in load_dataset("princeton-nlp/SWE-bench_Verified", split="test")}
assert len(mini) == 500, f"Mini-agent dataset has {len(mini)} instances"
assert set(ids) <= mini, "Mini-agent dataset is missing selected IDs"
assert mini == local, "The two Verified datasets do not contain the same 500 IDs"
print("PASS: both datasets contain the same 500 Verified IDs and all fixed 50 IDs")
