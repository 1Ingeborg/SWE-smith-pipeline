#!/usr/bin/env python3
"""Select a deterministic, strategy-diverse subset of SWE-smith candidates."""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def select_diverse(
    candidates: list[dict[str, Any]], limit: int, seed: int
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        groups[str(candidate.get("strategy") or "unknown")].append(candidate)
    rng = random.Random(seed)
    strategy_order = sorted(groups)
    rng.shuffle(strategy_order)
    for values in groups.values():
        rng.shuffle(values)

    selected: list[dict[str, Any]] = []
    while len(selected) < min(limit, len(candidates)):
        added = False
        for strategy in strategy_order:
            if groups[strategy] and len(selected) < limit:
                selected.append(groups[strategy].pop())
                added = True
        if not added:
            break
    return selected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    candidates = json.loads(args.input.read_text(encoding="utf-8"))
    selected = select_diverse(candidates, args.limit, args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(selected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        "input": str(args.input),
        "input_count": len(candidates),
        "selected_count": len(selected),
        "seed": args.seed,
        "strategy_counts": dict(Counter(row.get("strategy") for row in selected)),
    }
    args.output.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
