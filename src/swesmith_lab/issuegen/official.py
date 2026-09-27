#!/usr/bin/env python3
"""Optional upstream issuegen backend: generate, audit, and split tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = REPO_DIR / "configs" / "issue_gen" / "ig_v2_deepseek.yaml"
DEFAULT_LAUNCHER = SCRIPT_DIR / "official_launcher.py"
DEFAULT_AUDITOR = SCRIPT_DIR / "audit.py"
DEFAULT_USABILITY_CHECKER = SCRIPT_DIR / "usability.py"
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def data_root() -> Path:
    value = os.environ.get("SWE_LAB_DATA_ROOT")
    env_file = REPO_DIR / ".env"
    if not value and env_file.is_file():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip().removeprefix("export ")
            if line.startswith("SWE_LAB_DATA_ROOT="):
                value = line.split("=", 1)[1].strip().strip('"\'')
                break
    path = Path(value or REPO_DIR / ".local").expanduser()
    return (path if path.is_absolute() else REPO_DIR / path).resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Validated JSON array or JSONL")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-root", type=Path, default=data_root() / "results" / "issuegen-runs")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--demo-pool",
        type=Path,
        default=data_root() / "datasets" / "swe-smith-lab" / "demo-problem-statements.json",
    )
    parser.add_argument("--demo-seed", type=int, default=42)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--review-ambiguous", action="store_true")
    parser.add_argument(
        "--audit-policy",
        choices=("strict", "balanced"),
        default="balanced",
    )
    parser.add_argument("--check-reproduction", action="store_true")
    parser.add_argument(
        "--validation-dir",
        type=Path,
        default=REPO_DIR / "vendor" / "SWE-smith" / "logs" / "run_validation",
    )
    parser.add_argument(
        "--issuegen-repo", type=Path, default=REPO_DIR / "vendor" / "SWE-smith"
    )
    parser.add_argument(
        "--issuegen-python",
        type=Path,
        default=data_root() / "venvs" / "swesmith-lab-llm" / "bin" / "python",
    )
    parser.add_argument(
        "--audit-python",
        type=Path,
        default=data_root() / "venvs" / "swesmith-lab-core" / "bin" / "python",
    )
    parser.add_argument("--launcher", type=Path, default=DEFAULT_LAUNCHER)
    parser.add_argument("--auditor", type=Path, default=DEFAULT_AUDITOR)
    parser.add_argument(
        "--usability-checker", type=Path, default=DEFAULT_USABILITY_CHECKER
    )
    return parser.parse_args()


def load_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        value = json.loads(text)
        if not isinstance(value, list):
            raise ValueError("JSON input must be an array")
        return value
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(temporary, path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_commit(repo: Path) -> str | None:
    upstream_marker = repo / "UPSTREAM_COMMIT"
    if upstream_marker.is_file():
        return upstream_marker.read_text(encoding="utf-8").strip() + "+lab-patches"
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def validate_run_id(run_id: str) -> None:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError(
            "run-id must contain only letters, numbers, dot, underscore, and hyphen"
        )


def ensure_validation_link(workspace: Path, validation_dir: Path) -> None:
    if not validation_dir.is_dir():
        raise FileNotFoundError(f"Validation directory not found: {validation_dir}")
    logs_dir = workspace / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    link = logs_dir / "run_validation"
    if link.is_symlink():
        if link.resolve() != validation_dir.resolve():
            raise RuntimeError(f"Validation symlink points to the wrong directory: {link}")
        return
    if link.exists():
        raise RuntimeError(f"Refusing to replace existing validation path: {link}")
    link.symlink_to(validation_dir.resolve(), target_is_directory=True)


def run_command(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    printable = " ".join(command)
    print(f"+ {printable}", flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def main() -> None:
    args = parse_args()
    validate_run_id(args.run_id)
    if args.workers < 1:
        raise ValueError("--workers must be at least 1")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be at least 1")

    required_paths = [
        args.input,
        args.config,
        args.demo_pool,
        args.issuegen_repo,
        args.issuegen_python,
        args.audit_python,
        args.launcher,
        args.auditor,
        args.usability_checker,
    ]
    for path in required_paths:
        if not path.exists():
            raise FileNotFoundError(path)

    records = load_records(args.input)
    if args.limit is not None:
        records = records[: args.limit]
    if not records:
        raise ValueError("Input contains no records")
    instance_ids = [row.get("instance_id") for row in records]
    if not all(isinstance(value, str) and value for value in instance_ids):
        raise ValueError("Every input row must have a non-empty instance_id")
    if len(instance_ids) != len(set(instance_ids)):
        raise ValueError("Input contains duplicate instance_id values")

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    model = config.get("model")
    if not isinstance(model, str) or not model:
        raise ValueError("Issue generation config must define model")

    run_dir = args.run_root.resolve() / args.run_id
    workspace = run_dir / "workspace"
    input_json = run_dir / "input.json"
    raw_json = run_dir / "input__ig_llm.json"
    audit_jsonl = run_dir / "audit.jsonl"
    summary_json = run_dir / "summary.json"
    accepted_jsonl = run_dir / "accepted.jsonl"
    quarantine_jsonl = run_dir / "quarantine.jsonl"
    manifest_json = run_dir / "manifest.json"
    usability_jsonl = run_dir / "usability.jsonl"
    usability_summary_json = run_dir / "usability-summary.json"

    if run_dir.exists() and not args.resume and (
        manifest_json.exists()
        or {entry.name for entry in run_dir.iterdir()} - {"validated-input.jsonl", "validation-evidence", "issuegen.log"}
    ):
        raise RuntimeError(
            f"Run directory already exists; use a new --run-id or --resume: {run_dir}"
        )
    run_dir.mkdir(parents=True, exist_ok=True)

    selected_payload = json.dumps(records, ensure_ascii=False, sort_keys=True)
    selected_sha256 = hashlib.sha256(selected_payload.encode("utf-8")).hexdigest()
    if input_json.exists():
        existing_payload = json.dumps(
            load_records(input_json), ensure_ascii=False, sort_keys=True
        )
        existing_sha256 = hashlib.sha256(existing_payload.encode("utf-8")).hexdigest()
        if existing_sha256 != selected_sha256:
            raise RuntimeError("Resume input does not match the existing run input")
    else:
        write_json(input_json, records)

    ensure_validation_link(workspace, args.validation_dir)
    env = os.environ.copy()
    source_path = str(args.issuegen_repo.resolve())
    prior_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        source_path
        if not prior_pythonpath
        else f"{source_path}{os.pathsep}{prior_pythonpath}"
    )
    deepseek_key = env.get("DEEPSEEK_API_KEY", "").strip()
    if not deepseek_key:
        env_file = REPO_DIR / ".env"
        if env_file.is_file():
            for raw in env_file.read_text(encoding="utf-8").splitlines():
                line = raw.strip().removeprefix("export ")
                if line.startswith("DEEPSEEK_API_KEY="):
                    deepseek_key = line.split("=", 1)[1].strip().strip('"\'')
                    break
    if not deepseek_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not set")
    env["DEEPSEEK_API_KEY"] = deepseek_key
    env["SWESMITH_ISSUEGEN_MODEL"] = model
    env["HF_HUB_OFFLINE"] = "1"
    env["HF_DATASETS_OFFLINE"] = "1"

    manifest = {
        "schema_version": 1,
        "run_id": args.run_id,
        "status": "running",
        "input_path": str(args.input.resolve()),
        "input_sha256": selected_sha256,
        "input_count": len(records),
        "model": model,
        "config_path": str(args.config.resolve()),
        "config_sha256": sha256_file(args.config),
        "demo_pool_path": str(args.demo_pool.resolve()),
        "demo_pool_sha256": sha256_file(args.demo_pool),
        "demo_seed": args.demo_seed,
        "workers": args.workers,
        "issuegen_commit": git_commit(args.issuegen_repo),
        "lab_commit": git_commit(REPO_DIR),
        "started_at_unix": time.time(),
    }
    write_json(manifest_json, manifest)

    try:
        raw_records = load_records(raw_json) if raw_json.exists() else []
        raw_ids = {row.get("instance_id") for row in raw_records}
        can_reuse_raw = args.resume and raw_ids == set(instance_ids)
        if can_reuse_raw:
            print(f"Reusing complete issue generation output: {raw_json}", flush=True)
        else:
            issuegen_command = [
                str(args.issuegen_python),
                str(args.launcher),
                "-d",
                str(input_json),
                "-c",
                str(args.config.resolve()),
                "--demo_pool",
                str(args.demo_pool.resolve()),
                "--demo_seed",
                str(args.demo_seed),
                "-w",
                str(args.workers),
            ]
            run_command(issuegen_command, cwd=workspace, env=env)
            if not raw_json.exists():
                raise RuntimeError(f"Issue generation did not produce {raw_json}")
            raw_records = load_records(raw_json)
        raw_ids = {row["instance_id"] for row in raw_records}
        missing_ids = sorted(set(instance_ids) - raw_ids)
        if missing_ids:
            raise RuntimeError(
                f"Issue generation omitted {len(missing_ids)} instances: {missing_ids}"
            )

        audit_command = [
            str(args.audit_python),
            str(args.auditor),
            str(raw_json),
            str(audit_jsonl),
            "--summary",
            str(summary_json),
            "--accepted-output",
            str(accepted_jsonl),
            "--quarantine-output",
            str(quarantine_jsonl),
            "--policy",
            args.audit_policy,
        ]
        if args.review_ambiguous:
            audit_command.append("--review-ambiguous")
        run_command(audit_command, cwd=REPO_DIR, env=env)

        summary = json.loads(summary_json.read_text(encoding="utf-8"))
        usability_summary = None
        if args.check_reproduction:
            usability_command = [
                str(args.audit_python),
                str(args.usability_checker),
                str(accepted_jsonl),
                str(usability_jsonl),
                "--summary",
                str(usability_summary_json),
                "--execute",
            ]
            run_command(usability_command, cwd=REPO_DIR, env=env)
            usability_summary = json.loads(
                usability_summary_json.read_text(encoding="utf-8")
            )
        manifest.update(
            {
                "status": "completed",
                "finished_at_unix": time.time(),
                "raw_count": len(raw_records),
                "audit_counts": summary.get("counts", {}),
                "semantic_review_calls": summary.get("semantic_review_calls", 0),
                "usability_summary": usability_summary,
                "artifacts": {
                    "raw": str(raw_json),
                    "audit": str(audit_jsonl),
                    "summary": str(summary_json),
                    "accepted": str(accepted_jsonl),
                    "quarantine": str(quarantine_jsonl),
                    "issuegen_logs": str(workspace / "logs" / "issue_gen"),
                    "usability": (
                        str(usability_jsonl) if usability_summary is not None else None
                    ),
                    "usability_summary": (
                        str(usability_summary_json)
                        if usability_summary is not None
                        else None
                    ),
                },
            }
        )
        write_json(manifest_json, manifest)
    except Exception as error:
        manifest.update(
            {
                "status": "failed",
                "finished_at_unix": time.time(),
                "error": f"{type(error).__name__}: {error}",
            }
        )
        write_json(manifest_json, manifest)
        raise

    print(f"Completed run {args.run_id}: {manifest_json}", flush=True)


if __name__ == "__main__":
    main()
