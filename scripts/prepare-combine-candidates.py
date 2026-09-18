#!/usr/bin/env python3
"""Build combine_file/combine_module candidates from validated single bugs."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
RUNNER_PATH = SCRIPT_DIR / "run-multirepo-pipeline.py"
SPEC = importlib.util.spec_from_file_location("run_multirepo_pipeline", RUNNER_PATH)
RUNNER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(RUNNER)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--strategy",
        action="append",
        choices=("combine_file", "combine_module"),
        dest="strategies",
        help="Repeat to enable both strategies; defaults to both.",
    )
    parser.add_argument("--selected-patches", type=int)
    parser.add_argument("--num-patches", type=int, default=2)
    parser.add_argument("--max-combos", type=int)
    parser.add_argument(
        "--repo",
        action="append",
        dest="repositories",
        help="Restrict preparation to one repository; repeat to select several.",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def single_validated_path(run_dir: Path) -> Path:
    staged = run_dir / "validated-single.jsonl"
    return staged if staged.exists() else run_dir / "validated.jsonl"


def combine_specs(
    config: dict[str, Any],
    names: list[str],
    *,
    num_patches: int = 2,
    max_combos: int | None = None,
) -> dict[str, dict[str, Any]]:
    configured = config.get("generation_strategies", {})
    result: dict[str, dict[str, Any]] = {}
    for name in names:
        spec = dict(configured.get(name, {}))
        spec["enabled"] = True
        spec["num_patches"] = num_patches
        if max_combos is not None:
            spec["max_combos"] = max_combos
        result[name] = spec
    return result


def main() -> None:
    args = parse_args()
    run_dir = args.run_dir.resolve()
    config_path = run_dir / "config.snapshot.yaml"
    manifest_path = run_dir / "manifest.json"
    validated_path = single_validated_path(run_dir)
    for path in (config_path, manifest_path, validated_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    config, plans = RUNNER.load_config(config_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    progress = manifest.get("validation_progress")
    if progress is not None and not progress.get("complete"):
        raise RuntimeError("All single-candidate validation batches must finish first")

    if args.num_patches < 2:
        raise ValueError("num-patches must be at least 2")
    if args.max_combos is not None and args.max_combos < 1:
        raise ValueError("max-combos must be a positive integer")
    names = args.strategies or ["combine_file", "combine_module"]
    combiners = combine_specs(
        config,
        names,
        num_patches=args.num_patches,
        max_combos=args.max_combos,
    )
    selected_limit = args.selected_patches or config["combine_selected_patches"]
    if selected_limit < 1:
        raise ValueError("selected-patches must be a positive integer")

    validated = [
        row
        for row in read_jsonl(validated_path)
        if not str(row.get("strategy") or "").startswith("combine_")
    ]
    by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in validated:
        by_repo[row["repo"]].append(row)
    if args.repositories:
        known = {plan.repo for plan in plans}
        unknown = sorted(set(args.repositories) - known)
        if unknown:
            raise ValueError(f"Unknown repositories: {unknown}")
        plans = [plan for plan in plans if plan.repo in set(args.repositories)]

    print(f"Run directory: {run_dir}")
    print(f"Validated single bugs: {len(validated)}")
    print(f"Strategies: {', '.join(names)}")
    print(f"Selected combine candidates per repository: {selected_limit}")
    for plan in plans:
        print(f"- {plan.repo}: {len(by_repo.get(plan.repo, []))} validated inputs")
    if args.dry_run:
        print("Dry run completed; combine commands were not executed.")
        return

    paths = config.get("paths", {})
    python = Path(paths.get("swesmith_python", "/data/venvs/swesmith/bin/python"))
    swesmith_repo = Path(paths.get("swesmith_repo", "/data/repos/SWE-smith"))
    env = os.environ.copy()
    prior_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(swesmith_repo)
        if not prior_pythonpath
        else f"{swesmith_repo}{os.pathsep}{prior_pythonpath}"
    )
    env["HF_HUB_OFFLINE"] = "1"
    env["HF_DATASETS_OFFLINE"] = "1"

    combine_manifest_path = run_dir / "combine-preparation.json"
    if combine_manifest_path.exists():
        combine_manifest = json.loads(combine_manifest_path.read_text(encoding="utf-8"))
        if combine_manifest.get("status") == "completed":
            print("Combine candidates already prepared; nothing to do.")
            return
    else:
        combine_manifest = {"status": "pending", "repositories": {}}
    combine_manifest.update(
        {
            "status": "running",
            "strategies": names,
            "num_patches": args.num_patches,
            "max_combos": args.max_combos,
            "selected_patches_per_repository": selected_limit,
            "started_at_unix": time.time(),
        }
    )
    combine_manifest.pop("error", None)
    RUNNER.write_json(combine_manifest_path, combine_manifest)

    mutation_workspace = run_dir / "mutation-workspace"
    combine_workspace = run_dir / "combine-workspace-batched"
    gate_dir = combine_workspace / "logs" / "task_insts"
    gate_dir.mkdir(parents=True, exist_ok=True)
    selected_outputs: list[Path] = []

    try:
        for plan in plans:
            rows = by_repo.get(plan.repo, [])
            state = combine_manifest["repositories"].setdefault(plan.repo, {})
            slug = RUNNER.repo_slug(plan.repo)
            selected = run_dir / "combine-candidates" / f"{slug}.json"
            if state.get("status") == "completed" and selected.exists():
                selected_outputs.append(selected)
                continue
            if len(rows) < 2:
                state.update(
                    {
                        "status": "skipped_insufficient_validated",
                        "validated_inputs": len(rows),
                    }
                )
                RUNNER.write_json(combine_manifest_path, combine_manifest)
                continue

            per_repo_validated = run_dir / "combine-input" / f"{slug}.jsonl"
            write_jsonl(per_repo_validated, rows)
            gated = RUNNER.write_validation_gate(
                per_repo_validated, gate_dir / f"{plan.repo}.json"
            )
            bug_dir = mutation_workspace / "logs" / "bug_gen" / plan.repo
            if not bug_dir.is_dir():
                raise FileNotFoundError(
                    f"Original bug-generation artifacts are missing: {bug_dir}"
                )
            checkout = mutation_workspace / plan.repo
            repo_logs = run_dir / "logs" / "combine-preparation" / slug
            if not checkout.is_dir():
                RUNNER.materialize_repo_from_image(
                    plan.image_name,
                    checkout,
                    cwd=mutation_workspace,
                    env=env,
                    log_path=repo_logs / "source.log",
                )

            bug_link = combine_workspace / "logs" / "bug_gen" / plan.repo
            bug_link.parent.mkdir(parents=True, exist_ok=True)
            if not bug_link.exists():
                bug_link.symlink_to(bug_dir, target_is_directory=True)
            relative_bug_dir = f"logs/bug_gen/{plan.repo}"
            for name, spec in combiners.items():
                checkout_link = combine_workspace / plan.repo
                if not checkout_link.exists():
                    checkout_link.symlink_to(checkout, target_is_directory=True)
                RUNNER.run_command(
                    RUNNER.combine_command(python, name, spec, relative_bug_dir),
                    cwd=combine_workspace,
                    env=env,
                    log_path=repo_logs / f"{name}.log",
                )

            collected = bug_dir.parent / f"{plan.repo}_all_patches.json"
            RUNNER.run_command(
                [str(python), "-m", "swesmith.bug_gen.collect_patches", str(bug_dir)],
                cwd=mutation_workspace,
                env=env,
                log_path=repo_logs / "collect.log",
            )
            raw = run_dir / "combine-candidates" / f"{slug}.raw.json"
            kept = RUNNER.filter_patch_records(
                collected, raw, ("combine_file", "combine_module")
            )
            if kept:
                RUNNER.run_command(
                    [
                        str(python),
                        str(SCRIPT_DIR / "select-diverse-candidates.py"),
                        str(raw),
                        str(selected),
                        "--limit",
                        str(selected_limit),
                        "--seed",
                        str(plan.selection_seed),
                    ],
                    cwd=SCRIPT_DIR.parent,
                    env=env,
                    log_path=repo_logs / "select.log",
                )
                selected_outputs.append(selected)
            state.update(
                {
                    "status": "completed",
                    "validated_inputs": gated,
                    "raw_combine_candidates": kept,
                    "selected_combine_candidates": (
                        RUNNER.count_records(selected) if selected.exists() else 0
                    ),
                }
            )
            RUNNER.write_json(combine_manifest_path, combine_manifest)

        queue = run_dir / "combine-candidates.jsonl"
        summary = RUNNER.build_candidate_queue(selected_outputs, queue)
        combine_manifest.update(
            {
                "status": "completed",
                "candidate_count": summary["queued_count"],
                "candidate_summary": summary,
                "finished_at_unix": time.time(),
            }
        )
        combine_manifest["elapsed_seconds"] = round(
            combine_manifest["finished_at_unix"]
            - combine_manifest["started_at_unix"],
            3,
        )
        RUNNER.write_json(combine_manifest_path, combine_manifest)
        manifest["combine_preparation"] = {
            "status": "completed",
            "candidate_count": summary["queued_count"],
            "strategies": names,
            "artifact": str(queue),
        }
        RUNNER.write_json(manifest_path, manifest)
        print(f"Combine candidates: {summary['queued_count']}")
        print(f"Queue: {queue}")
    except BaseException as error:
        combine_manifest["status"] = "failed"
        combine_manifest["error"] = f"{type(error).__name__}: {error}"
        combine_manifest["finished_at_unix"] = time.time()
        RUNNER.write_json(combine_manifest_path, combine_manifest)
        raise


if __name__ == "__main__":
    main()
