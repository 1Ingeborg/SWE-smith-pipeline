#!/usr/bin/env python3
"""Issuegen helper: check generated issue reproductions without blocking."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any


PYTHON_BLOCK = re.compile(r"```(?:python|py)\s*\n(.*?)```", re.I | re.S)
EXCEPTION_NAME = re.compile(r"\b[A-Z][A-Za-z0-9_]*(?:Error|Exception)\b")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--timeout", type=int, default=45)
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


def extract_python_blocks(text: str) -> list[str]:
    return [match.strip() for match in PYTHON_BLOCK.findall(text) if match.strip()]


def syntax_error(code: str) -> str | None:
    try:
        ast.parse(code)
    except SyntaxError as error:
        return f"{error.msg} at line {error.lineno}"
    return None


def execute_reproduction(
    row: dict[str, Any], code: str, timeout: int
) -> dict[str, Any]:
    image = row.get("image_name")
    patch = row.get("patch")
    if not image or not patch:
        return {"status": "not_executed", "reason": "missing image_name or patch"}

    with tempfile.TemporaryDirectory(prefix="swesmith-repro-") as directory:
        audit_dir = Path(directory)
        (audit_dir / "bug.patch").write_text(patch, encoding="utf-8")
        (audit_dir / "repro.py").write_text(code + "\n", encoding="utf-8")
        command = [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--memory",
            "1g",
            "--cpus",
            "1",
            "--pids-limit",
            "256",
            "-v",
            f"{audit_dir}:/audit:ro",
            image,
            "bash",
            "-lc",
            (
                "set -e; cd /testbed; git apply /audit/bug.patch; "
                "source /opt/miniconda3/bin/activate; conda activate testbed; "
                "python /audit/repro.py"
            ),
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            return {
                "status": "timeout",
                "returncode": None,
                "stdout_tail": (error.stdout or "")[-2000:],
                "stderr_tail": (error.stderr or "")[-2000:],
            }

    return {
        "status": "exited_zero" if result.returncode == 0 else "exited_nonzero",
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-2000:],
        "stderr_tail": result.stderr[-4000:],
    }


def check_row(row: dict[str, Any], execute: bool, timeout: int) -> dict[str, Any]:
    statement = row.get("problem_statement") or ""
    blocks = extract_python_blocks(statement)
    claimed_exceptions = sorted(set(EXCEPTION_NAME.findall(statement)))
    warnings = []
    execution = None

    if not blocks:
        warnings.append("no fenced Python reproduction")
    else:
        error = syntax_error(blocks[0])
        if error:
            warnings.append(f"Python reproduction has syntax error: {error}")
        elif execute:
            execution = execute_reproduction(row, blocks[0], timeout)
            if execution["status"] == "timeout":
                warnings.append("Python reproduction timed out")
            elif execution["status"] == "not_executed":
                warnings.append(execution["reason"])
            elif execution["status"] == "exited_nonzero":
                observed = sorted(
                    set(EXCEPTION_NAME.findall(execution.get("stderr_tail", "")))
                )
                if claimed_exceptions and set(claimed_exceptions) & set(observed):
                    pass
                else:
                    warnings.append(
                        "Python reproduction exited with an exception not claimed "
                        "by the issue"
                    )

    return {
        "instance_id": row.get("instance_id"),
        "python_block_count": len(blocks),
        "claimed_exceptions": claimed_exceptions,
        "execution": execution,
        "warnings": warnings,
        "status": "checked" if not warnings else "checked_with_warnings",
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    args = parse_args()
    rows = load_records(args.input)
    results = [check_row(row, args.execute, args.timeout) for row in rows]
    write_jsonl(args.output, results)
    counts = Counter(result["status"] for result in results)
    summary = {
        "input": str(args.input),
        "total": len(results),
        "counts": dict(sorted(counts.items())),
        "warning_count": sum(bool(result["warnings"]) for result in results),
        "executed_count": sum(result["execution"] is not None for result in results),
    }
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
