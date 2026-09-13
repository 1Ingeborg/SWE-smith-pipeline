#!/usr/bin/env python3
"""Compare Qwen models on diverse validated SWE-smith patches."""

import argparse
import ast
import json
import os
import subprocess
import time
from pathlib import Path

from openai import OpenAI


DEFAULT_MODELS = [
    "qwen3.7-flash-2026-07-15",
    "qwen-plus-2025-12-01",
    "qwen3-coder-flash-2025-07-28",
]
PREFERRED_STRATEGIES = [
    "func_pm_remove_assign",
    "func_pm_remove_loop",
    "func_pm_class_rm_funcs",
]

SYSTEM_PROMPT = """You write realistic GitHub issue reports for software bugs.
You will receive a bug-inducing patch, the tests affected by the bug, and relevant
test source code. Write only the issue report that a user or developer could have
submitted after observing the buggy behavior.

Requirements:
- Describe observable actual behavior and expected behavior accurately.
- Include a concise reproduction example when the supplied evidence supports one.
- Do not mention the patch, diff, benchmark, synthetic data, pytest, or test names.
- Do not reveal the exact code change, removed line, root cause, or solution.
- Do not invent APIs or behavior that are not supported by the evidence.
- Keep the report focused and reasonably short.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("validation_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS)
    parser.add_argument("--base-url", default="https://dashscope.aliyuncs.com/compatible-mode/v1")
    parser.add_argument("--max-output-tokens", type=int, default=800)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def select_diverse(rows: list[dict]) -> list[dict]:
    selected = []
    for strategy in PREFERRED_STRATEGIES:
        candidates = [row for row in rows if row.get("strategy") == strategy]
        if not candidates:
            continue
        if strategy == "func_pm_remove_loop":
            candidates.sort(key=lambda row: len(row.get("FAIL_TO_PASS", [])))
        else:
            candidates.sort(key=lambda row: row["instance_id"])
        selected.append(candidates[0])
    if len(selected) != len(PREFERRED_STRATEGIES):
        raise RuntimeError("Dataset does not contain all preferred comparison strategies")
    return selected


def read_image_file(image: str, path: str) -> str:
    result = subprocess.run(
        ["docker", "run", "--rm", image, "cat", f"/testbed/{path}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def extract_test(source: str, test_id: str) -> str:
    parts = test_id.split("::")[1:]
    target = parts[-1].split("[")[0]
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target:
            segment = ast.get_source_segment(source, node)
            if segment:
                return segment
    return source[:12000]


def test_context(row: dict) -> str:
    snippets = []
    seen = set()
    for test_id in row.get("FAIL_TO_PASS", [])[:2]:
        test_file = test_id.split("::", 1)[0]
        if test_file not in seen:
            source = read_image_file(row["image_name"], test_file)
            seen.add(test_file)
        snippets.append(f"Affected behavior reference ({test_id}):\n{extract_test(source, test_id)}")
    return "\n\n".join(snippets)


def validation_summary(validation_dir: Path, row: dict) -> str:
    path = validation_dir / row["instance_id"] / "test_output.txt"
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    useful = [line for line in lines if "FAILED" in line or "ERROR" in line]
    useful.extend(lines[-8:])
    return "\n".join(dict.fromkeys(useful))[-6000:]


def user_prompt(row: dict, validation_dir: Path) -> str:
    return f"""Create an issue report for this buggy behavior.

<bug_inducing_patch>
{row['patch']}
</bug_inducing_patch>

<affected_test_execution>
{validation_summary(validation_dir, row)}
</affected_test_execution>

<relevant_test_source>
{test_context(row)}
</relevant_test_source>

Return only the issue title and body.
"""


def main() -> None:
    args = parse_args()
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not set")

    client = OpenAI(api_key=api_key, base_url=args.base_url)
    rows = select_diverse(load_jsonl(args.dataset))
    args.output.parent.mkdir(parents=True, exist_ok=True)

    results = []
    for row in rows:
        prompt = user_prompt(row, args.validation_dir)
        for model in args.models:
            started = time.perf_counter()
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0,
                    max_tokens=args.max_output_tokens,
                    extra_body={"enable_thinking": False},
                )
            except Exception as error:
                results.append(
                    {
                        "instance_id": row["instance_id"],
                        "strategy": row.get("strategy"),
                        "broken_test_count": len(row.get("FAIL_TO_PASS", [])),
                        "model": model,
                        "latency_seconds": round(time.perf_counter() - started, 3),
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
                print(f"failed model={model} strategy={row.get('strategy')} error={type(error).__name__}")
                continue
            usage = response.usage
            results.append(
                {
                    "instance_id": row["instance_id"],
                    "strategy": row.get("strategy"),
                    "broken_test_count": len(row.get("FAIL_TO_PASS", [])),
                    "model": model,
                    "latency_seconds": round(time.perf_counter() - started, 3),
                    "prompt_tokens": usage.prompt_tokens if usage else None,
                    "completion_tokens": usage.completion_tokens if usage else None,
                    "content": response.choices[0].message.content,
                }
            )
            print(
                f"completed model={model} strategy={row.get('strategy')} "
                f"prompt_tokens={usage.prompt_tokens if usage else '?'} "
                f"completion_tokens={usage.completion_tokens if usage else '?'}"
            )

    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(results)} responses to {args.output}")


if __name__ == "__main__":
    main()
