#!/usr/bin/env python3
"""Run the local SWE-smith procedural multi-repository production pipeline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, NamedTuple

import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
LAB_REPO = SCRIPT_DIR.parent
DEFAULT_CONFIG = LAB_REPO / "configs" / "experiments" / "multirepo-procedural.yaml"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class RepoPlan(NamedTuple):
    repo: str
    image_name: str
    source_image_name: str | None
    selected_patches: int
    seed: int
    selection_seed: int
    max_bugs_per_modifier: int
    max_entities: int
    max_candidates: int
    timeout_seconds: int | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Continue the same run and reuse completed stage artifacts.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate configuration and print the plan without running it.",
    )
    return parser.parse_args()


def validate_run_id(run_id: str) -> None:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError(
            "run-id must contain only letters, numbers, dot, underscore, and hyphen"
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def count_records(path: Path) -> int:
    if not path.exists():
        return 0
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return 0
    if text.startswith("["):
        value = json.loads(text)
        if not isinstance(value, list):
            raise ValueError(f"Expected JSON array: {path}")
        return len(value)
    return sum(1 for line in text.splitlines() if line.strip())


def resolve_path(value: str | Path, *, base: Path = LAB_REPO) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def optional_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    return positive_int(value, name)


def load_config(path: Path) -> tuple[dict[str, Any], list[RepoPlan]]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    config = require_mapping(config, "config")
    if config.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")

    generation = require_mapping(config.get("generation", {}), "generation")
    defaults = {
        "selected_patches": positive_int(
            generation.get("selected_patches", 15), "generation.selected_patches"
        ),
        "max_bugs_per_modifier": positive_int(
            generation.get("max_bugs_per_modifier", 20),
            "generation.max_bugs_per_modifier",
        ),
        "max_entities": positive_int(
            generation.get("max_entities", 2000), "generation.max_entities"
        ),
        "max_candidates": positive_int(
            generation.get("max_candidates", 500), "generation.max_candidates"
        ),
        "timeout_seconds": optional_int(
            generation.get("timeout_seconds", 1800), "generation.timeout_seconds"
        ),
        "seed": positive_int(generation.get("seed", 42), "generation.seed"),
        "selection_seed": positive_int(
            generation.get("selection_seed", 42), "generation.selection_seed"
        ),
    }

    raw_repositories = config.get("repositories")
    if not isinstance(raw_repositories, list) or not raw_repositories:
        raise ValueError("repositories must be a non-empty list")

    plans: list[RepoPlan] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_repositories):
        item = require_mapping(raw, f"repositories[{index}]")
        repo = item.get("repo")
        image_name = item.get("image_name")
        if not isinstance(repo, str) or not repo.strip():
            raise ValueError(f"repositories[{index}].repo must be a non-empty string")
        if repo in seen:
            raise ValueError(f"Duplicate repository: {repo}")
        seen.add(repo)
        if not isinstance(image_name, str) or not image_name.strip():
            raise ValueError(
                f"repositories[{index}].image_name must be a non-empty string"
            )
        source = item.get("source_image_name")
        if source is not None and (not isinstance(source, str) or not source.strip()):
            raise ValueError(
                f"repositories[{index}].source_image_name must be a string or null"
            )
        plans.append(
            RepoPlan(
                repo=repo,
                image_name=image_name,
                source_image_name=source,
                selected_patches=positive_int(
                    item.get("selected_patches", defaults["selected_patches"]),
                    f"repositories[{index}].selected_patches",
                ),
                seed=positive_int(
                    item.get("seed", defaults["seed"] + index),
                    f"repositories[{index}].seed",
                ),
                selection_seed=positive_int(
                    item.get("selection_seed", defaults["selection_seed"] + index),
                    f"repositories[{index}].selection_seed",
                ),
                max_bugs_per_modifier=positive_int(
                    item.get(
                        "max_bugs_per_modifier", defaults["max_bugs_per_modifier"]
                    ),
                    f"repositories[{index}].max_bugs_per_modifier",
                ),
                max_entities=positive_int(
                    item.get("max_entities", defaults["max_entities"]),
                    f"repositories[{index}].max_entities",
                ),
                max_candidates=positive_int(
                    item.get("max_candidates", defaults["max_candidates"]),
                    f"repositories[{index}].max_candidates",
                ),
                timeout_seconds=optional_int(
                    item.get("timeout_seconds", defaults["timeout_seconds"]),
                    f"repositories[{index}].timeout_seconds",
                ),
            )
        )

    validation_workers = positive_int(
        config.get("validation_workers", 1), "validation_workers"
    )
    target_validated = optional_int(config.get("target_validated"), "target_validated")
    config["validation_workers"] = validation_workers
    config["target_validated"] = target_validated
    return config, plans


def repo_slug(repo: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", repo)


def shell_join(command: list[str]) -> str:
    return shlex.join(command)


def command_succeeds(command: list[str]) -> bool:
    return subprocess.run(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def run_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
) -> None:
    print(f"+ (cd {cwd} && {shell_join(command)})", flush=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", newline="\n") as log:
        log.write(f"\n$ {shell_join(command)}\n")
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
        return_code = process.wait()
    if return_code != 0:
        raise subprocess.CalledProcessError(return_code, command)


def load_named_env(env: dict[str, str], path: Path, names: set[str]) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in names or env.get(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[key] = value


def ensure_image(
    plan: RepoPlan,
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
) -> str:
    inspect = ["docker", "image", "inspect", plan.image_name]
    if command_succeeds(inspect):
        print(f"Image already present: {plan.image_name}", flush=True)
        return "already_present"

    if plan.source_image_name:
        source_inspect = ["docker", "image", "inspect", plan.source_image_name]
        if not command_succeeds(source_inspect):
            run_command(
                ["docker", "pull", plan.source_image_name],
                cwd=cwd,
                env=env,
                log_path=log_path,
            )
        run_command(
            ["docker", "tag", plan.source_image_name, plan.image_name],
            cwd=cwd,
            env=env,
            log_path=log_path,
        )
        return "tagged_from_source"

    run_command(
        ["docker", "pull", plan.image_name],
        cwd=cwd,
        env=env,
        log_path=log_path,
    )
    return "pulled"


def generation_command(python: Path, plan: RepoPlan) -> list[str]:
    command = [
        str(python),
        "-m",
        "swesmith.bug_gen.procedural.generate",
        plan.repo,
        "--max_bugs",
        str(plan.max_bugs_per_modifier),
        "--seed",
        str(plan.seed),
        "--interleave",
        "--max_entities",
        str(plan.max_entities),
        "--max_candidates",
        str(plan.max_candidates),
    ]
    if plan.timeout_seconds is not None:
        command.extend(["--timeout_seconds", str(plan.timeout_seconds)])
    return command


def print_plan(
    config: dict[str, Any], plans: list[RepoPlan], config_path: Path, run_id: str
) -> None:
    paths = require_mapping(config.get("paths", {}), "paths")
    run_root = resolve_path(paths.get("run_root", "/data/results/multirepo-runs"))
    print(f"Config: {config_path.resolve()}")
    print(f"Run directory: {run_root / run_id}")
    print(f"Validation workers: {config['validation_workers']}")
    print(f"Target validated: {config['target_validated'] or 'all configured repos'}")
    print("Disk-space checking: disabled")
    print("Repositories:")
    for plan in plans:
        source = f" <- {plan.source_image_name}" if plan.source_image_name else ""
        print(
            f"- {plan.repo}: select={plan.selected_patches}, seed={plan.seed}, "
            f"image={plan.image_name}{source}"
        )


def main() -> None:
    args = parse_args()
    validate_run_id(args.run_id)
    config_path = args.config.resolve()
    if not config_path.is_file():
        raise FileNotFoundError(config_path)
    config, plans = load_config(config_path)
    print_plan(config, plans, config_path, args.run_id)
    if args.dry_run:
        print("Dry run completed; no production commands were executed.")
        return

    paths = require_mapping(config.get("paths", {}), "paths")
    swesmith_repo = resolve_path(paths.get("swesmith_repo", "/data/repos/SWE-smith"))
    swesmith_python = resolve_path(
        paths.get("swesmith_python", "/data/venvs/swesmith/bin/python")
    )
    issuegen_python = resolve_path(
        paths.get("issuegen_python", "/data/venvs/swesmith-issuegen/bin/python")
    )
    run_root = resolve_path(paths.get("run_root", "/data/results/multirepo-runs"))
    for required in (swesmith_repo, swesmith_python):
        if not required.exists():
            raise FileNotFoundError(required)

    run_dir = run_root / args.run_id
    manifest_path = run_dir / "manifest.json"
    config_digest = sha256_file(config_path)
    if run_dir.exists() and not args.resume:
        raise RuntimeError(
            f"Run directory already exists; choose another --run-id or add --resume: {run_dir}"
        )
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("config_sha256") != config_digest:
            raise RuntimeError("Resume config does not match the original run config")
    else:
        manifest = {
            "schema_version": 1,
            "run_id": args.run_id,
            "status": "running",
            "config_path": str(config_path),
            "config_sha256": config_digest,
            "started_at_unix": time.time(),
            "repositories": {},
            "disk_space_check": False,
        }

    run_dir.mkdir(parents=True, exist_ok=True)
    if not (run_dir / "config.snapshot.yaml").exists():
        shutil.copy2(config_path, run_dir / "config.snapshot.yaml")
    mutation_workspace = run_dir / "mutation-workspace"
    mutation_workspace.mkdir(parents=True, exist_ok=True)
    (run_dir / "candidates").mkdir(exist_ok=True)
    (run_dir / "validated").mkdir(exist_ok=True)
    manifest["status"] = "running"
    write_json(manifest_path, manifest)

    env = os.environ.copy()
    load_named_env(env, LAB_REPO / ".env", {"DASHSCOPE_API_KEY"})
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(swesmith_repo)
        if not existing_pythonpath
        else f"{swesmith_repo}{os.pathsep}{existing_pythonpath}"
    )
    env["HF_HUB_OFFLINE"] = "1"
    env["HF_DATASETS_OFFLINE"] = "1"

    validated_outputs: list[Path] = []
    validated_total = 0
    target_validated = config["target_validated"]
    try:
        for plan in plans:
            slug = repo_slug(plan.repo)
            repo_state = manifest["repositories"].setdefault(
                plan.repo,
                {
                    "status": "pending",
                    "image_name": plan.image_name,
                    "selected_patches": plan.selected_patches,
                    "stages": {},
                },
            )
            validated_output = run_dir / "validated" / f"{slug}.jsonl"
            if (
                target_validated is not None
                and validated_total >= target_validated
                and not validated_output.exists()
            ):
                repo_state["status"] = "skipped_target_reached"
                write_json(manifest_path, manifest)
                continue

            repo_state["status"] = "running"
            write_json(manifest_path, manifest)
            repo_log_dir = run_dir / "logs" / slug

            image_is_present = command_succeeds(
                ["docker", "image", "inspect", plan.image_name]
            )
            if (
                repo_state["stages"].get("image") != "completed"
                or not image_is_present
            ):
                repo_state["image_action"] = ensure_image(
                    plan,
                    cwd=mutation_workspace,
                    env=env,
                    log_path=repo_log_dir / "image.log",
                )
                repo_state["stages"]["image"] = "completed"
                write_json(manifest_path, manifest)

            bug_dir = mutation_workspace / "logs" / "bug_gen" / plan.repo
            if repo_state["stages"].get("generate") != "completed":
                run_command(
                    generation_command(swesmith_python, plan),
                    cwd=mutation_workspace,
                    env=env,
                    log_path=repo_log_dir / "generate.log",
                )
                if not bug_dir.is_dir() or not any(bug_dir.rglob("*.diff")):
                    raise RuntimeError(f"Generation produced no patches for {plan.repo}")
                repo_state["stages"]["generate"] = "completed"
                write_json(manifest_path, manifest)

            collected = bug_dir.parent / f"{plan.repo}_all_patches.json"
            if repo_state["stages"].get("collect") != "completed" or not collected.exists():
                run_command(
                    [
                        str(swesmith_python),
                        "-m",
                        "swesmith.bug_gen.collect_patches",
                        str(bug_dir),
                    ],
                    cwd=mutation_workspace,
                    env=env,
                    log_path=repo_log_dir / "collect.log",
                )
                if not collected.is_file():
                    raise RuntimeError(f"Patch collection produced no file for {plan.repo}")
                repo_state["stages"]["collect"] = "completed"
                repo_state["collected_count"] = count_records(collected)
                write_json(manifest_path, manifest)

            selected = run_dir / "candidates" / f"{slug}.json"
            if repo_state["stages"].get("select") != "completed" or not selected.exists():
                run_command(
                    [
                        str(swesmith_python),
                        str(SCRIPT_DIR / "select-diverse-candidates.py"),
                        str(collected),
                        str(selected),
                        "--limit",
                        str(plan.selected_patches),
                        "--seed",
                        str(plan.selection_seed),
                    ],
                    cwd=LAB_REPO,
                    env=env,
                    log_path=repo_log_dir / "select.log",
                )
                repo_state["stages"]["select"] = "completed"
                repo_state["candidate_count"] = count_records(selected)
                write_json(manifest_path, manifest)

            validation_dir = mutation_workspace / "logs" / "run_validation" / plan.repo
            if repo_state["stages"].get("validate") != "completed":
                run_command(
                    [
                        str(swesmith_python),
                        "-m",
                        "swesmith.harness.valid",
                        str(selected),
                        "--workers",
                        str(config["validation_workers"]),
                    ],
                    cwd=mutation_workspace,
                    env=env,
                    log_path=repo_log_dir / "validate.log",
                )
                repo_state["stages"]["validate"] = "completed"
                write_json(manifest_path, manifest)

            if repo_state["stages"].get("export") != "completed" or not validated_output.exists():
                run_command(
                    [
                        str(swesmith_python),
                        str(SCRIPT_DIR / "export-valid-local.py"),
                        str(selected),
                        str(validation_dir),
                        str(validated_output),
                        "--image-name",
                        plan.image_name,
                    ],
                    cwd=LAB_REPO,
                    env=env,
                    log_path=repo_log_dir / "export.log",
                )
                repo_state["stages"]["export"] = "completed"
                write_json(manifest_path, manifest)

            valid_count = count_records(validated_output)
            repo_state["valid_count"] = valid_count
            repo_state["status"] = "completed"
            validated_outputs.append(validated_output)
            validated_total += valid_count
            manifest["validated_count"] = validated_total
            write_json(manifest_path, manifest)

        if not validated_outputs:
            raise RuntimeError("No repository produced a validated output")

        combined = run_dir / "validated.jsonl"
        combine_log = run_dir / "logs" / "combine.log"
        run_command(
            [
                str(swesmith_python),
                str(SCRIPT_DIR / "combine-task-jsonl.py"),
                *[str(path) for path in validated_outputs],
                "--output",
                str(combined),
            ],
            cwd=LAB_REPO,
            env=env,
            log_path=combine_log,
        )
        validated_total = count_records(combined)
        manifest["validated_count"] = validated_total
        manifest["artifacts"] = {"validated": str(combined)}
        write_json(manifest_path, manifest)

        issue = require_mapping(config.get("issue_generation", {}), "issue_generation")
        if issue.get("enabled", True):
            if not issuegen_python.exists():
                raise FileNotFoundError(issuegen_python)
            issue_config = resolve_path(
                issue.get("config", "configs/issue_gen/ig_v2_qwen.yaml")
            )
            demo_pool = resolve_path(
                issue.get(
                    "demo_pool",
                    "/data/datasets/swe-smith-lab/demo-problem-statements.json",
                )
            )
            production = [
                str(swesmith_python),
                str(SCRIPT_DIR / "run-task-production.py"),
                str(combined),
                "--run-id",
                "issuegen",
                "--run-root",
                str(run_dir),
                "--config",
                str(issue_config),
                "--demo-pool",
                str(demo_pool),
                "--demo-seed",
                str(positive_int(issue.get("demo_seed", 42), "issue_generation.demo_seed")),
                "--workers",
                str(positive_int(issue.get("workers", 4), "issue_generation.workers")),
                "--audit-policy",
                str(issue.get("audit_policy", "balanced")),
                "--validation-dir",
                str(mutation_workspace / "logs" / "run_validation"),
                "--issuegen-repo",
                str(swesmith_repo),
                "--issuegen-python",
                str(issuegen_python),
                "--audit-python",
                str(swesmith_python),
            ]
            if args.resume or (run_dir / "issuegen").exists():
                production.append("--resume")
            if issue.get("review_ambiguous", False):
                production.append("--review-ambiguous")
            if issue.get("check_reproduction", False):
                production.append("--check-reproduction")
            run_command(
                production,
                cwd=LAB_REPO,
                env=env,
                log_path=run_dir / "logs" / "issuegen.log",
            )
            issue_manifest_path = run_dir / "issuegen" / "manifest.json"
            issue_manifest = json.loads(issue_manifest_path.read_text(encoding="utf-8"))
            manifest["issue_generation"] = {
                "status": issue_manifest.get("status"),
                "raw_count": issue_manifest.get("raw_count"),
                "audit_counts": issue_manifest.get("audit_counts", {}),
                "semantic_review_calls": issue_manifest.get(
                    "semantic_review_calls", 0
                ),
            }
            manifest["artifacts"].update(
                {
                    "accepted": str(run_dir / "issuegen" / "accepted.jsonl"),
                    "quarantine": str(run_dir / "issuegen" / "quarantine.jsonl"),
                    "audit": str(run_dir / "issuegen" / "audit.jsonl"),
                }
            )

        manifest["status"] = "completed"
        manifest["finished_at_unix"] = time.time()
        write_json(manifest_path, manifest)
    except Exception as error:
        manifest["status"] = "failed"
        manifest["finished_at_unix"] = time.time()
        manifest["error"] = f"{type(error).__name__}: {error}"
        write_json(manifest_path, manifest)
        raise

    print(f"Completed multi-repository run: {manifest_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted; rerun with the same --run-id and --resume.", file=sys.stderr)
        raise SystemExit(130)
