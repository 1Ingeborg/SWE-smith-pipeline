#!/usr/bin/env python3
"""Build a reusable repository/image catalog from the hosted SWE-smith dataset."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

def infer_strategy(row: dict[str, Any]) -> str:
    strategy = row.get("strategy")
    if isinstance(strategy, str) and strategy:
        return strategy
    instance_id = str(row.get("instance_id", ""))
    parts = instance_id.split(".")
    if len(parts) < 3:
        return "unknown"
    return parts[-1].split("__", 1)[0] or "unknown"


def build_catalog(rows: Iterable[dict[str, Any]], sample_size: int = 3) -> dict[str, Any]:
    by_repo: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "count": 0,
            "images": Counter(),
            "strategies": Counter(),
            "sample_instance_ids": [],
        }
    )
    total_rows = 0
    for row in rows:
        total_rows += 1
        repo = str(row.get("repo") or "unknown")
        entry = by_repo[repo]
        entry["count"] += 1
        image_name = row.get("image_name")
        if image_name:
            entry["images"][str(image_name)] += 1
        entry["strategies"][infer_strategy(row)] += 1
        instance_id = row.get("instance_id")
        if instance_id and len(entry["sample_instance_ids"]) < sample_size:
            entry["sample_instance_ids"].append(str(instance_id))

    repositories = []
    for repo, entry in by_repo.items():
        images = dict(entry["images"].most_common())
        strategies = dict(entry["strategies"].most_common())
        repositories.append(
            {
                "repo": repo,
                "count": entry["count"],
                "primary_image_name": next(iter(images), None),
                "image_counts": images,
                "strategy_counts": strategies,
                "sample_instance_ids": entry["sample_instance_ids"],
            }
        )
    repositories.sort(key=lambda item: (-item["count"], item["repo"]))
    return {"total_rows": total_rows, "repository_count": len(repositories), "repositories": repositories}


def write_csv(path: Path, repositories: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "rank",
                "repo",
                "count",
                "primary_image_name",
                "strategy_count",
                "top_strategies",
            ),
        )
        writer.writeheader()
        for rank, entry in enumerate(repositories, start=1):
            strategy_counts = entry["strategy_counts"]
            writer.writerow(
                {
                    "rank": rank,
                    "repo": entry["repo"],
                    "count": entry["count"],
                    "primary_image_name": entry["primary_image_name"] or "",
                    "strategy_count": len(strategy_counts),
                    "top_strategies": ";".join(
                        f"{name}:{count}" for name, count in list(strategy_counts.items())[:10]
                    ),
                }
            )


def iter_parquet_rows(directory: Path) -> Iterable[dict[str, Any]]:
    import pyarrow.parquet as parquet

    paths = sorted(directory.glob("*.parquet"))
    if not paths:
        raise FileNotFoundError(f"No parquet files found in {directory}")
    for path in paths:
        table = parquet.read_table(
            path, columns=["instance_id", "repo", "image_name"]
        )
        yield from table.to_pylist()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="SWE-bench/SWE-smith")
    parser.add_argument("--split", default="train")
    parser.add_argument(
        "--parquet-dir",
        type=Path,
        help="Read local Parquet shards instead of contacting Hugging Face",
    )
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=3)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.sample_size < 0:
        raise ValueError("--sample-size must be non-negative")
    if args.parquet_dir:
        rows = iter_parquet_rows(args.parquet_dir)
        source_access = "local_parquet_selected_columns"
    else:
        from datasets import load_dataset

        rows = load_dataset(args.dataset, split=args.split)
        source_access = "huggingface_datasets"
    catalog = build_catalog(rows, sample_size=args.sample_size)
    catalog.update(
        {
            "schema_version": 1,
            "source_dataset": args.dataset,
            "source_split": args.split,
            "source_access": source_access,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "purpose": "Repository and Docker image selection only; rows are not reused as generated tasks.",
        }
    )
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write_csv(args.output_csv, catalog["repositories"])
    print(
        json.dumps(
            {
                "total_rows": catalog["total_rows"],
                "repository_count": catalog["repository_count"],
                "output_json": str(args.output_json),
                "output_csv": str(args.output_csv),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
