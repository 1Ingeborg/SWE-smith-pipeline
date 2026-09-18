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
    parser.add_argument(
        "--stop-after",
        choices=("candidates", "validation", "issues"),
        default="issues",
        help=(
            "Checkpoint the run after candidate selection, Docker validation, "
            "or issue generation. Resume the same run-id to continue."
        ),
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


def yaml_files_semantically_equal(first: Path, second: Path) -> bool:
    """Return whether two YAML files differ only in comments or formatting."""
    try:
        return yaml.safe_load(first.read_text(encoding="utf-8")) == yaml.safe_load(
            second.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, yaml.YAMLError):
        return False


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


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def normalized_patch_fingerprint(record: dict[str, Any]) -> str:
    patch = str(record.get("patch") or "").replace("\r\n", "\n")
    patch = "\n".join(line.rstrip() for line in patch.splitlines()).strip()
    if not patch:
        payload = "missing-patch\0" + str(record.get("instance_id") or "")
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    payload = "\0".join(
        (
            str(record.get("repo") or ""),
            str(record.get("base_commit") or ""),
            patch,
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_candidate_queue(inputs: list[Path], output: Path) -> dict[str, Any]:
    """Combine selected JSON arrays into a stable, duplicate-free JSONL queue."""
    queued: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_patches: set[str] = set()
    duplicate_ids = 0
    duplicate_patches = 0
    input_count = 0
    for path in inputs:
        records = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError(f"Expected JSON array: {path}")
        for raw in records:
            input_count += 1
            record = require_mapping(raw, f"candidate in {path}")
            instance_id = str(record.get("instance_id") or "")
            if not instance_id:
                raise ValueError(f"Candidate has no instance_id: {path}")
            fingerprint = str(
                record.get("candidate_sha256")
                or normalized_patch_fingerprint(record)
            )
            if instance_id in seen_ids:
                duplicate_ids += 1
                continue
            if fingerprint in seen_patches:
                duplicate_patches += 1
                continue
            seen_ids.add(instance_id)
            seen_patches.add(fingerprint)
            row = dict(record)
            row["candidate_sha256"] = fingerprint
            row["queue_status"] = "pending_validation"
            queued.append(row)
    write_jsonl(output, queued)
    summary = {
        "inputs": [str(path) for path in inputs],
        "input_count": input_count,
        "queued_count": len(queued),
        "duplicate_instance_ids": duplicate_ids,
        "duplicate_patches": duplicate_patches,
    }
    write_json(output.with_suffix(".summary.json"), summary)
    return summary


def build_rejection_report(summary_paths: list[Path], output: Path) -> dict[str, Any]:
    rejected: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = {}
    for path in summary_paths:
        if not path.exists():
            continue
        summary = json.loads(path.read_text(encoding="utf-8"))
        for raw in summary.get("rejected", []):
            record = require_mapping(raw, f"rejection in {path}")
            row = dict(record)
            row["source_summary"] = str(path)
            rejected.append(row)
            reason = str(row.get("reason") or "unknown")
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    write_jsonl(output, rejected)
    result = {
        "rejected_count": len(rejected),
        "reason_counts": dict(sorted(reason_counts.items())),
        "sources": [str(path) for path in summary_paths],
    }
    write_json(output.with_suffix(".summary.json"), result)
    return result


def record_stage_seconds(state: dict[str, Any], stage: str, started: float) -> None:
    timings = state.setdefault("stage_seconds", {})
    timings[stage] = round(float(timings.get(stage, 0.0)) + time.monotonic() - started, 3)


def write_validation_gate(validated_path: Path, gate_path: Path) -> int:
    """Write the ``logs/task_insts/<repo>.json`` gate the combine strategies read.

    They only consult ``instance_id`` and strip the ``<repo>.`` prefix, so the
    lab's own validated output is a drop-in source for that gate. Using it keeps
    the "combine only already-validated patches" guarantee without running
    ``swesmith.harness.gather`` (which would push branches to GitHub).
    """
    rows = [
        {"instance_id": record["instance_id"]}
        for record in read_jsonl(validated_path)
        if record.get("instance_id")
    ]
    write_json(gate_path, rows)
    return len(rows)


def filter_patch_records(
    source: Path, destination: Path, prefixes: tuple[str, ...]
) -> int:
    """Keep collected patches whose instance-id suffix starts with a prefix.

    Keyed on ``instance_id`` rather than ``strategy`` because the upstream
    combine entry points write metadata holding only ``patch_files`` and
    ``num_patch_files`` -- no ``strategy`` key survives into the collected
    record, so filtering on it would drop every combined patch.
    """
    records = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError(f"Expected JSON array: {source}")
    kept = []
    for record in records:
        instance_id = str(record.get("instance_id") or "")
        # Mirrors the upstream convention: repo names themselves contain dots,
        # so the suffix is whatever follows the last one.
        suffix = instance_id.split(".")[-1]
        if suffix.startswith(prefixes):
            kept.append(record)
    write_json(destination, kept)
    return len(kept)


def target_result(actual: int, target: int | None) -> dict[str, Any]:
    shortfall = max(target - actual, 0) if target is not None else 0
    return {
        "target_validated": target,
        "target_reached": target is None or actual >= target,
        "validated_shortfall": shortfall,
    }


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


def whole_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


# Bug-injection strategies, mapped to the pipeline stage each one runs in.
# "generate" strategies produce fresh bugs before collect/validate. The combine
# strategies merge already-validated patches, so they must run after validation.
STRATEGY_STAGES: dict[str, str] = {
    "procedural": "generate",
    "lm_modify": "generate",
    "lm_rewrite": "generate",
    "pr_mirror": "generate",
    "combine_file": "post_validate",
    "combine_module": "post_validate",
}

# Omitting generation.strategies keeps the original single-strategy behaviour.
DEFAULT_STRATEGIES: dict[str, dict[str, Any]] = {"procedural": {"enabled": True}}


def load_strategies(generation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = generation.get("strategies")
    if raw is None:
        return {name: dict(spec) for name, spec in DEFAULT_STRATEGIES.items()}
    raw = require_mapping(raw, "generation.strategies")
    strategies: dict[str, dict[str, Any]] = {}
    for name, value in raw.items():
        if name not in STRATEGY_STAGES:
            raise ValueError(
                f"generation.strategies.{name} is not a known strategy; "
                f"expected one of {sorted(STRATEGY_STAGES)}"
            )
        spec = require_mapping(
            value if value is not None else {}, f"generation.strategies.{name}"
        )
        spec = dict(spec)
        enabled = spec.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(
                f"generation.strategies.{name}.enabled must be a boolean"
            )
        spec["enabled"] = enabled
        strategies[name] = spec
    if not strategies:
        raise ValueError("generation.strategies must list at least one strategy")
    return strategies


def enabled_strategies(
    strategies: dict[str, dict[str, Any]], stage: str
) -> dict[str, dict[str, Any]]:
    return {
        name: spec
        for name, spec in strategies.items()
        if spec.get("enabled") and STRATEGY_STAGES[name] == stage
    }


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
                    item.get("seed", defaults["seed"]),
                    f"repositories[{index}].seed",
                ),
                selection_seed=positive_int(
                    item.get("selection_seed", defaults["selection_seed"]),
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
    config["generation_strategies"] = load_strategies(generation)
    config["combine_selected_patches"] = positive_int(
        generation.get("combine_selected_patches", 15),
        "generation.combine_selected_patches",
    )
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


def materialize_repo_from_image(
    image_name: str,
    destination: Path,
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
) -> str:
    """Copy the image's authoritative /testbed checkout into the run workspace."""
    if destination.exists():
        if (destination / ".git").is_dir():
            print(f"Repository checkout already present: {destination}", flush=True)
            return "already_present"
        raise RuntimeError(
            f"Refusing to replace non-git repository path: {destination}"
        )

    temporary = destination.with_name(
        f".{destination.name}.from-image-{os.getpid()}"
    )
    if temporary.exists():
        raise RuntimeError(f"Temporary checkout path already exists: {temporary}")
    temporary.mkdir(parents=False)

    create = subprocess.run(
        ["docker", "create", image_name],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if create.returncode != 0:
        raise RuntimeError(
            f"Could not create source container for {image_name}: "
            f"{create.stderr.strip()}"
        )
    container_id = create.stdout.strip()
    if not container_id:
        raise RuntimeError(f"docker create returned no container ID for {image_name}")

    try:
        run_command(
            ["docker", "cp", f"{container_id}:/testbed/.", str(temporary)],
            cwd=cwd,
            env=env,
            log_path=log_path,
        )
        if not (temporary / ".git").is_dir():
            raise RuntimeError(f"Image does not contain /testbed/.git: {image_name}")
        if not command_succeeds(
            ["git", "-C", str(temporary), "rev-parse", "--is-inside-work-tree"]
        ):
            raise RuntimeError(f"Invalid git checkout copied from image: {image_name}")
        temporary.rename(destination)
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_id],
            cwd=cwd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if temporary.exists():
            shutil.rmtree(temporary)

    print(f"Repository restored from {image_name}:/testbed", flush=True)
    return "copied_from_image"


def generation_command(
    python: Path, plan: RepoPlan, *, interleave: bool = True
) -> list[str]:
    command = [
        str(python),
        "-m",
        "swesmith.bug_gen.procedural.generate",
        plan.repo,
        "--max_bugs",
        str(plan.max_bugs_per_modifier),
        "--seed",
        str(plan.seed),
    ]
    if interleave:
        command.append("--interleave")
    command.extend(
        [
            "--max_entities",
            str(plan.max_entities),
            "--max_candidates",
            str(plan.max_candidates),
        ]
    )
    if plan.timeout_seconds is not None:
        command.extend(["--timeout_seconds", str(plan.timeout_seconds)])
    return command


def strategy_config_file(spec: dict[str, Any], name: str) -> str:
    value = spec.get("config_file")
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"generation.strategies.{name}.config_file must be a non-empty string"
        )
    return value


def procedural_command(
    python: Path, plan: RepoPlan, spec: dict[str, Any]
) -> list[str]:
    return generation_command(
        python, plan, interleave=bool(spec.get("interleave", True))
    )


def lm_modify_command(
    python: Path, plan: RepoPlan, spec: dict[str, Any]
) -> list[str]:
    command = [
        str(python),
        "-m",
        "swesmith.bug_gen.llm.modify",
        plan.repo,
        "--config_file",
        strategy_config_file(spec, "lm_modify"),
        "--model",
        str(spec.get("model", "openai/gpt-4o")),
        "--n_bugs",
        str(positive_int(spec.get("n_bugs", 1), "lm_modify.n_bugs")),
        "--n_workers",
        str(positive_int(spec.get("n_workers", 1), "lm_modify.n_workers")),
    ]
    if spec.get("max_bugs") is not None:
        command.extend(
            ["--max_bugs", str(positive_int(spec["max_bugs"], "lm_modify.max_bugs"))]
        )
    return command


def lm_rewrite_command(
    python: Path, plan: RepoPlan, spec: dict[str, Any]
) -> list[str]:
    command = [
        str(python),
        "-m",
        "swesmith.bug_gen.llm.rewrite",
        plan.repo,
        "--config_file",
        strategy_config_file(spec, "lm_rewrite"),
        "--n_workers",
        str(positive_int(spec.get("n_workers", 1), "lm_rewrite.n_workers")),
    ]
    if spec.get("model"):
        command.extend(["--model", str(spec["model"])])
    if spec.get("max_bugs") is not None:
        command.extend(
            ["--max_bugs", str(positive_int(spec["max_bugs"], "lm_rewrite.max_bugs"))]
        )
    if spec.get("redo_existing"):
        command.append("--redo_existing")
    return command


def pr_mirror_command(
    python: Path, plan: RepoPlan, spec: dict[str, Any]
) -> list[str]:
    files = spec.get("instances_files") or []
    if not isinstance(files, list) or not files:
        raise ValueError(
            "generation.strategies.pr_mirror.instances_files must be a non-empty list"
        )
    command = [
        str(python),
        "-m",
        "swesmith.bug_gen.mirror.generate",
        *[str(entry) for entry in files],
        "--model",
        str(spec.get("model", "openai/gpt-4o")),
        "--num_processes",
        str(positive_int(spec.get("num_processes", 1), "pr_mirror.num_processes")),
    ]
    if spec.get("redo_existing"):
        command.append("--redo_existing")
    return command


STRATEGY_COMMAND_BUILDERS = {
    "procedural": procedural_command,
    "lm_modify": lm_modify_command,
    "lm_rewrite": lm_rewrite_command,
    "pr_mirror": pr_mirror_command,
}


# These strategies import litellm, which the main swesmith venv cannot load on
# Python 3.10 (litellm 1.100.1 imports typing.NotRequired). They must therefore
# run on the separate interpreter configured as paths.llm_python.
LLM_STRATEGIES = frozenset({"lm_modify", "lm_rewrite", "pr_mirror"})


def generation_commands(
    python: Path,
    plan: RepoPlan,
    strategies: dict[str, dict[str, Any]],
    *,
    llm_python: Path | None = None,
) -> list[tuple[str, list[str]]]:
    """Return (strategy name, command) pairs for every enabled generate strategy."""
    commands: list[tuple[str, list[str]]] = []
    for name, spec in enabled_strategies(strategies, "generate").items():
        interpreter = python
        if name in LLM_STRATEGIES:
            if llm_python is None:
                raise ValueError(
                    f"paths.llm_python is required to run the {name} strategy; "
                    "the main swesmith venv cannot import litellm"
                )
            interpreter = llm_python
        commands.append(
            (name, STRATEGY_COMMAND_BUILDERS[name](interpreter, plan, spec))
        )
    return commands


def combine_command(
    python: Path,
    name: str,
    spec: dict[str, Any],
    bug_gen_dir: str,
) -> list[str]:
    """Build a combine command.

    ``bug_gen_dir`` must be the *relative* ``logs/bug_gen/<repo>`` path: the
    upstream entry points assert ``bug_gen_dir.startswith("logs/bug_gen")``
    before doing anything, so an absolute path raises AssertionError. The caller
    therefore runs this with ``cwd=mutation_workspace``.
    """
    module = (
        "swesmith.bug_gen.combine.same_file"
        if name == "combine_file"
        else "swesmith.bug_gen.combine.same_module"
    )
    command = [
        str(python),
        "-m",
        module,
        bug_gen_dir,
        "--num_patches",
        str(positive_int(spec.get("num_patches", 2), f"{name}.num_patches")),
        "--max_combos",
        str(positive_int(spec.get("max_combos", 100), f"{name}.max_combos")),
    ]
    if name == "combine_file":
        command.extend(
            [
                "--limit_per_file",
                str(whole_int(spec.get("limit_per_file", -1), f"{name}.limit_per_file")),
            ]
        )
    else:
        command.extend(
            [
                "--limit_per_module",
                str(
                    whole_int(
                        spec.get("limit_per_module", -1), f"{name}.limit_per_module"
                    )
                ),
                "--depth",
                str(positive_int(spec.get("depth", 3), f"{name}.depth")),
            ]
        )
    if spec.get("include_invalid_patches"):
        command.append("--include_invalid_patches")
    return command


def run_combine_stage(
    *,
    plan: RepoPlan,
    slug: str,
    combiners: dict[str, dict[str, Any]],
    validated_output: Path,
    run_dir: Path,
    mutation_workspace: Path,
    python: Path,
    env: dict[str, str],
    validation_workers: int,
    combine_selected_patches: int,
    log_dir: Path,
) -> Path | None:
    """Merge already-validated patches, then re-collect, validate and export them."""
    combine_workspace = run_dir / "combine-workspace"
    combine_workspace.mkdir(parents=True, exist_ok=True)

    # The combine entry points read logs/task_insts/<repo>.json relative to cwd.
    gate_dir = combine_workspace / "logs" / "task_insts"
    gate_dir.mkdir(parents=True, exist_ok=True)
    gated = write_validation_gate(validated_output, gate_dir / f"{plan.repo}.json")
    if gated == 0:
        print(f"No validated patches to combine for {plan.repo}; skipping.", flush=True)
        return None
    print(f"Combine gate for {plan.repo}: {gated} validated instances", flush=True)

    bug_dir = mutation_workspace / "logs" / "bug_gen" / plan.repo
    checkout = mutation_workspace / plan.repo
    if not checkout.is_dir():
        # The last generator removed its checkout on completion, so restore one
        # for combine rather than letting it fall back to a GitHub clone.
        materialize_repo_from_image(
            plan.image_name,
            checkout,
            cwd=mutation_workspace,
            env=env,
            log_path=log_dir / "combine.source.log",
        )

    # Both entry points assert that bug_gen_dir starts with the *relative* path
    # "logs/bug_gen", and both finish with `rm -rf <repo>`. Exposing the real
    # directories through symlinks inside this throwaway workspace satisfies the
    # assertion while keeping the real bug_gen tree and checkout out of reach of
    # that cleanup.
    bug_link = combine_workspace / "logs" / "bug_gen" / plan.repo
    bug_link.parent.mkdir(parents=True, exist_ok=True)
    if not bug_link.exists():
        bug_link.symlink_to(bug_dir, target_is_directory=True)
    relative_bug_dir = f"logs/bug_gen/{plan.repo}"

    for name, spec in combiners.items():
        link = combine_workspace / plan.repo
        if not link.exists():
            if not checkout.is_dir():
                raise RuntimeError(
                    f"Missing repository checkout for combine: {checkout}"
                )
            link.symlink_to(checkout, target_is_directory=True)
        run_command(
            combine_command(python, name, spec, relative_bug_dir),
            cwd=combine_workspace,
            env=env,
            log_path=log_dir / f"combine.{name}.log",
        )

    collected = bug_dir.parent / f"{plan.repo}_all_patches.json"
    run_command(
        [str(python), "-m", "swesmith.bug_gen.collect_patches", str(bug_dir)],
        cwd=mutation_workspace,
        env=env,
        log_path=log_dir / "combine.collect.log",
    )
    candidates = run_dir / "candidates" / f"{slug}__combine.json"
    kept = filter_patch_records(
        collected, candidates, ("combine_file", "combine_module")
    )
    if kept == 0:
        print(f"Combine produced no candidates for {plan.repo}.", flush=True)
        return None

    selected = run_dir / "candidates" / f"{slug}__combine_selected.json"
    run_command(
        [
            str(python),
            str(SCRIPT_DIR / "select-diverse-candidates.py"),
            str(candidates),
            str(selected),
            "--limit",
            str(combine_selected_patches),
            "--seed",
            str(plan.selection_seed),
        ],
        cwd=LAB_REPO,
        env=env,
        log_path=log_dir / "combine.select.log",
    )

    validation_dir = mutation_workspace / "logs" / "run_validation" / plan.repo
    run_command(
        [
            str(python),
            "-m",
            "swesmith.harness.valid",
            str(selected),
            "--workers",
            str(validation_workers),
        ],
        cwd=mutation_workspace,
        env=env,
        log_path=log_dir / "combine.validate.log",
    )

    output = run_dir / "validated" / f"{slug}__combine.jsonl"
    run_command(
        [
            str(python),
            str(SCRIPT_DIR / "export-valid-local.py"),
            str(selected),
            str(validation_dir),
            str(output),
            "--image-name",
            plan.image_name,
        ],
        cwd=LAB_REPO,
        env=env,
        log_path=log_dir / "combine.export.log",
    )
    return output


def print_plan(
    config: dict[str, Any],
    plans: list[RepoPlan],
    config_path: Path,
    run_id: str,
    stop_after: str,
) -> None:
    paths = require_mapping(config.get("paths", {}), "paths")
    run_root = resolve_path(paths.get("run_root", "/data/results/multirepo-runs"))
    print(f"Config: {config_path.resolve()}")
    print(f"Run directory: {run_root / run_id}")
    print(f"Validation workers: {config['validation_workers']}")
    print(f"Target validated: {config['target_validated'] or 'all configured repos'}")
    print(f"Stop after: {stop_after}")
    print(f"Repository selection: all {len(plans)} configured repositories")
    active = [
        name
        for name, spec in config.get("generation_strategies", {}).items()
        if spec.get("enabled")
    ]
    print(f"Strategies: {', '.join(active) if active else 'none'}")
    print("Disk-space checking: disabled")
    print("Repository source: Docker image /testbed (GitHub clone not required)")
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
    all_plans = plans
    print_plan(config, plans, config_path, args.run_id, args.stop_after)
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
    llm_python_value = paths.get("llm_python")
    llm_python = resolve_path(llm_python_value) if llm_python_value else None
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
            snapshot = run_dir / "config.snapshot.yaml"
            if not snapshot.is_file() or not yaml_files_semantically_equal(
                snapshot, config_path
            ):
                raise RuntimeError("Resume config does not match the original run config")
            # Comment-only and formatting-only edits are safe to resume. Refresh
            # both metadata and the snapshot so future checks use the new file.
            manifest["config_sha256"] = config_digest
            manifest["config_metadata_refreshed_at_unix"] = time.time()
            shutil.copy2(config_path, snapshot)
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
            **target_result(0, config["target_validated"]),
        }

    run_dir.mkdir(parents=True, exist_ok=True)
    if not (run_dir / "config.snapshot.yaml").exists():
        shutil.copy2(config_path, run_dir / "config.snapshot.yaml")
    mutation_workspace = run_dir / "mutation-workspace"
    mutation_workspace.mkdir(parents=True, exist_ok=True)
    (run_dir / "candidates").mkdir(exist_ok=True)
    (run_dir / "validated").mkdir(exist_ok=True)
    manifest["status"] = "running"
    manifest["requested_stop_after"] = args.stop_after
    manifest["last_selected_repositories"] = [plan.repo for plan in plans]
    manifest["last_started_at_unix"] = time.time()
    manifest.pop("error", None)
    manifest.pop("finished_at_unix", None)
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

    candidate_outputs: list[Path] = []
    validated_outputs: list[Path] = []
    rejection_summaries: list[Path] = []
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
                stage_started = time.monotonic()
                repo_state["image_action"] = ensure_image(
                    plan,
                    cwd=mutation_workspace,
                    env=env,
                    log_path=repo_log_dir / "image.log",
                )
                repo_state["stages"]["image"] = "completed"
                record_stage_seconds(repo_state, "image", stage_started)
                write_json(manifest_path, manifest)

            bug_dir = mutation_workspace / "logs" / "bug_gen" / plan.repo
            if repo_state["stages"].get("generate") != "completed":
                stage_started = time.monotonic()
                checkout = mutation_workspace / plan.repo
                repo_state["source_action"] = materialize_repo_from_image(
                    plan.image_name,
                    checkout,
                    cwd=mutation_workspace,
                    env=env,
                    log_path=repo_log_dir / "source.log",
                )
                repo_state["source"] = f"{plan.image_name}:/testbed"
                write_json(manifest_path, manifest)
                generators = generation_commands(
                    swesmith_python,
                    plan,
                    config["generation_strategies"],
                    llm_python=llm_python,
                )
                if not generators:
                    raise RuntimeError(
                        f"No generate-stage strategy is enabled for {plan.repo}"
                    )
                for strategy_name, command in generators:
                    # Upstream generators rmtree their checkout on completion, so
                    # restore it before every strategy that needs one. This is
                    # idempotent and avoids the GitHub clone path entirely.
                    if not checkout.is_dir():
                        materialize_repo_from_image(
                            plan.image_name,
                            checkout,
                            cwd=mutation_workspace,
                            env=env,
                            log_path=repo_log_dir / f"source.{strategy_name}.log",
                        )
                    run_command(
                        command,
                        cwd=mutation_workspace,
                        env=env,
                        log_path=repo_log_dir / f"generate.{strategy_name}.log",
                    )
                if not bug_dir.is_dir() or not any(bug_dir.rglob("*.diff")):
                    raise RuntimeError(f"Generation produced no patches for {plan.repo}")
                repo_state["generate_strategies"] = [name for name, _ in generators]
                repo_state["stages"]["generate"] = "completed"
                record_stage_seconds(repo_state, "generate", stage_started)
                write_json(manifest_path, manifest)

            collected = bug_dir.parent / f"{plan.repo}_all_patches.json"
            if repo_state["stages"].get("collect") != "completed" or not collected.exists():
                stage_started = time.monotonic()
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
                record_stage_seconds(repo_state, "collect", stage_started)
                write_json(manifest_path, manifest)

            selected = run_dir / "candidates" / f"{slug}.json"
            if repo_state["stages"].get("select") != "completed" or not selected.exists():
                stage_started = time.monotonic()
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
                record_stage_seconds(repo_state, "select", stage_started)
                write_json(manifest_path, manifest)
            candidate_outputs.append(selected)

            if args.stop_after == "candidates":
                repo_state["status"] = "candidates_ready"
                write_json(manifest_path, manifest)
                continue

            validation_dir = mutation_workspace / "logs" / "run_validation" / plan.repo
            if repo_state["stages"].get("validate") != "completed":
                stage_started = time.monotonic()
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
                record_stage_seconds(repo_state, "validate", stage_started)
                write_json(manifest_path, manifest)

            if repo_state["stages"].get("export") != "completed" or not validated_output.exists():
                stage_started = time.monotonic()
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
                record_stage_seconds(repo_state, "export", stage_started)
                write_json(manifest_path, manifest)

            valid_count = count_records(validated_output)
            repo_state["valid_count"] = valid_count
            validated_outputs.append(validated_output)
            rejection_summaries.append(validated_output.with_suffix(".summary.json"))
            validated_total += valid_count
            manifest["validated_count"] = validated_total
            write_json(manifest_path, manifest)

            combiners = enabled_strategies(
                config["generation_strategies"], "post_validate"
            )
            if combiners and repo_state["stages"].get("combine") != "completed":
                combine_output = run_combine_stage(
                    plan=plan,
                    slug=slug,
                    combiners=combiners,
                    validated_output=validated_output,
                    run_dir=run_dir,
                    mutation_workspace=mutation_workspace,
                    python=swesmith_python,
                    env=env,
                    validation_workers=config["validation_workers"],
                    combine_selected_patches=config["combine_selected_patches"],
                    log_dir=repo_log_dir,
                )
                repo_state["stages"]["combine"] = "completed"
                if combine_output is not None:
                    combine_count = count_records(combine_output)
                    repo_state["combine_valid_count"] = combine_count
                    validated_outputs.append(combine_output)
                    rejection_summaries.append(
                        combine_output.with_suffix(".summary.json")
                    )
                    validated_total += combine_count
                    manifest["validated_count"] = validated_total
                write_json(manifest_path, manifest)

            repo_state["status"] = "completed"
            write_json(manifest_path, manifest)

        # Rebuild the root queue from every repository artifact created so far.
        # This also preserves completed outputs when resuming an interrupted run.
        candidate_outputs = [
            run_dir / "candidates" / f"{repo_slug(plan.repo)}.json"
            for plan in all_plans
            if (run_dir / "candidates" / f"{repo_slug(plan.repo)}.json").is_file()
        ]
        if not candidate_outputs:
            raise RuntimeError("No repository produced a candidate output")
        candidate_queue = run_dir / "candidates.jsonl"
        candidate_summary = build_candidate_queue(candidate_outputs, candidate_queue)
        manifest["candidate_count"] = candidate_summary["queued_count"]
        manifest["candidate_repository_count"] = len(candidate_outputs)
        manifest["configured_repository_count"] = len(all_plans)
        manifest["missing_candidate_repositories"] = [
            plan.repo
            for plan in all_plans
            if not (run_dir / "candidates" / f"{repo_slug(plan.repo)}.json").is_file()
        ]
        manifest.setdefault("artifacts", {})["candidates"] = str(candidate_queue)
        manifest["candidate_deduplication"] = candidate_summary
        write_json(manifest_path, manifest)

        if args.stop_after == "candidates":
            manifest["status"] = (
                "completed_candidates"
                if not manifest["missing_candidate_repositories"]
                else "completed_candidates_partial"
            )
            manifest["finished_at_unix"] = time.time()
            manifest["last_elapsed_seconds"] = round(
                manifest["finished_at_unix"] - manifest["last_started_at_unix"], 3
            )
            write_json(manifest_path, manifest)
            print(f"Run status={manifest['status']}: {manifest_path}")
            return

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
        manifest.update(target_result(validated_total, target_validated))
        manifest.setdefault("artifacts", {})["validated"] = str(combined)
        rejected_output = run_dir / "rejected.jsonl"
        rejection_report = build_rejection_report(
            rejection_summaries, rejected_output
        )
        manifest["artifacts"]["rejected"] = str(rejected_output)
        manifest["validation_rejections"] = rejection_report
        write_json(manifest_path, manifest)

        if args.stop_after == "validation":
            suffix = "" if manifest["target_reached"] else "_with_shortfall"
            manifest["status"] = f"completed_validation{suffix}"
            manifest["finished_at_unix"] = time.time()
            manifest["last_elapsed_seconds"] = round(
                manifest["finished_at_unix"] - manifest["last_started_at_unix"], 3
            )
            write_json(manifest_path, manifest)
            print(f"Run status={manifest['status']}: {manifest_path}")
            return

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

        manifest["status"] = (
            "completed"
            if manifest["target_reached"]
            else "completed_with_shortfall"
        )
        manifest["finished_at_unix"] = time.time()
        manifest["last_elapsed_seconds"] = round(
            manifest["finished_at_unix"] - manifest["last_started_at_unix"], 3
        )
        manifest.pop("error", None)
        write_json(manifest_path, manifest)
    except Exception as error:
        manifest["status"] = "failed"
        manifest["finished_at_unix"] = time.time()
        manifest["error"] = f"{type(error).__name__}: {error}"
        write_json(manifest_path, manifest)
        raise

    if manifest["status"] == "completed_with_shortfall":
        print(
            "Completed configured repositories with a validation shortfall: "
            f"target={manifest['target_validated']} "
            f"actual={manifest['validated_count']} "
            f"shortfall={manifest['validated_shortfall']}"
        )
    print(f"Run status={manifest['status']}: {manifest_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted; rerun with the same --run-id and --resume.", file=sys.stderr)
        raise SystemExit(130)
