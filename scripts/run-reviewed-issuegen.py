#!/usr/bin/env python3
"""Generate DeepSeek-reviewed issue statements for validated SWE-smith tasks."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any


LAB_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = LAB_ROOT / "src" / "swesmith_lab" / "issuegen" / "generate.py"
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def read_records(path: Path) -> list[dict[str, Any]]:
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return []
    if content.startswith("["):
        result = json.loads(content)
        if not isinstance(result, list):
            raise ValueError(f"Expected a JSON array: {path}")
        return result
    return [json.loads(line) for line in content.splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temporary, path)


def load_key(env: dict[str, str]) -> None:
    if env.get("DEEPSEEK_API_KEY"):
        return
    env_file = LAB_ROOT / ".env"
    if env_file.is_file():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip().removeprefix("export ")
            if line.startswith("DEEPSEEK_API_KEY="):
                env["DEEPSEEK_API_KEY"] = line.split("=", 1)[1].strip().strip('"\'')
                break
    if not env.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY is missing; set it in the environment or lab .env")


def classify(
    input_rows: list[dict[str, Any]],
    generated: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    by_id = {row["instance_id"]: row for row in input_rows}
    accepted: list[dict[str, Any]] = []
    quarantine: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for row in generated:
        instance_id = row["instance_id"]
        status = row.get("review_status", "unknown")
        counts[status] += 1
        merged = {**by_id[instance_id], **row}
        if status == "accepted" and str(row.get("problem_statement") or "").strip():
            accepted.append(merged)
        else:
            quarantine.append(merged)
    for failure in failures:
        counts["preparation_failed"] += 1
        quarantine.append({**by_id[failure["instance_id"]], **failure, "review_status": "preparation_failed", "problem_statement": ""})
    if len(accepted) + len(quarantine) != len(input_rows):
        raise RuntimeError("Classification count does not match input count")
    return accepted, quarantine, dict(sorted(counts.items()))


def run(command: list[str], env: dict[str, str]) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=LAB_ROOT, env=env, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--validation-dir", required=True, type=Path)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--max-output-tokens", type=int, default=800)
    parser.add_argument("--max-failing-tests", type=int, default=3)
    parser.add_argument("--max-rewrites", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not RUN_ID.fullmatch(args.run_id):
        raise ValueError("Invalid run-id")
    if args.workers < 1 or args.max_output_tokens < 1 or args.max_failing_tests < 1 or args.max_rewrites < 0:
        raise ValueError("Invalid worker, token, test, or rewrite setting")
    if not args.input.is_file() or not args.validation_dir.is_dir() or not args.python.is_file():
        raise FileNotFoundError("Input, validation directory, or Python executable is missing")

    rows = read_records(args.input)
    ids = [row.get("instance_id") for row in rows]
    if not ids or any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError("Input must contain unique, non-empty instance_id values")
    for row in rows:
        if not row.get("image_name") or not row.get("patch") or not row.get("FAIL_TO_PASS"):
            raise ValueError(f"Missing image, patch, or FAIL_TO_PASS: {row['instance_id']}")
    env = os.environ.copy()
    load_key(env)
    images = sorted({row["image_name"] for row in rows})
    for name in images:
        result = subprocess.run(["docker", "image", "inspect", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if result.returncode != 0:
            raise RuntimeError(f"Docker image is missing: {name}")

    run_dir = args.run_root.resolve() / args.run_id
    manifest_path = run_dir / "manifest.json"
    settings = {
        "backend": "deepseek_reviewed",
        "model": "deepseek-flash",
        "api_base": "https://api.deepseek.com",
        "input_instance_ids": ids,
        "validation_dir": str(args.validation_dir.resolve()),
        "workers": args.workers,
        "max_output_tokens": args.max_output_tokens,
        "max_failing_tests": args.max_failing_tests,
        "max_rewrites": args.max_rewrites,
    }
    if run_dir.exists() and not args.resume and (
        manifest_path.exists()
        or {entry.name for entry in run_dir.iterdir()} - {"validated-input.jsonl", "validation-evidence", "issuegen.log"}
    ):
        raise RuntimeError(f"Run already exists: {run_dir}; choose a new run-id or --resume")
    input_path = run_dir / "input.jsonl"
    if args.resume:
        if not manifest_path.is_file():
            raise RuntimeError(f"Cannot resume without manifest: {manifest_path}")
        prior = json.loads(manifest_path.read_text(encoding="utf-8"))
        if any(prior.get(key) != value for key, value in settings.items()):
            raise RuntimeError("Resume settings or input IDs differ from the existing run")
        if not input_path.is_file() or read_records(input_path) != rows:
            raise RuntimeError("Resume input content differs from the existing run")
    run_dir.mkdir(parents=True, exist_ok=True)
    if not input_path.exists():
        write_jsonl(input_path, rows)
    manifest = {**settings, "status": "running", "input_count": len(rows), "updated_at_unix": time.time()}
    write_json(manifest_path, manifest)

    prepared_path = run_dir / "prepared.jsonl"
    failure_path = run_dir / "prepared.jsonl.failures.jsonl"
    raw_path = run_dir / "problem-statements.jsonl"
    try:
        if not prepared_path.exists():
            run([
                str(args.python), str(GENERATOR), str(prepared_path),
                "--dataset", str((run_dir / "input.jsonl").resolve()),
                "--validation-dir", str(args.validation_dir.resolve()),
                "--prepare-only", "--max-failing-tests", str(args.max_failing_tests),
            ], env)
        prepared = read_records(prepared_path)
        failures = read_records(failure_path) if failure_path.exists() else []
        prepared_ids = [row["instance_id"] for row in prepared]
        failed_ids = [row["instance_id"] for row in failures]
        if len(set(prepared_ids + failed_ids)) != len(ids) or set(prepared_ids + failed_ids) != set(ids):
            raise RuntimeError("Prepared and failed IDs do not partition the input; use a new run-id")
        if prepared:
            run([
                str(args.python), str(GENERATOR), str(raw_path),
                "--prepared-input", str(prepared_path),
                "--generator-model", "deepseek-flash",
                "--leakage-reviewer-model", "deepseek-flash",
                "--factuality-reviewer-model", "deepseek-flash",
                "--base-url", "https://api.deepseek.com",
                "--api-key-env", "DEEPSEEK_API_KEY",
                "--max-output-tokens", str(args.max_output_tokens),
                "--max-rewrites", str(args.max_rewrites),
                "--workers", str(args.workers),
                *(["--resume"] if raw_path.exists() else []),
            ], env)
        generated = read_records(raw_path) if raw_path.exists() else []
        generated_ids = [row["instance_id"] for row in generated]
        if len(set(generated_ids)) != len(prepared_ids) or set(generated_ids) != set(prepared_ids):
            raise RuntimeError("Generated results do not match prepared IDs")
        accepted, quarantine, counts = classify(rows, generated, failures)
        write_jsonl(run_dir / "accepted.jsonl", accepted)
        write_jsonl(run_dir / "quarantine.jsonl", quarantine)
        write_jsonl(run_dir / "audit.jsonl", generated)
        summary = {
            "input_count": len(rows), "prepared_count": len(prepared),
            "preparation_failed_count": len(failures), "raw_count": len(generated),
            "accepted_count": len(accepted), "quarantine_count": len(quarantine),
            "counts": counts,
        }
        write_json(run_dir / "summary.json", summary)
        manifest.update({**summary, "audit_counts": counts, "status": "completed", "updated_at_unix": time.time()})
        write_json(manifest_path, manifest)
        print(f"Completed {args.run_id}: accepted={len(accepted)} quarantine={len(quarantine)}", flush=True)
    except Exception as error:
        manifest.update({"status": "failed", "error": f"{type(error).__name__}: {error}", "updated_at_unix": time.time()})
        write_json(manifest_path, manifest)
        raise


if __name__ == "__main__":
    main()
