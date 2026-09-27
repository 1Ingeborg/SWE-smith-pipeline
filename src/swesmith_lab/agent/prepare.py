#!/usr/bin/env python3
"""Agent helper: select tasks and build leak-resistant SWE-agent images."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shlex
import string
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
LAB_REPO = Path(__file__).resolve().parents[3]
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
IMAGE_REPOSITORY_PATTERN = re.compile(
    r"^(?:[a-z0-9]+(?:[._-][a-z0-9]+)*/)*[a-z0-9]+(?:[._-][a-z0-9]+)*$"
)
PUBLIC_INSTANCE_KEYS = {
    "instance_id",
    "image_name",
    "problem_statement",
    "repo_name",
    "base_commit",
}
REQUIRED_TASK_FIELDS = {
    "instance_id",
    "repo",
    "image_name",
    "patch",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
    "problem_statement",
}
OPAQUE_ALPHABET = string.ascii_lowercase + string.digits


@dataclass(frozen=True)
class PilotConfig:
    source_dataset: Path
    audit_path: Path | None
    run_root: Path
    seed: int
    per_repository: int
    require_full_quota: bool
    image_repository: str
    platform: str
    memory_limit: str
    remove_f2p_tests: bool
    runtime_enabled: bool
    runtime_repository: str
    runtime_package: str
    runtime_tool_packages: tuple[str, ...]
    runtime_index_url: str
    runtime_trusted_host: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse the saved selection and already-built task images.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the selection without writing files or using Docker.",
    )
    parser.add_argument(
        "--select-only",
        action="store_true",
        help="Write the private selection but do not build task images.",
    )
    parser.add_argument(
        "--image-limit",
        type=int,
        help="Build at most the first N task images (useful for a one-task smoke test).",
    )
    return parser.parse_args()


def validate_run_id(run_id: str) -> None:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError(
            "run-id must contain only letters, numbers, dot, underscore, and hyphen"
        )


def require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def nonempty_string_list(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{name} entries must be non-empty strings")
    return tuple(value)


def resolve_path(value: str | Path, *, base: Path = LAB_REPO) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


def load_config(path: Path) -> PilotConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    config = require_mapping(raw, "config")
    if config.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")

    source = require_mapping(config.get("source", {}), "source")
    selection = require_mapping(config.get("selection", {}), "selection")
    image = require_mapping(config.get("task_images", {}), "task_images")
    runtime = require_mapping(image.get("runtime", {}), "task_images.runtime")

    source_dataset_value = source.get("dataset")
    if not isinstance(source_dataset_value, str) or not source_dataset_value.strip():
        raise ValueError("source.dataset must be a non-empty path")
    audit_value = source.get("audit")
    if audit_value is not None and (
        not isinstance(audit_value, str) or not audit_value.strip()
    ):
        raise ValueError("source.audit must be a path or null")
    run_root_value = config.get("run_root", "results/agent-pilot-runs")
    if not isinstance(run_root_value, str) or not run_root_value.strip():
        raise ValueError("run_root must be a non-empty path")

    image_repository = image.get("repository", "swesmith-lab/agent-task")
    if not isinstance(image_repository, str) or not IMAGE_REPOSITORY_PATTERN.fullmatch(
        image_repository
    ):
        raise ValueError("task_images.repository is not a valid lowercase image name")
    platform = image.get("platform", "linux/x86_64")
    memory_limit = image.get("memory_limit", "10g")
    if not isinstance(platform, str) or not platform:
        raise ValueError("task_images.platform must be a non-empty string")
    if not isinstance(memory_limit, str) or not memory_limit:
        raise ValueError("task_images.memory_limit must be a non-empty string")
    runtime_repository = runtime.get(
        "repository", "swesmith-lab/agent-runtime"
    )
    if not isinstance(
        runtime_repository, str
    ) or not IMAGE_REPOSITORY_PATTERN.fullmatch(runtime_repository):
        raise ValueError(
            "task_images.runtime.repository is not a valid lowercase image name"
        )
    runtime_package = runtime.get("package", "swe-rex==1.4.0")
    runtime_tool_packages = nonempty_string_list(
        runtime.get("tool_packages", []), "task_images.runtime.tool_packages"
    )
    runtime_index_url = runtime.get(
        "index_url", "https://pypi.org/simple"
    )
    runtime_trusted_host = runtime.get("trusted_host", "")
    for name, value in (
        ("task_images.runtime.package", runtime_package),
        ("task_images.runtime.index_url", runtime_index_url),
        ("task_images.runtime.trusted_host", runtime_trusted_host),
    ):
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
    if not runtime_package or not runtime_index_url:
        raise ValueError("runtime package and index_url must be non-empty")

    seed = selection.get("seed", 42)
    if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
        raise ValueError("selection.seed must be a non-negative integer")

    return PilotConfig(
        source_dataset=resolve_path(source_dataset_value),
        audit_path=resolve_path(audit_value) if audit_value is not None else None,
        run_root=resolve_path(run_root_value),
        seed=seed,
        per_repository=positive_int(
            selection.get("per_repository", 3), "selection.per_repository"
        ),
        require_full_quota=bool(selection.get("require_full_quota", True)),
        image_repository=image_repository,
        platform=platform,
        memory_limit=memory_limit,
        remove_f2p_tests=bool(image.get("remove_f2p_tests", True)),
        runtime_enabled=bool(runtime.get("enabled", True)),
        runtime_repository=runtime_repository,
        runtime_package=runtime_package,
        runtime_tool_packages=runtime_tool_packages,
        runtime_index_url=runtime_index_url,
        runtime_trusted_host=runtime_trusted_host,
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: Any, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if private:
        os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def atomic_write_jsonl(
    path: Path, rows: Iterable[dict[str, Any]], *, private: bool = False
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    if private:
        os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"Expected an object at {path}:{line_number}")
            rows.append(value)
    return rows


def validate_task(task: dict[str, Any]) -> None:
    missing = sorted(REQUIRED_TASK_FIELDS - task.keys())
    if missing:
        raise ValueError(f"Task is missing fields {missing}: {task.get('instance_id')}")
    for field in ("instance_id", "repo", "image_name", "patch", "problem_statement"):
        if not isinstance(task[field], str) or not task[field].strip():
            raise ValueError(f"{field} must be a non-empty string")
    for field in ("FAIL_TO_PASS", "PASS_TO_PASS"):
        if not isinstance(task[field], list) or not task[field]:
            raise ValueError(f"{field} must be a non-empty list")
        if not all(isinstance(item, str) and item for item in task[field]):
            raise ValueError(f"{field} entries must be non-empty strings")


def mutation_paths_that_are_test_files(task: dict[str, Any]) -> list[str]:
    """Return mutated paths that are themselves FAIL_TO_PASS test files.

    A few suites keep their tests inside the modules they exercise (patsy does
    this throughout). Hiding the F2P test file would then delete the very file
    the mutation lives in, leaving the Agent nothing to repair, so such tasks
    are dropped before any image is built.
    """
    from swesmith.profiles import registry

    mutated = set(patch_paths(task["patch"]))
    test_files, _ = registry.get_from_inst(task).get_test_files(task)
    return sorted(mutated & {safe_repo_path(path) for path in test_files})


def load_source_tasks(config: PilotConfig) -> list[dict[str, Any]]:
    tasks = load_jsonl(config.source_dataset)
    seen: set[str] = set()
    for task in tasks:
        validate_task(task)
        instance_id = task["instance_id"]
        if instance_id in seen:
            raise ValueError(f"Duplicate source instance_id: {instance_id}")
        seen.add(instance_id)

    if config.audit_path is not None:
        audits = {
            row["instance_id"]: row
            for row in load_jsonl(config.audit_path)
            if isinstance(row.get("instance_id"), str)
        }
        for task in tasks:
            audit = audits.get(task["instance_id"])
            if audit is not None:
                task["_audit"] = audit

    if config.remove_f2p_tests:
        kept: list[dict[str, Any]] = []
        dropped: list[tuple[str, list[str]]] = []
        for task in tasks:
            collisions = mutation_paths_that_are_test_files(task)
            if collisions:
                dropped.append((task["instance_id"], collisions))
            else:
                kept.append(task)
        if dropped:
            print(
                f"Skipping {len(dropped)} tasks whose mutated file is itself a "
                f"FAIL_TO_PASS test file:"
            )
            for instance_id, collisions in dropped[:5]:
                print(f"  {instance_id}: {', '.join(collisions)}")
            if len(dropped) > 5:
                print(f"  ... and {len(dropped) - 5} more")
        tasks = kept
        if not tasks:
            raise ValueError("Every task was dropped by the test-file collision check")
    return tasks


def _seed_for_repo(seed: int, repo: str) -> int:
    value = sha256_bytes(f"{seed}\0{repo}".encode("utf-8"))
    return int(value[:16], 16)


def _opaque_token(rng: random.Random, length: int = 10) -> str:
    return "".join(rng.choice(OPAQUE_ALPHABET) for _ in range(length))


def select_balanced_tasks(
    tasks: list[dict[str, Any]],
    *,
    per_repository: int,
    seed: int,
    require_full_quota: bool = True,
) -> list[dict[str, Any]]:
    """Select a fixed repository quota while preferring distinct strategies."""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        grouped[task["repo"]].append(task)

    selected: list[dict[str, Any]] = []
    for repo in sorted(grouped):
        candidates = grouped[repo]
        if require_full_quota and len(candidates) < per_repository:
            raise ValueError(
                f"Repository {repo} has {len(candidates)} tasks, fewer than quota "
                f"{per_repository}"
            )
        quota = min(per_repository, len(candidates))
        rng = random.Random(_seed_for_repo(seed, repo))
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for task in sorted(candidates, key=lambda item: item["instance_id"]):
            strategy = task.get("strategy")
            buckets[str(strategy) if strategy is not None else "(unknown)"].append(task)
        for bucket in buckets.values():
            rng.shuffle(bucket)
        strategies = list(buckets)
        rng.shuffle(strategies)

        repo_selected: list[dict[str, Any]] = []
        while len(repo_selected) < quota:
            made_progress = False
            for strategy in list(strategies):
                bucket = buckets[strategy]
                if not bucket:
                    strategies.remove(strategy)
                    continue
                repo_selected.append(bucket.pop())
                made_progress = True
                if len(repo_selected) == quota:
                    break
            if not made_progress:
                break
            rng.shuffle(strategies)
        selected.extend(repo_selected)

    global_rng = random.Random(seed ^ 0x5EED5EED)
    global_rng.shuffle(selected)
    used_ids: set[str] = set()
    prepared: list[dict[str, Any]] = []
    for index, task in enumerate(selected, 1):
        token = _opaque_token(global_rng)
        agent_id = f"agent-task-{index:04d}-{token}"
        if agent_id in used_ids:
            raise RuntimeError("Opaque Agent id collision")
        used_ids.add(agent_id)
        row = dict(task)
        row["_agent_instance_id"] = agent_id
        prepared.append(row)
    return prepared


_STRATEGY_IN_INSTANCE_ID = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)__[A-Za-z0-9]+$")


def resolved_strategy(task: dict[str, Any]) -> str:
    """Return the task's mutation strategy, recovering it from the instance id.

    Upstream leaves ``strategy`` null for the ``combine_*`` families, so every
    combine_file task would otherwise be counted as ``(unknown)`` in the
    selection summary. The strategy is still unambiguous in the instance id
    (``<repo>.<hash>.<strategy>__<token>``), so recover it from there.
    """
    strategy = task.get("strategy")
    if strategy is not None:
        return str(strategy)
    match = _STRATEGY_IN_INSTANCE_ID.search(str(task.get("instance_id", "")))
    return match.group(1) if match else "(unknown)"


def selection_summary(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "selected": len(tasks),
        "repositories": dict(sorted(Counter(task["repo"] for task in tasks).items())),
        "strategies": dict(sorted(Counter(resolved_strategy(task) for task in tasks).items())),
    }


def safe_repo_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or "\x00" in value or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe repository path: {value!r}")
    normalized = str(path)
    if normalized in {".", ""}:
        raise ValueError(f"Unsafe repository path: {value!r}")
    return normalized


def patch_paths(patch: str) -> list[str]:
    from unidiff import PatchSet

    paths = []
    for patched_file in PatchSet(patch):
        paths.append(safe_repo_path(patched_file.path))
    if not paths:
        raise ValueError("Mutation patch contains no files")
    return paths


def split_image_reference(reference: str) -> tuple[str, str]:
    if ":" not in reference:
        return reference, "latest"
    repository, tag = reference.rsplit(":", 1)
    if "/" in tag or not repository or not tag:
        raise ValueError(f"Expected a tagged Docker image reference: {reference}")
    return repository, tag


def task_image_name(image_repository: str, run_id: str, agent_id: str) -> str:
    run_hash = sha256_bytes(run_id.encode("utf-8"))[:8]
    tag = f"r{run_hash}-{agent_id.lower()}"
    if len(tag) > 128:
        tag = f"r{run_hash}-{sha256_bytes(agent_id.encode('utf-8'))[:24]}"
    return f"{image_repository}:{tag}"


def public_instance(task: dict[str, Any], image_name: str) -> dict[str, str]:
    value = {
        "instance_id": task["_agent_instance_id"],
        "image_name": image_name,
        "problem_statement": task["problem_statement"],
        "repo_name": "testbed",
        "base_commit": "HEAD",
    }
    assert set(value) == PUBLIC_INSTANCE_KEYS
    return value


def _decode_exec_output(result: Any) -> str:
    output = result.output
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace")
    return str(output)


def exec_checked(
    container: Any,
    command: str | list[str],
    *,
    workdir: str = "/testbed",
    user: str | None = None,
    environment: dict[str, str] | None = None,
) -> str:
    result = container.exec_run(
        command,
        workdir=workdir,
        user=user,
        environment=environment,
    )
    output = _decode_exec_output(result)
    if result.exit_code != 0:
        rendered = command if isinstance(command, str) else shlex.join(command)
        raise RuntimeError(
            f"Container command failed ({result.exit_code}): {rendered}\n{output}"
        )
    return output


def apply_patch_in_container(
    container: Any,
    patch_path: str,
    *,
    reverse: bool = False,
    user: str | None = None,
) -> str:
    from swesmith.constants import GIT_APPLY_CMDS

    errors: list[str] = []
    for base_command in GIT_APPLY_CMDS:
        reverse_flag = " --reverse" if reverse else ""
        command = f"{base_command}{reverse_flag} {shlex.quote(patch_path)}"
        result = container.exec_run(command, workdir="/testbed", user=user)
        output = _decode_exec_output(result)
        if result.exit_code == 0:
            return output
        errors.append(f"{command}: {output}")
    raise RuntimeError("Failed to apply patch:\n" + "\n".join(errors))


def _valid_git_tag(tag: str) -> bool:
    return bool(
        tag
        and len(tag) <= 128
        and not tag.startswith("-")
        and ".." not in tag
        and "@{" not in tag
        and not tag.endswith(".")
        and not tag.endswith("/")
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", tag)
    )


def runtime_image_name(
    runtime_repository: str,
    source_image_id: str,
    runtime_package: str,
    runtime_tool_packages: tuple[str, ...] = (),
) -> str:
    image_token = source_image_id.removeprefix("sha256:")[:16]
    runtime_spec = json.dumps(
        {
            "runtime_package": runtime_package,
            "tool_packages": list(runtime_tool_packages),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    package_token = sha256_bytes(runtime_spec.encode("utf-8"))[:10]
    return f"{runtime_repository}:p{package_token}-i{image_token}"


def ensure_runtime_image(
    client: Any,
    source_image_name: str,
    *,
    config: PilotConfig,
) -> tuple[str, str]:
    """Install SWE-ReX in an isolated venv once per repository base image."""
    import docker
    from swebench.harness.constants import DOCKER_USER

    source_image = client.images.get(source_image_name)
    if not config.runtime_enabled:
        return source_image_name, source_image.id

    image_name = runtime_image_name(
        config.runtime_repository,
        source_image.id,
        config.runtime_package,
        config.runtime_tool_packages,
    )
    try:
        existing = client.images.get(image_name)
        return image_name, existing.id
    except docker.errors.ImageNotFound:
        pass

    container = None
    try:
        container_name = "swesmith-agent-runtime-" + sha256_bytes(
            image_name.encode("utf-8")
        )[:16]
        container = client.containers.create(
            image=source_image_name,
            name=container_name,
            user=DOCKER_USER,
            detach=True,
            command="tail -f /dev/null",
            platform=config.platform,
            mem_limit=config.memory_limit,
        )
        container.start()
        exec_checked(
            container,
            ["python3", "-m", "venv", "/opt/swerex"],
            workdir="/",
            user=DOCKER_USER,
        )
        pip_environment = {
            "PIP_INDEX_URL": config.runtime_index_url,
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        }
        if config.runtime_trusted_host:
            pip_environment["PIP_TRUSTED_HOST"] = config.runtime_trusted_host
        exec_checked(
            container,
            [
                "/opt/swerex/bin/python",
                "-m",
                "pip",
                "install",
                "--no-cache-dir",
                config.runtime_package,
            ],
            workdir="/",
            user=DOCKER_USER,
            environment=pip_environment,
        )
        if config.runtime_tool_packages:
            project_python = exec_checked(
                container,
                ["/bin/bash", "-lc", "command -v python"],
                workdir="/testbed",
                user=DOCKER_USER,
            ).strip()
            if not project_python.startswith("/"):
                raise RuntimeError(
                    f"Could not resolve the project Python interpreter: {project_python!r}"
                )
            exec_checked(
                container,
                [
                    project_python,
                    "-m",
                    "pip",
                    "install",
                    "--no-cache-dir",
                    *config.runtime_tool_packages,
                ],
                workdir="/testbed",
                user=DOCKER_USER,
                environment=pip_environment,
            )
        exec_checked(
            container,
            [
                "ln",
                "-sf",
                "/opt/swerex/bin/swerex-remote",
                "/usr/local/bin/swerex-remote",
            ],
            workdir="/",
            user=DOCKER_USER,
        )
        exec_checked(
            container,
            ["/usr/local/bin/swerex-remote", "--help"],
            workdir="/",
            user=DOCKER_USER,
        )
        repository, tag = split_image_reference(image_name)
        built = container.commit(
            repository=repository,
            tag=tag,
            message=(
                f"Agent runtime: {config.runtime_package}; tools: "
                + ", ".join(config.runtime_tool_packages)
            ),
        )
        return image_name, built.id
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass


def build_task_image(
    task: dict[str, Any],
    *,
    run_id: str,
    run_dir: Path,
    config: PilotConfig,
) -> dict[str, Any]:
    import docker
    from swebench.harness.constants import DOCKER_USER
    from swebench.harness.docker_utils import copy_to_container
    from swesmith.profiles import registry

    started = time.monotonic()
    agent_id = task["_agent_instance_id"]
    image_name = task_image_name(config.image_repository, run_id, agent_id)
    client = docker.from_env()
    container = None
    patch_file = run_dir / "private" / "patches" / f"{agent_id}.diff"
    patch_file.parent.mkdir(parents=True, exist_ok=True)
    patch_file.write_text(task["patch"], encoding="utf-8")
    os.chmod(patch_file, 0o600)
    mutation_paths = patch_paths(task["patch"])
    rp = registry.get_from_inst(task)
    f2p_files, _ = rp.get_test_files(task)
    f2p_files = sorted({safe_repo_path(path) for path in f2p_files})

    try:
        try:
            client.images.get(task["image_name"])
        except docker.errors.ImageNotFound as exc:
            raise RuntimeError(
                f"Base image is not present locally: {task['image_name']}"
            ) from exc

        runtime_base_name, runtime_base_id = ensure_runtime_image(
            client, task["image_name"], config=config
        )

        container_name = f"swesmith-agent-prep-{sha256_bytes((run_id + agent_id).encode())[:16]}"
        container = client.containers.create(
            image=runtime_base_name,
            name=container_name,
            user=DOCKER_USER,
            detach=True,
            command="tail -f /dev/null",
            platform=config.platform,
            mem_limit=config.memory_limit,
        )
        container.start()

        original_head = exec_checked(
            container, ["git", "rev-parse", "HEAD"], user=DOCKER_USER
        ).strip()
        tag_result = container.exec_run(
            ["git", "describe", "--tags", "--exact-match", "HEAD"],
            workdir="/testbed",
            user=DOCKER_USER,
        )
        original_exact_tag = (
            _decode_exec_output(tag_result).strip() if tag_result.exit_code == 0 else None
        )

        container_patch = f"/tmp/{agent_id}.diff"
        copy_to_container(container, patch_file, Path(container_patch))
        apply_patch_in_container(container, container_patch, user=DOCKER_USER)
        exec_checked(container, ["rm", "-f", container_patch], workdir="/", user=DOCKER_USER)

        if config.remove_f2p_tests:
            for path in f2p_files:
                exec_checked(
                    container,
                    ["rm", "-rf", "--", f"/testbed/{path}"],
                    workdir="/",
                    user=DOCKER_USER,
                )

        # This exact path is inside a disposable container created above. Removing the
        # original object database prevents the Agent from recovering the clean code or
        # mutation diff with git log/show.
        exec_checked(
            container,
            ["rm", "-rf", "--", "/testbed/.git"],
            workdir="/",
            user=DOCKER_USER,
        )
        exec_checked(container, ["git", "init", "-q"], user=DOCKER_USER)
        exec_checked(
            container,
            ["git", "config", "user.email", "agent-task@invalid.local"],
            user=DOCKER_USER,
        )
        exec_checked(
            container,
            ["git", "config", "user.name", "SWE-smith Lab"],
            user=DOCKER_USER,
        )
        exec_checked(container, ["git", "add", "-A"], user=DOCKER_USER)
        git_dates = {
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
        }
        exec_checked(
            container,
            ["git", "commit", "-q", "-m", "Agent task baseline"],
            user=DOCKER_USER,
            environment=git_dates,
        )
        if original_exact_tag and _valid_git_tag(original_exact_tag):
            exec_checked(
                container, ["git", "tag", original_exact_tag], user=DOCKER_USER
            )

        commit_count = exec_checked(
            container, ["git", "rev-list", "--all", "--count"], user=DOCKER_USER
        ).strip()
        if commit_count != "1":
            raise RuntimeError(f"Sanitized task repository has {commit_count} commits")
        status = exec_checked(
            container, ["git", "status", "--porcelain"], user=DOCKER_USER
        )
        if status.strip():
            raise RuntimeError(f"Sanitized task repository is dirty:\n{status}")
        for path in mutation_paths:
            result = container.exec_run(
                ["test", "-e", f"/testbed/{path}"], workdir="/", user=DOCKER_USER
            )
            if result.exit_code != 0:
                raise RuntimeError(f"Mutated source file disappeared: {path}")
        if config.remove_f2p_tests:
            for path in f2p_files:
                result = container.exec_run(
                    ["test", "-e", f"/testbed/{path}"],
                    workdir="/",
                    user=DOCKER_USER,
                )
                if result.exit_code == 0:
                    raise RuntimeError(f"F2P test file was not hidden: {path}")

        repository, tag = split_image_reference(image_name)
        built_image = container.commit(
            repository=repository,
            tag=tag,
            message="Leak-resistant local SWE-agent task image",
        )
        return {
            "status": "ready",
            "agent_instance_id": agent_id,
            "source_instance_id": task["instance_id"],
            "source_image_name": task["image_name"],
            "runtime_base_name": runtime_base_name,
            "runtime_base_id": runtime_base_id,
            "runtime_package": config.runtime_package
            if config.runtime_enabled
            else None,
            "runtime_tool_packages": list(config.runtime_tool_packages)
            if config.runtime_enabled
            else [],
            "task_image_name": image_name,
            "task_image_id": built_image.id,
            "patch_sha256": sha256_bytes(task["patch"].encode("utf-8")),
            "mutation_paths": mutation_paths,
            "f2p_test_files_removed": f2p_files if config.remove_f2p_tests else [],
            "original_head": original_head,
            "original_exact_tag": original_exact_tag,
            "seconds": round(time.monotonic() - started, 3),
        }
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass
        try:
            patch_file.unlink()
        except FileNotFoundError:
            pass


def load_manifest(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    records = value.get("images", [])
    if not isinstance(records, list):
        raise ValueError(f"Invalid image manifest: {path}")
    return {
        record["agent_instance_id"]: record
        for record in records
        if isinstance(record, dict) and isinstance(record.get("agent_instance_id"), str)
    }


def image_is_present(reference: str) -> bool:
    import docker

    try:
        docker.from_env().images.get(reference)
        return True
    except docker.errors.ImageNotFound:
        return False


def prepare_selection(
    config: PilotConfig,
    *,
    run_dir: Path,
    config_path: Path,
    resume: bool,
) -> list[dict[str, Any]]:
    selected_path = run_dir / "private" / "selected.jsonl"
    metadata_path = run_dir / "private" / "run-metadata.json"
    source_hash = sha256_file(config.source_dataset)
    config_hash = sha256_file(config_path)

    if resume:
        if not selected_path.exists() or not metadata_path.exists():
            raise RuntimeError("Cannot resume: saved selection metadata is missing")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("source_sha256") != source_hash:
            raise RuntimeError("Cannot resume: source dataset content changed")
        if metadata.get("config_sha256") != config_hash:
            raise RuntimeError("Cannot resume: pilot config content changed")
        return load_jsonl(selected_path)

    if run_dir.exists() and any(run_dir.iterdir()):
        raise RuntimeError(
            f"Run directory already contains files; use --resume or a new run-id: {run_dir}"
        )
    tasks = load_source_tasks(config)
    selected = select_balanced_tasks(
        tasks,
        per_repository=config.per_repository,
        seed=config.seed,
        require_full_quota=config.require_full_quota,
    )
    atomic_write_jsonl(selected_path, selected, private=True)
    atomic_write_json(
        metadata_path,
        {
            "schema_version": 1,
            "source_dataset": str(config.source_dataset),
            "source_sha256": source_hash,
            "audit_path": str(config.audit_path) if config.audit_path else None,
            "config_path": str(config_path),
            "config_sha256": config_hash,
            "seed": config.seed,
            "per_repository": config.per_repository,
        },
        private=True,
    )
    return selected


def main() -> None:
    args = parse_args()
    validate_run_id(args.run_id)
    if args.image_limit is not None and args.image_limit < 1:
        raise ValueError("--image-limit must be positive")
    config_path = args.config.resolve()
    config = load_config(config_path)
    tasks = load_source_tasks(config)
    selected_preview = select_balanced_tasks(
        tasks,
        per_repository=config.per_repository,
        seed=config.seed,
        require_full_quota=config.require_full_quota,
    )
    summary = selection_summary(selected_preview)
    run_dir = config.run_root / args.run_id

    print(f"Config: {config_path}")
    print(f"Source: {config.source_dataset} ({len(tasks)} accepted tasks)")
    print(f"Run directory: {run_dir}")
    print(f"Selected: {summary['selected']}")
    print(f"Repositories: {json.dumps(summary['repositories'], ensure_ascii=False)}")
    print(f"Strategies: {len(summary['strategies'])} distinct")
    print("Agent-visible ids are opaque; mutation metadata remains private.")
    if args.dry_run:
        print("Dry run completed; no files were written and Docker was not used.")
        return

    selected = prepare_selection(
        config,
        run_dir=run_dir,
        config_path=config_path,
        resume=args.resume,
    )
    aggregate_summary = {
        "schema_version": 1,
        "run_id": args.run_id,
        **selection_summary(selected),
        "task_images_ready": 0,
        "public_instances": 0,
    }
    summary_path = run_dir / "summary.json"
    atomic_write_json(summary_path, aggregate_summary)
    print(f"Private selection: {run_dir / 'private' / 'selected.jsonl'}")
    if args.select_only:
        print("Selection completed; task images were not built.")
        return

    manifest_path = run_dir / "private" / "task-images.json"
    records = load_manifest(manifest_path)
    targets = selected[: args.image_limit] if args.image_limit else selected
    for index, task in enumerate(targets, 1):
        agent_id = task["_agent_instance_id"]
        existing = records.get(agent_id)
        if existing and existing.get("status") == "ready" and image_is_present(
            existing["task_image_name"]
        ):
            print(f"[{index}/{len(targets)}] Reusing {existing['task_image_name']}")
            continue
        print(f"[{index}/{len(targets)}] Building isolated image for {agent_id}")
        try:
            record = build_task_image(
                task,
                run_id=args.run_id,
                run_dir=run_dir,
                config=config,
            )
        except Exception as exc:
            records[agent_id] = {
                "status": "error",
                "agent_instance_id": agent_id,
                "source_instance_id": task["instance_id"],
                "error": str(exc),
            }
            atomic_write_json(
                manifest_path, {"schema_version": 1, "images": list(records.values())}, private=True
            )
            raise
        records[agent_id] = record
        atomic_write_json(
            manifest_path,
            {"schema_version": 1, "images": list(records.values())},
            private=True,
        )

    public_rows: list[dict[str, str]] = []
    for task in selected:
        record = records.get(task["_agent_instance_id"])
        if record and record.get("status") == "ready" and image_is_present(
            record["task_image_name"]
        ):
            public_rows.append(public_instance(task, record["task_image_name"]))
    public_path = run_dir / "public" / "instances.jsonl"
    atomic_write_jsonl(public_path, public_rows)
    aggregate_summary["task_images_ready"] = len(public_rows)
    aggregate_summary["public_instances"] = len(public_rows)
    aggregate_summary["selection_complete"] = len(selected) == summary["selected"]
    aggregate_summary["image_preparation_complete"] = len(public_rows) == len(selected)
    atomic_write_json(summary_path, aggregate_summary)

    print(f"Public SWE-agent instances: {public_path} ({len(public_rows)} rows)")
    print(f"Private image manifest: {manifest_path}")
    if len(public_rows) < len(selected):
        print("Only part of the selection is image-ready; rerun with --resume to continue.")
    else:
        print("All selected tasks are ready for the zero-API SWE-agent smoke test.")


if __name__ == "__main__":
    main()
