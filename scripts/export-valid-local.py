#!/usr/bin/env python3
"""Export validated SWE-smith patches without pushing branches to GitHub."""

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("patches", type=Path, help="JSON produced by collect_patches")
    parser.add_argument("validation_dir", type=Path, help="Repository validation log directory")
    parser.add_argument("output", type=Path, help="Destination JSONL path")
    parser.add_argument("--image-name", required=True)
    args = parser.parse_args()

    candidates = json.loads(args.patches.read_text(encoding="utf-8"))
    valid = []
    rejected = []

    for candidate in candidates:
        instance_id = candidate["instance_id"]
        report_path = args.validation_dir / instance_id / "report.json"
        if not report_path.exists():
            rejected.append({"instance_id": instance_id, "reason": "missing_report"})
            continue

        report = json.loads(report_path.read_text(encoding="utf-8"))
        fail_to_pass = report.get("FAIL_TO_PASS", [])
        pass_to_pass = report.get("PASS_TO_PASS", [])
        if not fail_to_pass:
            rejected.append({"instance_id": instance_id, "reason": "no_FAIL_TO_PASS"})
            continue
        if not pass_to_pass:
            rejected.append({"instance_id": instance_id, "reason": "no_PASS_TO_PASS"})
            continue

        valid.append(
            {
                "instance_id": instance_id,
                "repo": candidate["repo"],
                "image_name": args.image_name,
                "patch": candidate["patch"],
                "FAIL_TO_PASS": fail_to_pass,
                "PASS_TO_PASS": pass_to_pass,
                "problem_statement": "",
                "strategy": candidate.get("strategy"),
                "rewrite": candidate.get("rewrite"),
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as output_file:
        for row in valid:
            output_file.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary_path = args.output.with_suffix(".summary.json")
    reason_counts = Counter(row["reason"] for row in rejected)
    summary_path.write_text(
        json.dumps(
            {
                "input_candidates": len(candidates),
                "valid": len(valid),
                "rejected_count": len(rejected),
                "rejection_reason_counts": dict(sorted(reason_counts.items())),
                "rejected": rejected,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"valid={len(valid)} rejected={len(rejected)}")
    print(f"dataset={args.output}")
    print(f"summary={summary_path}")


if __name__ == "__main__":
    main()
