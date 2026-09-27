#!/usr/bin/env python3
"""Agent helper: evaluate patches against locally generated SWE-smith tasks."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import shlex
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parents[3] / "results" / "agent-evaluation-runs"
COMPLETED_STATUSES = {
    "completed",
    "timeout",
    "patch_apply_error",
    "empty_prediction",
}
REQUIRED_TASK_FIELDS = {
    "_agent_instance_id",
    "instance_id",
    "repo",
    "image_name",
    "patch",
    "FAIL_TO_PASS",
    "PASS_TO_PASS",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument(
        "--predictions",
        required=True,
        help="SWE-agent preds.json/JSONL, or the literal 'gold'.",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--memory-limit", default="10g")
    parser.add_argument("--platform", default="linux/x86_64")
    parser.add_argument("--timeout-seconds", type=int)
    parser.add_argument("--f2p-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="Fail if predictions do not cover every selected private task.",
    )
    parser.add_argument(
        "--agent-instance-id",
        action="append",
        dest="agent_instance_ids",
        help="Evaluate only this opaque Agent id (repeatable).",
    )
    return parser.parse_args()


def validate_run_id(run_id: str) -> None:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError(
            "run-id must contain only letters, numbers, dot, underscore, and hyphen"
        )


def safe_repo_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or "\x00" in value or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe repository path: {value!r}")
    normalized = str(path)
    if normalized in {".", ""}:
        raise ValueError(f"Unsafe repository path: {value!r}")
    return normalized


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, value: Any, *, private: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if private:
        os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def load_json_or_jsonl(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def load_private_tasks(path: Path) -> dict[str, dict[str, Any]]:
    raw = load_json_or_jsonl(path)
    if not isinstance(raw, list):
        raise ValueError("Private task dataset must be a JSON array or JSONL records")
    tasks: dict[str, dict[str, Any]] = {}
    for index, task in enumerate(raw, 1):
        if not isinstance(task, dict):
            raise ValueError(f"Private task {index} is not an object")
        missing = sorted(REQUIRED_TASK_FIELDS - task.keys())
        if missing:
            raise ValueError(f"Private task {index} is missing fields: {missing}")
        agent_id = task["_agent_instance_id"]
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError(f"Private task {index} has an invalid _agent_instance_id")
        if agent_id in tasks:
            raise ValueError(f"Duplicate _agent_instance_id: {agent_id}")
        tasks[agent_id] = task
    return tasks


def normalize_predictions(raw: Any) -> dict[str, dict[str, Any]]:
    """Normalize SWE-agent preds.json (mapping) or JSONL (list)."""
    if isinstance(raw, dict):
        items = []
        for key, value in raw.items():
            if not isinstance(value, dict):
                raise ValueError(f"Prediction for {key!r} is not an object")
            row = dict(value)
            row.setdefault("instance_id", key)
            if row["instance_id"] != key:
                raise ValueError(
                    f"Prediction key/id mismatch: {key!r} != {row['instance_id']!r}"
                )
            items.append(row)
    elif isinstance(raw, list):
        items = raw
    else:
        raise ValueError("Predictions must be a JSON object or list")

    predictions: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(items, 1):
        if not isinstance(row, dict):
            raise ValueError(f"Prediction {index} is not an object")
        instance_id = row.get("instance_id")
        if not isinstance(instance_id, str) or not instance_id:
            raise ValueError(f"Prediction {index} has no instance_id")
        if instance_id in predictions:
            raise ValueError(f"Duplicate prediction: {instance_id}")
        if "model_patch" not in row:
            raise ValueError(f"Prediction {instance_id} has no model_patch field")
        model_patch = row["model_patch"]
        if model_patch is not None and not isinstance(model_patch, str):
            raise ValueError(f"Prediction {instance_id} model_patch must be text or null")
        predictions[instance_id] = {
            "instance_id": instance_id,
            "model_patch": model_patch,
            "model_name_or_path": row.get("model_name_or_path", "unknown"),
        }
    return predictions


def gold_predictions(tasks: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        agent_id: {
            "instance_id": agent_id,
            "model_patch": task["patch"],
            "model_name_or_path": "gold-reverse-mutation",
        }
        for agent_id, task in tasks.items()
    }


def patch_paths(patch: str) -> list[str]:
    from unidiff import PatchSet

    paths = [safe_repo_path(item.path) for item in PatchSet(patch)]
    if patch.strip() and not paths:
        raise ValueError("Prediction patch contains no parseable file paths")
    return paths


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
    reverse: bool,
    user: str | None,
) -> str:
    from swesmith.constants import GIT_APPLY_CMDS

    failures: list[str] = []
    for base_command in GIT_APPLY_CMDS:
        reverse_flag = " --reverse" if reverse else ""
        command = f"{base_command}{reverse_flag} {shlex.quote(patch_path)}"
        result = container.exec_run(command, workdir="/testbed", user=user)
        output = _decode_exec_output(result)
        if result.exit_code == 0:
            return output
        failures.append(f"{command}: {output}")
    raise RuntimeError("Failed to apply patch:\n" + "\n".join(failures))


def filter_tasks(
    tasks: dict[str, dict[str, Any]], requested_ids: list[str] | None
) -> dict[str, dict[str, Any]]:
    if requested_ids is None:
        return tasks
    missing = sorted(set(requested_ids) - tasks.keys())
    if missing:
        raise ValueError(f"Unknown --agent-instance-id values: {missing}")
    requested = set(requested_ids)
    return {key: value for key, value in tasks.items() if key in requested}


def summarize_reports(
    *,
    reports: Iterable[dict[str, Any]],
    selected: int,
    missing_predictions: list[str],
    unexpected_predictions: list[str],
) -> dict[str, Any]:
    reports = list(reports)
    status_counts: dict[str, int] = {}
    for report in reports:
        status = str(report.get("status", "unknown"))
        status_counts[status] = status_counts.get(status, 0) + 1
    resolved = sum(bool(report.get("resolved", False)) for report in reports)
    return {
        "selected_tasks": selected,
        "evaluated": len(reports),
        "resolved": resolved,
        "unresolved": len(reports) - resolved,
        "resolution_rate": resolved / len(reports) if reports else 0.0,
        "status_counts": dict(sorted(status_counts.items())),
        "missing_predictions": missing_predictions,
        "unexpected_predictions": unexpected_predictions,
    }


def _existing_completed_report(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(value, dict) and value.get("status") in COMPLETED_STATUSES:
        return value
    return None


def evaluate_one(
    task: dict[str, Any],
    prediction: dict[str, Any],
    *,
    output_dir: Path,
    run_id: str,
    gold: bool,
    f2p_only: bool,
    timeout_seconds: int | None,
    platform: str,
    memory_limit: str,
) -> dict[str, Any]:
    import docker
    from swebench.harness.constants import DOCKER_USER, TESTS_TIMEOUT
    from swebench.harness.docker_utils import copy_to_container, exec_run_with_timeout
    from swesmith.constants import TEST_OUTPUT_END, TEST_OUTPUT_START
    from swesmith.harness.grading import get_eval_report
    from swesmith.profiles import registry

    agent_id = task["_agent_instance_id"]
    source_id = task["instance_id"]
    instance_dir = output_dir / agent_id
    instance_dir.mkdir(parents=True, exist_ok=True)
    report_path = instance_dir / "report.json"
    test_output_path = instance_dir / "test_output.txt"
    bug_patch_path = instance_dir / "bug.patch"
    model_patch_path = instance_dir / "model.patch"
    eval_script_path = instance_dir / "eval.sh"
    for private_path in (instance_dir,):
        try:
            os.chmod(private_path, 0o700)
        except OSError:
            pass

    model_patch = prediction.get("model_patch")
    base_report: dict[str, Any] = {
        "status": "error",
        "resolved": False,
        "patch_exists": bool(model_patch and str(model_patch).strip()),
        "agent_instance_id": agent_id,
        "source_instance_id": source_id,
        "model_name_or_path": prediction.get("model_name_or_path", "unknown"),
        "gold": gold,
    }
    if not model_patch or not str(model_patch).strip():
        base_report["status"] = "empty_prediction"
        atomic_write_json(report_path, base_report)
        return base_report

    started = time.monotonic()
    client = docker.from_env()
    container = None
    try:
        client.images.get(task["image_name"])
        patch_paths(task["patch"])
        if not gold:
            patch_paths(model_patch)

        container_name = (
            "swesmith-local-eval-"
            + sha256_text(f"{run_id}\0{agent_id}\0{threading.get_ident()}")[:20]
        )
        container = client.containers.create(
            image=task["image_name"],
            name=container_name,
            user=DOCKER_USER,
            detach=True,
            command="tail -f /dev/null",
            platform=platform,
            mem_limit=memory_limit,
        )
        container.start()

        bug_patch_path.write_text(task["patch"], encoding="utf-8")
        os.chmod(bug_patch_path, 0o600)
        copy_to_container(container, bug_patch_path, Path("/tmp/bug.patch"))
        apply_patch_in_container(
            container, "/tmp/bug.patch", reverse=False, user=DOCKER_USER
        )

        # Commit only inside this private evaluator container. It establishes a clean
        # buggy baseline, so restoring test files cannot accidentally erase the bug.
        exec_checked(
            container,
            ["git", "config", "user.email", "local-eval@invalid.local"],
            user=DOCKER_USER,
        )
        exec_checked(
            container,
            ["git", "config", "user.name", "SWE-smith Lab"],
            user=DOCKER_USER,
        )
        exec_checked(container, ["git", "add", "-A"], user=DOCKER_USER)
        exec_checked(
            container,
            ["git", "commit", "-q", "-m", "Private buggy baseline"],
            user=DOCKER_USER,
            environment={
                "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
                "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00",
            },
        )

        model_patch_path.write_text(model_patch, encoding="utf-8")
        os.chmod(model_patch_path, 0o600)
        copy_to_container(container, model_patch_path, Path("/tmp/model.patch"))
        try:
            apply_patch_in_container(
                container, "/tmp/model.patch", reverse=gold, user=DOCKER_USER
            )
        except Exception as exc:
            base_report.update(
                {
                    "status": "patch_apply_error",
                    "error": str(exc),
                    "seconds": round(time.monotonic() - started, 3),
                }
            )
            atomic_write_json(report_path, base_report)
            return base_report

        rp = registry.get_from_inst(task)
        f2p_files, p2p_files = rp.get_test_files(task)
        for test_file in sorted(
            {safe_repo_path(path) for path in f2p_files + p2p_files}
        ):
            exec_checked(
                container,
                ["git", "checkout", "HEAD", "--", test_file],
                user=DOCKER_USER,
            )

        test_command, _ = rp.get_test_cmd(copy.deepcopy(task), f2p_only=f2p_only)
        eval_script_path.write_text(
            "\n".join(
                [
                    "#!/bin/bash",
                    "set -uxo pipefail",
                    "cd /testbed",
                    f": '{TEST_OUTPUT_START}'",
                    test_command,
                    f": '{TEST_OUTPUT_END}'",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        os.chmod(eval_script_path, 0o700)
        copy_to_container(container, eval_script_path, Path("/eval.sh"))
        timeout = timeout_seconds or rp.timeout
        test_output, timed_out, runtime = exec_run_with_timeout(
            container, "/bin/bash /eval.sh", timeout=timeout
        )
        if timed_out:
            test_output += f"\n\n{TESTS_TIMEOUT}: {timeout} seconds exceeded"
        test_output_path.write_text(test_output, encoding="utf-8", errors="replace")
        os.chmod(test_output_path, 0o600)

        grading_prediction = {
            "instance_id": source_id,
            "model_patch": model_patch,
            "model_name_or_path": prediction.get("model_name_or_path", "unknown"),
        }
        grading_report = get_eval_report(
            grading_prediction,
            copy.deepcopy(task),
            str(test_output_path),
            f2p_only=f2p_only,
        )
        base_report.update(grading_report)
        base_report.update(
            {
                "status": "timeout" if timed_out else "completed",
                "timed_out": timed_out,
                "test_runtime_seconds": round(runtime, 3),
                "timeout_seconds": timeout,
                "seconds": round(time.monotonic() - started, 3),
            }
        )
    except Exception as exc:
        base_report.update(
            {
                "status": "error",
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "seconds": round(time.monotonic() - started, 3),
            }
        )
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception as cleanup_error:
                base_report["cleanup_error"] = str(cleanup_error)
        try:
            client.close()
        except Exception:
            pass
        atomic_write_json(report_path, base_report)
    return base_report


def main() -> None:
    args = parse_args()
    validate_run_id(args.run_id)
    if args.workers < 1:
        raise ValueError("--workers must be positive")
    if args.timeout_seconds is not None and args.timeout_seconds < 1:
        raise ValueError("--timeout-seconds must be positive")

    all_tasks = load_private_tasks(args.dataset.resolve())
    tasks = filter_tasks(all_tasks, args.agent_instance_ids)
    is_gold = args.predictions == "gold"
    if is_gold:
        predictions = gold_predictions(tasks)
    else:
        predictions = normalize_predictions(
            load_json_or_jsonl(Path(args.predictions).resolve())
        )

    missing_predictions = sorted(tasks.keys() - predictions.keys())
    unexpected_predictions = sorted(predictions.keys() - all_tasks.keys())
    if args.require_all and missing_predictions:
        raise ValueError(
            f"Predictions are missing {len(missing_predictions)} selected tasks"
        )
    payload_ids = sorted(tasks.keys() & predictions.keys())
    if not payload_ids:
        raise ValueError("No predictions match the selected private tasks")

    output_dir = args.output_root.resolve() / args.run_id
    if output_dir.exists() and any(output_dir.iterdir()) and not args.resume:
        raise RuntimeError(
            f"Output directory already contains files; use --resume or a new run-id: {output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(output_dir, 0o700)
    except OSError:
        pass

    reports: list[dict[str, Any]] = []
    pending: list[str] = []
    for agent_id in payload_ids:
        report_path = output_dir / agent_id / "report.json"
        existing = _existing_completed_report(report_path) if args.resume else None
        if existing is not None:
            reports.append(existing)
        else:
            pending.append(agent_id)

    print(f"Private tasks selected: {len(tasks)}")
    print(f"Matching predictions: {len(payload_ids)}")
    print(f"Already completed: {len(reports)}")
    print(f"Pending evaluations: {len(pending)}")
    print(f"Workers: {args.workers}")
    print(f"Mode: {'gold (reverse mutation)' if is_gold else 'Agent model_patch'}")

    with ThreadPoolExecutor(max_workers=min(args.workers, len(pending) or 1)) as pool:
        futures = {
            pool.submit(
                evaluate_one,
                tasks[agent_id],
                predictions[agent_id],
                output_dir=output_dir,
                run_id=args.run_id,
                gold=is_gold,
                f2p_only=args.f2p_only,
                timeout_seconds=args.timeout_seconds,
                platform=args.platform,
                memory_limit=args.memory_limit,
            ): agent_id
            for agent_id in pending
        }
        for future in as_completed(futures):
            report = future.result()
            reports.append(report)
            mark = "RESOLVED" if report.get("resolved") else report.get("status")
            print(f"[{len(reports)}/{len(payload_ids)}] {futures[future]}: {mark}")

    summary = summarize_reports(
        reports=reports,
        selected=len(tasks),
        missing_predictions=missing_predictions,
        unexpected_predictions=unexpected_predictions,
    )
    summary.update(
        {
            "schema_version": 1,
            "run_id": args.run_id,
            "dataset": str(args.dataset.resolve()),
            "predictions": args.predictions,
            "gold": is_gold,
            "f2p_only": args.f2p_only,
            "workers": args.workers,
        }
    )
    atomic_write_json(output_dir / "report.json", summary)
    print(
        f"Resolved {summary['resolved']}/{summary['evaluated']} "
        f"({summary['resolution_rate']:.1%})"
    )
    print(f"Report: {output_dir / 'report.json'}")
    if summary["status_counts"].get("error", 0):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
