#!/usr/bin/env python3
"""Run a SWE-smith Agent experiment inside results/<run-id>."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import yaml


LAB_ROOT = Path(__file__).resolve().parents[3]
RESULTS_ROOT = LAB_ROOT / "results"
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
def data_root() -> Path:
    value = os.environ.get("SWE_LAB_DATA_ROOT")
    if not value and (LAB_ROOT / ".env").is_file():
        for line in (LAB_ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("SWE_LAB_DATA_ROOT="):
                value = line.split("=", 1)[1].strip().strip("\"'")
                break
    path = Path(value or LAB_ROOT / ".local").expanduser()
    return (path if path.is_absolute() else LAB_ROOT / path).resolve()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Rollout experiment YAML.")
    parser.add_argument("--experiment", help="Select exactly one framework from --config.")
    parser.add_argument("--stage", choices=("prepare", "gold", "agent", "rollout", "eval", "sft"))
    parser.add_argument("--allow-api-calls", action="store_true")
    parser.add_argument("--with-gold", action="store_true", help="Include the optional gold check in the full Agent flow.")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    args.stage = args.stage or "agent"
    if args.stage in {"agent", "rollout", "eval", "sft"} and not args.experiment:
        parser.error("--experiment is required for agent/rollout/eval/sft")
    if args.stage in {"prepare", "gold"} and args.experiment:
        parser.error("--experiment is not used for prepare/gold")
    if args.with_gold and args.stage != "agent":
        parser.error("--with-gold is only valid for the full agent stage")
    return args


def resolve_input(path: Path) -> Path:
    return (path if path.is_absolute() else LAB_ROOT / path).resolve()


def _mapping(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a YAML mapping")
    return value


def _keys(value: dict, allowed: set[str], name: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"Unknown {name} keys: {', '.join(sorted(unknown))}")


def _string(value: dict, key: str, name: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{name}.{key} must be a non-empty string")
    return item


def _positive_int(value: dict, key: str, name: str) -> int:
    item = value.get(key)
    if type(item) is not int or item < 1:
        raise ValueError(f"{name}.{key} must be a positive integer")
    return item


def _nonnegative_number(value: dict, key: str, name: str) -> float:
    item = value.get(key)
    if type(item) not in (int, float) or item < 0:
        raise ValueError(f"{name}.{key} must be a non-negative number")
    return float(item)


def _evaluation(value: object, name: str) -> dict:
    settings = _mapping(value, name)
    _keys(settings, {"workers", "memory_limit", "timeout_seconds"}, name)
    _positive_int(settings, "workers", name)
    _string(settings, "memory_limit", name)
    _positive_int(settings, "timeout_seconds", name)
    return settings


def load_rollout_config(path: Path) -> dict:
    config = _mapping(yaml.safe_load(require_file(path, "Rollout config").read_text(encoding="utf-8")), "rollout config")
    _keys(config, {"schema_version", "run_dir", "prepare", "gold", "experiments"}, "rollout config")
    if type(config.get("schema_version")) is not int or config["schema_version"] != 1:
        raise ValueError("rollout config schema_version must be 1")
    _string(config, "run_dir", "rollout config")
    prepare = _mapping(config.get("prepare"), "prepare")
    _keys(prepare, {"template"}, "prepare")
    _string(prepare, "template", "prepare")
    if "gold" in config:
        gold = _mapping(config["gold"], "gold")
        _keys(gold, {"run_id", "evaluation"}, "gold")
        if not SAFE_ID.fullmatch(_string(gold, "run_id", "gold")):
            raise ValueError("gold.run_id contains unsafe characters")
        _evaluation(gold.get("evaluation"), "gold.evaluation")
    experiments = _mapping(config.get("experiments"), "experiments")
    if not experiments:
        raise ValueError("experiments must not be empty")
    for experiment_id, raw in experiments.items():
        if experiment_id not in {"swe-agent", "mini-swe-agent"}:
            raise ValueError(f"Unsupported experiment framework: {experiment_id!r}")
        item = _mapping(raw, f"experiments.{experiment_id}")
        common = {"rollout_id", "native_config", "model_name", "api_base", "api_key_env",
                  "temperature", "workers", "evaluation"}
        rollout_id = _string(item, "rollout_id", experiment_id)
        if not SAFE_ID.fullmatch(rollout_id):
            raise ValueError(f"Invalid rollout_id for {experiment_id}: {rollout_id!r}")
        if experiment_id == "swe-agent":
            _keys(item, common | {"per_instance_call_limit", "per_instance_cost_limit",
                                  "total_cost_limit", "max_instances", "completion_kwargs"}, experiment_id)
            _positive_int(item, "per_instance_call_limit", experiment_id)
            _nonnegative_number(item, "per_instance_cost_limit", experiment_id)
            _nonnegative_number(item, "total_cost_limit", experiment_id)
            if "max_instances" in item:
                _positive_int(item, "max_instances", experiment_id)
            if "completion_kwargs" in item:
                _mapping(item["completion_kwargs"], f"{experiment_id}.completion_kwargs")
        else:
            _keys(item, common | {"step_limit", "cost_limit", "max_output_tokens"}, experiment_id)
            _positive_int(item, "step_limit", experiment_id)
            _nonnegative_number(item, "cost_limit", experiment_id)
            _positive_int(item, "max_output_tokens", experiment_id)
        for key in ("native_config", "model_name", "api_base", "api_key_env"):
            _string(item, key, experiment_id)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item["api_key_env"]):
            raise ValueError(f"Invalid api_key_env for {experiment_id}")
        if _nonnegative_number(item, "temperature", experiment_id) > 2:
            raise ValueError(f"{experiment_id}.temperature must not exceed 2")
        _positive_int(item, "workers", experiment_id)
        _evaluation(item.get("evaluation"), f"{experiment_id}.evaluation")
    return config


def _sensitive_key(key: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return any(word in normalized for word in ("apikey", "secret", "password", "credential")) or normalized.endswith("token")


def _redact(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "<redacted>" if _sensitive_key(key)
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def swe_model_overrides(item: dict) -> dict:
    model = {
        "name": item["model_name"], "api_base": item["api_base"],
        "temperature": item["temperature"],
        "per_instance_call_limit": item["per_instance_call_limit"],
        "per_instance_cost_limit": item["per_instance_cost_limit"],
        "total_cost_limit": item["total_cost_limit"],
    }
    if "completion_kwargs" in item:
        model["completion_kwargs"] = copy.deepcopy(item["completion_kwargs"])
    return model


def effective_native_config(framework: str, item: dict, path: Path) -> dict:
    native = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), "framework-native config")
    effective = copy.deepcopy(native)
    if framework == "swe-agent":
        model = effective.setdefault("agent", {}).setdefault("model", {})
        model.update(swe_model_overrides(item))
    else:
        effective.setdefault("agent", {}).update({
            "step_limit": item["step_limit"], "cost_limit": item["cost_limit"],
        })
        model = effective.setdefault("model", {})
        model["model_name"] = item["model_name"]
        model.setdefault("model_kwargs", {}).update({
            "api_base": item["api_base"], "temperature": item["temperature"],
            "max_tokens": item["max_output_tokens"],
        })
    return _redact(effective)


def lab_bundle_path(value: str) -> Path:
    relative = Path(value.removeprefix("lab:"))
    if not value.startswith("lab:") or relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError(f"Invalid repository-relative Agent bundle: {value}")
    bundle = (LAB_ROOT / relative).resolve()
    if LAB_ROOT.resolve() not in bundle.parents:
        raise ValueError(f"Agent bundle escapes repository: {value}")
    require_file(bundle / "config.yaml", "Repository-owned Agent bundle config")
    return bundle


def lab_bundle_entries(native_path: Path) -> list[tuple[dict, str, Path]]:
    native = _mapping(yaml.safe_load(native_path.read_text(encoding="utf-8")), "SWE-agent native config")
    bundles = native.get("agent", {}).get("tools", {}).get("bundles", [])
    entries = []
    for bundle in bundles:
        value = bundle.get("path")
        if isinstance(value, str) and value.startswith("lab:"):
            entries.append((bundle, value, lab_bundle_path(value)))
    return entries


def lab_bundle_digests(native_path: Path) -> dict[str, str]:
    digests = {}
    for _, value, bundle in lab_bundle_entries(native_path):
        hasher = hashlib.sha256()
        for path in sorted(bundle.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"Agent bundle contains a symlink: {path}")
            if path.is_file():
                hasher.update(path.relative_to(bundle).as_posix().encode("utf-8"))
                hasher.update(b"\0")
                hasher.update(path.read_bytes())
                hasher.update(b"\0")
        digests[value] = hasher.hexdigest()
    return digests


def resolved_sweagent_config(native_path: Path, destination: Path, *, dry_run: bool,
                             model_overrides: dict | None = None) -> Path:
    native = _mapping(yaml.safe_load(native_path.read_text(encoding="utf-8")), "SWE-agent native config")
    changed = False
    if model_overrides is not None:
        native.setdefault("agent", {}).setdefault("model", {}).update(copy.deepcopy(model_overrides))
        changed = True
    for bundle in native.get("agent", {}).get("tools", {}).get("bundles", []):
        value = bundle.get("path")
        if isinstance(value, str) and value.startswith("lab:"):
            bundle["path"] = str(lab_bundle_path(value))
            changed = True
    if not changed:
        return native_path
    rendered = yaml.safe_dump(native, sort_keys=False, allow_unicode=True)
    if destination.is_file() and destination.read_text(encoding="utf-8") != rendered:
        raise RuntimeError(f"Resolved SWE-agent config changed; use a new rollout_id: {destination}")
    if not dry_run and not destination.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(rendered, encoding="utf-8")
    return destination


def configured_args(cli: argparse.Namespace) -> tuple[argparse.Namespace, Path, Path, str]:
    config = load_rollout_config(resolve_input(cli.config))
    run_dir = resolve_run_dir(Path(config["run_dir"]))
    values = argparse.Namespace(**vars(cli))
    values.run_dir = run_dir
    values.prepare_config = Path(config["prepare"]["template"])
    values.workers = 1
    values.eval_workers = 2
    values.eval_memory_limit = "4g"
    values.eval_timeout_seconds = 120
    values.sweagent_root = None
    values.sweagent_executable = None
    values.mini_executable = None
    values.mini_step_limit = None
    values.mini_cost_limit = None
    values.mini_max_tokens = None
    values.max_instances = None
    values.temperature = None
    values.swe_model_overrides = None
    snapshot = {"schema_version": 1, "run_dir": config["run_dir"]}
    if cli.stage == "prepare":
        template = require_file(resolve_input(values.prepare_config), "Task preparation template")
        values.framework = "shared"
        values.rollout_id = "prepare"
        snapshot["prepare"] = config["prepare"]
        snapshot["template_digest"] = hashlib.sha256(template.read_bytes()).hexdigest()
        snapshot_name = "prepare"
    elif cli.stage == "gold":
        if "gold" not in config:
            raise ValueError("Gold check was requested but this rollout config has no gold section")
        values.framework = "gold"
        values.rollout_id = config["gold"]["run_id"]
        settings = config["gold"]["evaluation"]
        values.eval_workers = settings["workers"]
        values.eval_memory_limit = settings["memory_limit"]
        values.eval_timeout_seconds = settings["timeout_seconds"]
        snapshot["gold"] = config["gold"]
        snapshot_name = "gold"
    else:
        if cli.experiment not in config["experiments"]:
            raise ValueError(f"Unknown experiment: {cli.experiment}")
        item = config["experiments"][cli.experiment]
        native = require_file(resolve_input(Path(item["native_config"])), "Framework-native config")
        values.framework = cli.experiment
        values.rollout_id = item["rollout_id"]
        values.model_name = item["model_name"]
        values.api_base = item["api_base"]
        values.api_key_env = item["api_key_env"]
        values.temperature = item["temperature"]
        values.workers = item["workers"]
        values.agent_config = Path(item["native_config"])
        values.mini_config = Path(item["native_config"])
        settings = item["evaluation"]
        values.eval_workers = settings["workers"]
        values.eval_memory_limit = settings["memory_limit"]
        values.eval_timeout_seconds = settings["timeout_seconds"]
        if values.framework == "swe-agent":
            values.per_instance_call_limit = item["per_instance_call_limit"]
            values.per_instance_cost_limit = item["per_instance_cost_limit"]
            values.total_cost_limit = item["total_cost_limit"]
            values.max_instances = item.get("max_instances")
            values.swe_model_overrides = swe_model_overrides(item)
        else:
            values.mini_step_limit = item["step_limit"]
            values.mini_cost_limit = item["cost_limit"]
            values.mini_max_tokens = item["max_output_tokens"]
        snapshot["experiment_id"] = cli.experiment
        snapshot["experiment"] = item
        snapshot["native_config_digest"] = hashlib.sha256(native.read_bytes()).hexdigest()
        if values.framework == "swe-agent":
            snapshot["lab_bundle_digests"] = lab_bundle_digests(native)
        snapshot["effective_framework_config"] = effective_native_config(values.framework, item, native)
        snapshot_name = f"experiment-{values.framework}-{values.rollout_id}"
    snapshot_path = run_dir / "meta" / "rollout-configs" / f"{snapshot_name}.yaml"
    snapshot_text = yaml.safe_dump(snapshot, sort_keys=True, allow_unicode=True)
    return values, run_dir, snapshot_path, snapshot_text


def check_snapshot(path: Path, content: str) -> None:
    if path.is_file() and path.read_text(encoding="utf-8") != content:
        raise RuntimeError(f"Saved rollout config changed; use a new rollout_id: {path}")


def save_snapshot(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def resolve_run_dir(path: Path) -> Path:
    if RESULTS_ROOT.is_symlink():
        raise RuntimeError(f"results must be a real directory, not a symlink: {RESULTS_ROOT}")
    root = RESULTS_ROOT.resolve()
    candidate = resolve_input(path)
    if candidate.parent != root or not SAFE_ID.fullmatch(candidate.name):
        raise ValueError(f"--run-dir must be results/<run-id> inside {root}")
    if not candidate.is_dir():
        raise FileNotFoundError(f"Task-generation run directory does not exist: {candidate}")
    manifest_path = candidate / "meta" / "manifest.json"
    if not manifest_path.is_file():
        manifest_path = candidate / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Task-generation manifest is missing: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("run_id") != candidate.name:
        raise ValueError(f"Task-generation manifest run_id does not match {candidate.name}")
    return candidate


def require_file(path: Path, description: str) -> Path:
    if not path.is_file():
        raise FileNotFoundError(f"{description} is missing: {path}")
    return path


def output_paths(run_dir: Path, framework: str, rollout_id: str) -> tuple[Path, Path]:
    if not SAFE_ID.fullmatch(rollout_id):
        raise ValueError("--rollout-id contains unsafe characters")
    return (
        run_dir / "rollouts" / framework / rollout_id,
        run_dir / "evaluations" / framework / rollout_id,
    )


def preparation_paths(run_dir: Path) -> tuple[Path, Path, Path]:
    # prepare.py includes its run-id in Docker image tags. Keep it unique per
    # pipeline run so separate datasets cannot reuse each other's task images.
    prepared = run_dir / "task-prep" / run_dir.name
    return prepared, prepared / "public" / "instances.jsonl", prepared / "private" / "selected.jsonl"


def preparation_complete(run_dir: Path, *, verify_images: bool) -> bool:
    prepared, instances, selected = preparation_paths(run_dir)
    summary_path = prepared / "summary.json"
    if not all(path.is_file() for path in (instances, selected, summary_path)):
        return False
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        public_rows = [json.loads(line) for line in instances.read_text(encoding="utf-8").splitlines() if line.strip()]
        private_rows = [json.loads(line) for line in selected.read_text(encoding="utf-8").splitlines() if line.strip()]
        public_ids = {row["instance_id"] for row in public_rows}
        private_ids = {row["_agent_instance_id"] for row in private_rows}
        image_names = [row["image_name"] for row in public_rows]
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False
    if not summary.get("image_preparation_complete") or not public_rows:
        return False
    if len(public_rows) != len(private_rows) or len(public_ids) != len(public_rows) or public_ids != private_ids:
        return False
    if any(not isinstance(name, str) or not name for name in image_names):
        return False
    if not verify_images:
        return True
    import docker

    client = docker.from_env()
    try:
        for name in image_names:
            try:
                client.images.get(name)
            except docker.errors.ImageNotFound:
                return False
    finally:
        client.close()
    return True


@contextmanager
def preparation_lock(run_dir: Path):
    import fcntl

    lock_path = run_dir / "task-prep" / ".prepare.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        print(f"Waiting for task-preparation lock: {lock_path}", flush=True)
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def gold_lock(run_dir: Path):
    import fcntl

    lock_path = run_dir / "evaluations" / "gold" / ".gold.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def prepare_config(run_dir: Path, template_path: Path, *, dry_run: bool) -> Path:
    accepted = require_file(run_dir / "issuegen" / "accepted.jsonl", "Accepted issue dataset")
    if not accepted.read_text(encoding="utf-8").strip():
        raise ValueError(f"Accepted issue dataset is empty: {accepted}")
    template = yaml.safe_load(require_file(template_path, "Task preparation template").read_text(encoding="utf-8"))
    if not isinstance(template, dict):
        raise ValueError(f"Task preparation template must be a YAML mapping: {template_path}")
    audit = run_dir / "issuegen" / "audit.jsonl"
    template["source"] = {"dataset": str(accepted), "audit": str(audit) if audit.is_file() else None}
    template["run_root"] = str(run_dir / "task-prep")
    rendered = yaml.safe_dump(template, sort_keys=False, allow_unicode=True)
    destination = run_dir / "task-prep" / "config.yaml"
    if destination.is_file() and destination.read_text(encoding="utf-8") != rendered:
        raise RuntimeError(f"Task preparation config changed; refusing to overwrite: {destination}")
    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.is_file():
            destination.write_text(rendered, encoding="utf-8")
    return destination


def api_environment(key_name: str) -> dict[str, str]:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_name):
        raise ValueError("--api-key-env must be an environment variable name")
    environment = os.environ.copy()
    env_file = LAB_ROOT / ".env"
    if env_file.is_file() and key_name not in environment:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(key_name + "="):
                environment[key_name] = line.split("=", 1)[1].strip().strip("\"'")
                break
    if not environment.get(key_name):
        raise RuntimeError(f"Required API key is not set: {key_name}")
    environment.pop("MSWEA_GLOBAL_COST_LIMIT", None)
    environment.pop("MSWEA_GLOBAL_CALL_LIMIT", None)
    return environment


def build_command(args: argparse.Namespace, run_dir: Path) -> tuple[list[str], dict[str, str] | None]:
    rollout, evaluation = output_paths(run_dir, args.framework, args.rollout_id)
    _, instances, selected = preparation_paths(run_dir)
    root = data_root()
    python = sys.executable
    if args.stage == "prepare":
        config = prepare_config(run_dir, resolve_input(args.prepare_config), dry_run=args.dry_run)
        command = [python, str(LAB_ROOT / "src/swesmith_lab/agent/prepare.py"),
                   "--config", str(config), "--run-id", run_dir.name]
        if args.resume:
            command.append("--resume")
        if args.dry_run:
            command.append("--dry-run")
        return command, None

    if args.stage == "gold":
        require_file(selected, "Prepared private selection")
        destination = run_dir / "evaluations" / "gold" / args.rollout_id
        if destination.exists() and any(destination.iterdir()) and not args.resume:
            raise RuntimeError(f"Gold evaluation exists; use --resume: {destination}")
        command = [python, str(LAB_ROOT / "src/swesmith_lab/agent/evaluate.py"),
                   "--dataset", str(selected), "--predictions", "gold",
                   "--run-id", args.rollout_id, "--output-root", str(destination.parent),
                   "--workers", str(args.eval_workers), "--memory-limit", args.eval_memory_limit,
                   "--timeout-seconds", str(args.eval_timeout_seconds)]
        if args.resume:
            command.append("--resume")
        return command, None

    require_file(instances, "Prepared public instances")
    require_file(selected, "Prepared private selection")
    if not instances.read_text(encoding="utf-8").strip():
        raise ValueError(f"Prepared public instances are empty: {instances}")
    summary = require_file(run_dir / "task-prep" / run_dir.name / "summary.json", "Task preparation summary")
    if not json.loads(summary.read_text(encoding="utf-8")).get("image_preparation_complete"):
        raise RuntimeError("Task preparation is incomplete; resume the prepare stage")

    if args.stage == "eval":
        predictions = require_file(rollout / "preds.json", "Agent predictions")
        if evaluation.exists() and any(evaluation.iterdir()) and not args.resume:
            raise RuntimeError(f"Evaluation exists; use --resume or another rollout-id: {evaluation}")
        command = [python, str(LAB_ROOT / "src/swesmith_lab/agent/evaluate.py"),
                   "--dataset", str(selected), "--predictions", str(predictions),
                   "--run-id", args.rollout_id, "--output-root", str(evaluation.parent),
                   "--workers", str(args.eval_workers), "--memory-limit", args.eval_memory_limit,
                   "--timeout-seconds", str(args.eval_timeout_seconds)]
        if args.resume:
            command.append("--resume")
        return command, None

    if args.stage == "sft":
        command = [python, str(LAB_ROOT / "src/swesmith_lab/agent/sft.py"),
                   "--framework", args.framework, "--rollout-dir", str(rollout),
                   "--eval-dir", str(evaluation),
                   "--output-dir", str(run_dir / "sft" / args.framework / args.rollout_id),
                   "--instances", str(instances), "--model", args.model_name]
        if args.framework == "mini-swe-agent":
            command.extend(["--native-config", str(resolve_input(args.mini_config))])
        if getattr(args, "max_instances", None) is not None:
            command.extend(["--limit", str(args.max_instances)])
        return command, None

    if rollout.exists() and any(rollout.iterdir()) and not args.resume:
        raise RuntimeError(f"Rollout exists; use --resume or another rollout-id: {rollout}")
    if not args.allow_api_calls and not args.dry_run:
        raise RuntimeError("Agent stage requires explicit --allow-api-calls")
    if args.framework == "swe-agent":
        sweagent_root = args.sweagent_root or LAB_ROOT / "vendor" / "SWE-agent"
        sweagent_bin = args.sweagent_executable or root / "venvs" / "sweagent" / "bin" / "sweagent"
        native_config = resolved_sweagent_config(
            require_file(resolve_input(args.agent_config), "SWE-agent native config"),
            run_dir / "meta" / "rollout-configs" / f"native-swe-agent-{args.rollout_id}.yaml",
            dry_run=args.dry_run,
            model_overrides=getattr(args, "swe_model_overrides", None),
        )
        command = [python, str(LAB_ROOT / "src/swesmith_lab/agent/run.py"),
                   "--instances", str(instances), "--run-id", args.rollout_id,
                   "--output-root", str(rollout.parent), "--sweagent-root", str(sweagent_root),
                   "--sweagent-executable", str(sweagent_bin), "--mode", "model",
                   "--allow-api-calls", "--agent-config", str(native_config),
                   "--model-name", args.model_name, "--api-base", args.api_base,
                   "--api-key-env", args.api_key_env, "--workers", str(args.workers),
                   "--per-instance-call-limit", str(args.per_instance_call_limit),
                   "--per-instance-cost-limit", str(args.per_instance_cost_limit),
                   "--total-cost-limit", str(args.total_cost_limit)]
        if getattr(args, "max_instances", None) is not None:
            command += ["--limit", str(args.max_instances)]
        if getattr(args, "temperature", None) is not None:
            command += ["--temperature", str(args.temperature)]
        if (LAB_ROOT / ".env").is_file():
            command += ["--env-file", str(LAB_ROOT / ".env")]
        if args.resume:
            command.append("--resume")
        return command, None

    mini_bin = args.mini_executable or root / "venvs" / "mini-sweagent" / "bin" / "mini-extra"
    require_file(mini_bin, "mini-swe-agent executable")
    mini_config = require_file(resolve_input(args.mini_config), "mini-swe-agent config")
    command = [str(mini_bin), "swebench", "--subset", str(instances.parent),
               "--split", "train", "--output", str(rollout), "--workers", str(args.workers),
               "--config", "swebench_backticks.yaml", "--config", str(mini_config),
               "--model", args.model_name]
    if getattr(args, "temperature", None) is not None:
        command += ["--config", f"model.model_kwargs.temperature={args.temperature}"]
        command += ["--config", f"model.model_kwargs.api_base={args.api_base}"]
    if getattr(args, "mini_step_limit", None) is not None:
        command += ["--config", f"agent.step_limit={args.mini_step_limit}"]
        command += ["--config", f"agent.cost_limit={args.mini_cost_limit}"]
        command += ["--config", f"model.model_kwargs.max_tokens={args.mini_max_tokens}"]
    return command, None if args.dry_run else api_environment(args.api_key_env)


def ensure_prepared(cli: argparse.Namespace, args: argparse.Namespace, run_dir: Path,
                    *, check_api: bool = True, check_rollout: bool = True) -> bool:
    """Prepare once before an Agent launch; return False only for a pending dry-run."""
    if not args.dry_run and check_api:
        if not args.allow_api_calls:
            raise RuntimeError("Agent stage requires explicit --allow-api-calls")
        api_environment(args.api_key_env)  # Fail before Docker work if the API key is absent.
        if check_rollout:
            rollout, _ = output_paths(run_dir, args.framework, args.rollout_id)
            if rollout.exists() and any(rollout.iterdir()) and not args.resume:
                raise RuntimeError(f"Rollout exists; use --resume or another rollout-id: {rollout}")

    prepare_cli = argparse.Namespace(**vars(cli))
    prepare_cli.stage = "prepare"
    prepare_cli.experiment = None
    prepare_args, _, snapshot_path, snapshot_text = configured_args(prepare_cli)
    check_snapshot(snapshot_path, snapshot_text)

    if args.dry_run:
        if preparation_complete(run_dir, verify_images=False):
            print("Dry run: task preparation appears complete; Agent would start without rebuilding images.")
            return True
        prepared, _, _ = preparation_paths(run_dir)
        prepare_args.resume = (prepared / "private" / "run-metadata.json").is_file()
        build_command(prepare_args, run_dir)  # Validate the planned preparation without writing.
        print("Dry run: task preparation would run first, then the selected Agent; no work was started.")
        return False

    with preparation_lock(run_dir):
        if preparation_complete(run_dir, verify_images=True):
            print("Task preparation is complete; reusing existing images.", flush=True)
            return True

        prepared, _, _ = preparation_paths(run_dir)
        metadata = prepared / "private" / "run-metadata.json"
        if prepared.is_dir() and any(prepared.iterdir()) and not metadata.is_file():
            raise RuntimeError(f"Incomplete task preparation has no resume metadata: {prepared}")
        prepare_args.resume = metadata.is_file()
        command, _ = build_command(prepare_args, run_dir)
        print("Task preparation is missing or incomplete; running prepare before Agent.", flush=True)
        save_snapshot(snapshot_path, snapshot_text)
        subprocess.run(command, cwd=LAB_ROOT, check=True)
        if not preparation_complete(run_dir, verify_images=True):
            raise RuntimeError("Task preparation did not complete; Agent API calls were not started")
    return True


def expected_ids(run_dir: Path, *, max_instances: int | None = None) -> list[str]:
    _, instances, _ = preparation_paths(run_dir)
    rows = [json.loads(line) for line in require_file(instances, "Prepared public instances")
            .read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = [row["instance_id"] for row in rows]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("Prepared public instances are empty or contain duplicate IDs")
    return ids[:max_instances]


def _json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def rollout_complete(run_dir: Path, args: argparse.Namespace, ids: list[str]) -> bool:
    rollout, _ = output_paths(run_dir, args.framework, args.rollout_id)
    predictions = _json(rollout / "preds.json")
    if predictions is None or set(predictions) != set(ids):
        return False
    if any(not isinstance(row, dict) or "model_patch" not in row for row in predictions.values()):
        return False
    extension = ".traj" if args.framework == "swe-agent" else ".traj.json"
    if any(not (rollout / iid / f"{iid}{extension}").is_file() for iid in ids):
        return False
    if args.framework == "swe-agent":
        summary = _json(rollout / "lab-run-summary.json")
        if summary is None or summary.get("process_return_code") != 0:
            return False
        if (summary.get("expected_instances") != len(ids) or summary.get("missing_ids")
                or summary.get("unexpected_ids")):
            return False
    return True


def evaluation_complete(directory: Path, ids: list[str], *, gold: bool) -> bool:
    summary = _json(directory / "report.json")
    if summary is None or summary.get("gold") is not gold:
        return False
    if summary.get("selected_tasks") != len(ids) or summary.get("evaluated") != len(ids):
        return False
    if summary.get("missing_predictions") or summary.get("unexpected_predictions"):
        return False
    if (summary.get("status_counts") or {}).get("error", 0):
        return False
    return all((_json(directory / iid / "report.json") or {}).get("status") in
               {"completed", "timeout", "patch_apply_error", "empty_prediction"} for iid in ids)


def sft_complete(directory: Path, eval_dir: Path, ids: list[str]) -> bool:
    manifest = _json(directory / "manifest.json")
    if manifest is None or manifest.get("source_ids") != sorted(ids):
        return False
    summary = _json(eval_dir / "report.json")
    if summary is None or manifest.get("evaluation_resolved") != summary.get("resolved"):
        return False
    paths = (directory / "all.jsonl", directory / "resolved.jsonl")
    if not all(path.is_file() for path in (*paths, directory / "resolved_chat.jsonl", directory / "dataset_info.json")):
        return False
    try:
        all_rows = [json.loads(line) for line in paths[0].read_text(encoding="utf-8").splitlines() if line.strip()]
        resolved_rows = [json.loads(line) for line in paths[1].read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return False
    resolved_ids = {iid for iid in ids if (_json(eval_dir / iid / "report.json") or {}).get("resolved") is True}
    return ({row.get("instance_id") for row in all_rows} == set(ids)
            and len(all_rows) == len(ids)
            and len(resolved_rows) == summary["resolved"]
            and len(resolved_ids) == summary["resolved"]
            and {row.get("instance_id") for row in resolved_rows} == resolved_ids
            and {row.get("instance_id") for row in resolved_rows} ==
            {row["instance_id"] for row in all_rows if row.get("resolved") is True})


def run_configured_agent(cli: argparse.Namespace, args: argparse.Namespace, run_dir: Path,
                         snapshot_path: Path, snapshot_text: str) -> None:
    rollout, evaluation = output_paths(run_dir, args.framework, args.rollout_id)
    gold_plan = None
    if cli.with_gold:
        gold_cli = argparse.Namespace(**vars(cli))
        gold_cli.stage = "gold"
        gold_cli.experiment = None
        gold_plan = configured_args(gold_cli)
        check_snapshot(gold_plan[2], gold_plan[3])
    prepared = preparation_complete(run_dir, verify_images=False)
    ids = expected_ids(run_dir, max_instances=getattr(args, "max_instances", None)) if prepared else []
    completed_rollout = prepared and rollout_complete(run_dir, args, ids)
    partial_rollout = rollout.is_dir() and any(rollout.iterdir()) and not completed_rollout
    if partial_rollout and not cli.resume:
        raise RuntimeError(f"Rollout is incomplete; use --resume: {rollout}")
    if not completed_rollout and not args.dry_run:
        if not args.allow_api_calls:
            raise RuntimeError("Rollout requires explicit --allow-api-calls")
        api_environment(args.api_key_env)

    if args.dry_run:
        if not prepared:
            ensure_prepared(cli, args, run_dir, check_api=False, check_rollout=False)
        evaluated = completed_rollout and evaluation_complete(evaluation, ids, gold=False)
        gold_status = "omitted"
        if gold_plan is not None:
            gold_args = gold_plan[0]
            gold_dir = run_dir / "evaluations" / "gold" / gold_args.rollout_id
            gold_status = ("skip" if prepared and evaluation_complete(
                gold_dir, expected_ids(run_dir), gold=True) else "run")
        sft_dir = run_dir / "sft" / args.framework / args.rollout_id
        print(f"Dry run: prepare={'skip' if prepared else 'run'}; "
              f"rollout={'skip' if completed_rollout else 'run'}; "
              f"eval={'skip' if evaluated else 'run'}; "
              f"gold={gold_status}; "
              f"sft={'skip' if evaluated and sft_complete(sft_dir, evaluation, ids) else 'run'}")
        print("Dry run: no files, Docker images, or API calls were created.")
        return

    if not completed_rollout:
        ensure_prepared(cli, args, run_dir, check_api=False, check_rollout=False)
    ids = expected_ids(run_dir, max_instances=getattr(args, "max_instances", None))
    if not rollout_complete(run_dir, args, ids):
        stage_args = argparse.Namespace(**vars(args))
        stage_args.stage = "rollout"
        stage_args.resume = partial_rollout
        command, environment = build_command(stage_args, run_dir)
        save_snapshot(snapshot_path, snapshot_text)
        print(f"Running {args.framework} rollout: {rollout}", flush=True)
        subprocess.run(command, cwd=LAB_ROOT, env=environment, check=True)
        if not rollout_complete(run_dir, args, ids):
            raise RuntimeError(f"Rollout did not cover every selected task: {rollout}")
    else:
        print(f"Rollout complete; reusing: {rollout}", flush=True)

    if not evaluation_complete(evaluation, ids, gold=False):
        stage_args = argparse.Namespace(**vars(args))
        stage_args.stage = "eval"
        stage_args.resume = evaluation.is_dir() and any(evaluation.iterdir())
        command, _ = build_command(stage_args, run_dir)
        command.append("--require-all")
        if getattr(args, "max_instances", None) is not None:
            for iid in ids:
                command.extend(["--agent-instance-id", iid])
        subprocess.run(command, cwd=LAB_ROOT, check=True)
        if not evaluation_complete(evaluation, ids, gold=False):
            raise RuntimeError(f"Evaluation did not complete: {evaluation}")
    else:
        print(f"Evaluation complete; reusing: {evaluation}", flush=True)

    if cli.with_gold:
        gold_args, _, gold_snapshot, gold_text = gold_plan
        gold_dir = run_dir / "evaluations" / "gold" / gold_args.rollout_id
        gold_ids = expected_ids(run_dir)
        with gold_lock(run_dir):
            if not evaluation_complete(gold_dir, gold_ids, gold=True):
                gold_args.resume = gold_dir.is_dir() and any(gold_dir.iterdir())
                command, _ = build_command(gold_args, run_dir)
                command.append("--require-all")
                save_snapshot(gold_snapshot, gold_text)
                subprocess.run(command, cwd=LAB_ROOT, check=True)
                if not evaluation_complete(gold_dir, gold_ids, gold=True):
                    raise RuntimeError(f"Gold check did not complete: {gold_dir}")
            else:
                print(f"Gold check complete; reusing: {gold_dir}", flush=True)

    sft_dir = run_dir / "sft" / args.framework / args.rollout_id
    if sft_complete(sft_dir, evaluation, ids):
        print(f"SFT complete; reusing: {sft_dir}", flush=True)
        return
    if sft_dir.is_dir() and any(sft_dir.iterdir()):
        raise RuntimeError(f"Incomplete SFT output needs inspection; refusing to overwrite: {sft_dir}")
    stage_args = argparse.Namespace(**vars(args))
    stage_args.stage = "sft"
    command, _ = build_command(stage_args, run_dir)
    subprocess.run(command, cwd=LAB_ROOT, check=True)
    if not sft_complete(sft_dir, evaluation, ids):
        raise RuntimeError(f"SFT export did not cover every selected task: {sft_dir}")


def main(argv: list[str] | None = None) -> None:
    cli = parse_args(argv)
    args, run_dir, snapshot_path, snapshot_text = configured_args(cli)
    check_snapshot(snapshot_path, snapshot_text)
    if args.workers < 1 or args.eval_workers < 1:
        raise ValueError("Worker counts must be positive")
    if args.eval_timeout_seconds < 1:
        raise ValueError("Evaluation timeout must be positive")
    if args.stage == "agent":
        run_configured_agent(cli, args, run_dir, snapshot_path, snapshot_text)
        return
    if args.stage == "rollout":
        prepared = preparation_complete(run_dir, verify_images=not args.dry_run)
        ids = expected_ids(run_dir, max_instances=getattr(args, "max_instances", None)) if prepared else []
        rollout, _ = output_paths(run_dir, args.framework, args.rollout_id)
        if prepared and rollout_complete(run_dir, args, ids):
            print(f"Rollout complete; reusing: {rollout}")
            return
        if rollout.is_dir() and any(rollout.iterdir()) and not args.resume:
            raise RuntimeError(f"Rollout is incomplete; use --resume: {rollout}")
        if not args.dry_run:
            if not args.allow_api_calls:
                raise RuntimeError("Rollout requires explicit --allow-api-calls")
            api_environment(args.api_key_env)
        if not ensure_prepared(cli, args, run_dir, check_api=False, check_rollout=False):
            return
    if args.stage in {"eval", "gold", "sft"} and not args.dry_run:
        if args.stage == "gold":
            _, _, selected = preparation_paths(run_dir)
            rows = [json.loads(line) for line in require_file(selected, "Prepared private selection")
                    .read_text(encoding="utf-8").splitlines() if line.strip()]
            ids = [row["_agent_instance_id"] for row in rows]
            destination = run_dir / "evaluations" / "gold" / args.rollout_id
            complete = evaluation_complete(destination, ids, gold=True)
        else:
            ids = expected_ids(run_dir, max_instances=getattr(args, "max_instances", None))
            _, evaluation = output_paths(run_dir, args.framework, args.rollout_id)
            destination = evaluation if args.stage == "eval" else run_dir / "sft" / args.framework / args.rollout_id
            complete = (evaluation_complete(evaluation, ids, gold=False) if args.stage == "eval"
                        else sft_complete(destination, evaluation, ids))
        if complete:
            print(f"{args.stage} complete; reusing: {destination}")
            return
        if args.stage in {"eval", "gold"}:
            args.resume = destination.is_dir() and any(destination.iterdir())
        elif destination.is_dir() and any(destination.iterdir()):
            raise RuntimeError(f"Incomplete SFT output needs inspection; refusing to overwrite: {destination}")
    command, environment = build_command(args, run_dir)
    if args.stage in {"eval", "gold"}:
        command.append("--require-all")
        if args.stage == "eval" and getattr(args, "max_instances", None) is not None:
            for iid in expected_ids(run_dir, max_instances=args.max_instances):
                command.extend(["--agent-instance-id", iid])
    print(f"Task-generation run: {run_dir}")
    if args.stage in {"agent", "rollout", "eval", "sft"}:
        rollout, evaluation = output_paths(run_dir, args.framework, args.rollout_id)
        print(f"Rollout: {rollout}")
        print(f"Evaluation: {evaluation}")
    elif args.stage == "gold":
        print(f"Gold evaluation: {run_dir / 'evaluations' / 'gold' / args.rollout_id}")
    else:
        print(f"Task preparation: {run_dir / 'task-prep' / run_dir.name}")
    print(f"Stage: {args.stage}; framework: {args.framework}")
    if args.dry_run:
        print("Dry run: no files, Docker images, or API calls were created.")
        return
    save_snapshot(snapshot_path, snapshot_text)
    subprocess.run(command, cwd=LAB_ROOT, env=environment, check=True)


if __name__ == "__main__":
    main()
