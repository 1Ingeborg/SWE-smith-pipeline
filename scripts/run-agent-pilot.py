#!/usr/bin/env python3
"""Run pinned SWE-agent on public local-pilot instances with cost guards."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
DEFAULT_SWEAGENT_ROOT = Path("/data/repos/SWE-agent")
DEFAULT_SWEAGENT_EXECUTABLE = Path("/data/venvs/sweagent/bin/sweagent")
DEFAULT_OUTPUT_ROOT = Path("/data/trajectories/agent-pilot-runs")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instances", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--sweagent-root", type=Path, default=DEFAULT_SWEAGENT_ROOT)
    parser.add_argument(
        "--sweagent-executable", type=Path, default=DEFAULT_SWEAGENT_EXECUTABLE
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--limit",
        type=int,
        help="Run only the first N validated public instances.",
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--mode",
        choices=("smoke", "model"),
        default="smoke",
        help="smoke uses SWE-agent's zero-cost instant model; model may call an API.",
    )
    parser.add_argument("--agent-config", type=Path)
    parser.add_argument("--model-name")
    parser.add_argument("--api-base")
    parser.add_argument("--api-key-env", default="DASHSCOPE_API_KEY")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--per-instance-call-limit", type=int, default=30)
    parser.add_argument("--per-instance-cost-limit", type=float, default=2.0)
    parser.add_argument("--total-cost-limit", type=float, default=12.0)
    parser.add_argument(
        "--allow-api-calls",
        action="store_true",
        help="Required in model mode. This is the explicit paid/external API gate.",
    )
    parser.add_argument(
        "--use-standalone-python",
        action="store_true",
        help="Let SWE-ReX build and cache a runtime image instead of using container Python.",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    if not RUN_ID_PATTERN.fullmatch(args.run_id):
        raise ValueError("run-id contains unsafe characters")
    if args.workers < 1:
        raise ValueError("--workers must be positive")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be positive")
    if args.per_instance_call_limit < 1:
        raise ValueError("--per-instance-call-limit must be positive")
    if args.per_instance_cost_limit < 0 or args.total_cost_limit < 0:
        raise ValueError("cost limits must be non-negative")
    if args.mode == "model":
        if not args.allow_api_calls:
            raise ValueError("model mode requires explicit --allow-api-calls")
        if not args.model_name:
            raise ValueError("model mode requires --model-name")
        if not args.api_base:
            raise ValueError("model mode requires --api-base")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", args.api_key_env):
            raise ValueError("--api-key-env is not a valid environment variable name")


def load_public_instances(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise ValueError("Public instances file is empty")
    expected = {
        "instance_id",
        "image_name",
        "problem_statement",
        "repo_name",
        "base_commit",
    }
    ids: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != expected:
            raise ValueError(
                "Public instances must contain exactly the five Agent-visible fields"
            )
        if row["instance_id"] in ids:
            raise ValueError(f"Duplicate public instance_id: {row['instance_id']}")
        ids.add(row["instance_id"])
    return rows


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid environment line {path}:{line_number}")
        name, value = line.split("=", 1)
        name = name.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"Invalid environment name {path}:{line_number}")
        values[name] = value.strip().strip('"').strip("'")
    return values


def build_command(
    args: argparse.Namespace,
    *,
    config_path: Path,
    output_dir: Path,
) -> list[str]:
    command = [
        str(args.sweagent_executable),
        "run-batch",
        "--config",
        str(config_path),
        "--instances.type",
        "file",
        "--instances.path",
        str(args.instances.resolve()),
        "--instances.shuffle=false",
        "--num_workers",
        str(args.workers),
        "--output_dir",
        str(output_dir),
        "--progress_bar=false",
    ]
    if not args.use_standalone_python:
        command.append("--instances.deployment.python_standalone_dir=")
    if args.mode == "smoke":
        command.extend(["--agent.model.name", "instant_empty_submit"])
    else:
        command.extend(
            [
                "--agent.model.name",
                args.model_name,
                "--agent.model.api_base",
                args.api_base,
                "--agent.model.api_key",
                f"${args.api_key_env}",
                "--agent.model.per_instance_call_limit",
                str(args.per_instance_call_limit),
                "--agent.model.per_instance_cost_limit",
                str(args.per_instance_cost_limit),
                "--agent.model.total_cost_limit",
                str(args.total_cost_limit),
            ]
        )
    return command


def summarize_run(
    output_dir: Path,
    *,
    expected_ids: set[str],
    process_return_code: int,
) -> dict[str, Any]:
    predictions_path = output_dir / "preds.json"
    if predictions_path.exists():
        raw_predictions = json.loads(predictions_path.read_text(encoding="utf-8"))
        predictions = raw_predictions if isinstance(raw_predictions, dict) else {}
    else:
        predictions = {}

    exit_statuses: dict[str, str] = {}
    api_calls = 0
    total_cost = 0.0
    trajectory_count = 0
    for path in output_dir.glob("*/*.traj"):
        try:
            trajectory = json.loads(path.read_text(encoding="utf-8"))
            info = trajectory.get("info", {})
            instance_id = path.parent.name
            exit_statuses[instance_id] = str(info.get("exit_status", "missing"))
            stats = info.get("model_stats", {})
            api_calls += int(stats.get("api_calls", 0) or 0)
            total_cost += float(stats.get("instance_cost", 0.0) or 0.0)
            trajectory_count += 1
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue

    prediction_ids = set(predictions)
    return {
        "schema_version": 1,
        "process_return_code": process_return_code,
        "expected_instances": len(expected_ids),
        "prediction_count": len(predictions),
        "nonempty_patches": sum(
            bool(value.get("model_patch", "").strip())
            for value in predictions.values()
            if isinstance(value, dict) and isinstance(value.get("model_patch", ""), str)
        ),
        "trajectory_count": trajectory_count,
        "covered_ids": sorted(expected_ids & prediction_ids),
        "missing_ids": sorted(expected_ids - prediction_ids),
        "unexpected_ids": sorted(prediction_ids - expected_ids),
        "exit_statuses": dict(sorted(exit_statuses.items())),
        "reported_api_calls": api_calls,
        "reported_cost": total_cost,
    }


def main() -> None:
    args = parse_args()
    validate_args(args)
    instances_path = args.instances.resolve()
    instances = load_public_instances(instances_path)
    if args.limit is not None:
        instances = instances[: args.limit]
        if not instances:
            raise ValueError("No public instances selected")
    expected_ids = {row["instance_id"] for row in instances}
    sweagent_root = args.sweagent_root.resolve()
    executable = args.sweagent_executable.resolve()
    if not executable.exists():
        raise FileNotFoundError(
            f"SWE-agent executable not found: {executable}; run bootstrap-swe-agent.sh"
        )

    if args.agent_config:
        config_path = args.agent_config.resolve()
    elif args.mode == "smoke":
        config_path = sweagent_root / "config" / "default_backticks.yaml"
    else:
        config_path = sweagent_root / "config" / "default.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"Agent config not found: {config_path}")

    output_dir = args.output_root.resolve() / args.run_id
    if output_dir.exists() and any(output_dir.iterdir()) and not args.resume:
        raise RuntimeError(
            f"Output directory already contains files; use --resume or a new run-id: {output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.limit is not None:
        instances_path = output_dir / "selected-public-instances.jsonl"
        instances_path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in instances),
            encoding="utf-8",
        )
        args.instances = instances_path

    environment = os.environ.copy()
    if args.env_file:
        environment.update(load_env_file(args.env_file.resolve()))
    if args.mode == "model" and not environment.get(args.api_key_env):
        raise RuntimeError(
            f"Required API key environment variable is not set: {args.api_key_env}"
        )
    environment["SWE_AGENT_CONFIG_ROOT"] = str(sweagent_root)
    # The server cannot reliably reach raw.githubusercontent.com. LiteLLM ships a
    # local cost map, so avoid a pointless ~30 second network timeout at startup.
    environment.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

    command = build_command(args, config_path=config_path, output_dir=output_dir)
    command_record = {
        "schema_version": 1,
        "mode": args.mode,
        "sweagent_root": str(sweagent_root),
        "sweagent_executable": str(executable),
        "agent_config": str(config_path),
        "instances": str(instances_path),
        "workers": args.workers,
        "model_name": "instant_empty_submit" if args.mode == "smoke" else args.model_name,
        "api_base": None if args.mode == "smoke" else args.api_base,
        "api_key_env": None if args.mode == "smoke" else args.api_key_env,
        "per_instance_call_limit": args.per_instance_call_limit,
        "per_instance_cost_limit": args.per_instance_cost_limit,
        "total_cost_limit": args.total_cost_limit,
        "use_standalone_python": args.use_standalone_python,
    }
    (output_dir / "lab-run-config.yaml").write_text(
        yaml.safe_dump(command_record, sort_keys=False), encoding="utf-8"
    )

    print(f"Mode: {args.mode}")
    print(f"Public instances: {instances_path} ({len(instances)} tasks)")
    print(f"SWE-agent config: {config_path}")
    print(f"Output: {output_dir}")
    if args.mode == "model":
        print(
            f"External API enabled explicitly; call limit={args.per_instance_call_limit}/task, "
            f"workers={args.workers}"
        )
    else:
        print("Zero-cost smoke mode; no external model API will be called.")

    result = subprocess.run(command, cwd=sweagent_root, env=environment, check=False)
    summary = summarize_run(
        output_dir,
        expected_ids=expected_ids,
        process_return_code=result.returncode,
    )
    (output_dir / "lab-run-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"Predictions: {summary['prediction_count']}/{summary['expected_instances']}; "
        f"trajectories: {summary['trajectory_count']}; "
        f"reported API calls: {summary['reported_api_calls']}"
    )
    print(f"Summary: {output_dir / 'lab-run-summary.json'}")
    if result.returncode != 0 or summary["missing_ids"]:
        raise SystemExit(result.returncode or 2)


if __name__ == "__main__":
    main()
