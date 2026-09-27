#!/usr/bin/env python3
"""Pipeline helper: select a deterministic, strategy-diverse candidate subset."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def normalized_patch(candidate: dict[str, Any]) -> str:
    patch = str(candidate.get("patch") or "").replace("\r\n", "\n")
    return "\n".join(line.rstrip() for line in patch.splitlines()).strip()


def candidate_fingerprint(candidate: dict[str, Any]) -> str:
    """Identify equivalent source mutations independently of instance IDs."""
    patch = normalized_patch(candidate)
    if not patch:
        payload = "missing-patch\0" + str(candidate.get("instance_id") or "")
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    payload = "\0".join(
        (
            str(candidate.get("repo") or ""),
            str(candidate.get("base_commit") or ""),
            patch,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def deduplicate_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_patches: set[str] = set()
    for candidate in candidates:
        instance_id = str(candidate.get("instance_id") or "")
        fingerprint = candidate_fingerprint(candidate)
        if instance_id in seen_ids or fingerprint in seen_patches:
            continue
        seen_ids.add(instance_id)
        seen_patches.add(fingerprint)
        row = dict(candidate)
        row["candidate_sha256"] = fingerprint
        unique.append(row)
    return unique


def strategy_name(candidate: dict[str, Any]) -> str:
    explicit = candidate.get("strategy")
    if explicit:
        return str(explicit)
    suffix = str(candidate.get("instance_id") or "").rsplit(".", 1)[-1]
    derived = suffix.split("__", 1)[0]
    return derived or "unknown"


def select_diverse(
    candidates: list[dict[str, Any]], limit: int, seed: int
) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    candidates = deduplicate_candidates(candidates)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        groups[strategy_name(candidate)].append(candidate)
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
    unique = deduplicate_candidates(candidates)
    selected = select_diverse(unique, args.limit, args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(selected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = {
        "input": str(args.input),
        "input_count": len(candidates),
        "unique_count": len(unique),
        "duplicate_count": len(candidates) - len(unique),
        "selected_count": len(selected),
        "seed": args.seed,
        "strategy_counts": dict(Counter(strategy_name(row) for row in selected)),
    }
    args.output.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
