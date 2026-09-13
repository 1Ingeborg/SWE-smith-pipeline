#!/usr/bin/env python3
"""Generate and automatically review SWE-smith problem statements at scale."""

import argparse
import ast
import json
import os
import re
import shlex
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from openai import OpenAI


GENERATOR_SYSTEM_PROMPT = """You write realistic GitHub issue reports from an
external user's point of view. The private evidence may contain an implementation
change, test code, and failure output. Reason from it silently; never cite or
describe those private artifacts.

Requirements:
- Describe only observable actual behavior and supported expected behavior.
- Include a concise reproduction using public APIs when the evidence supports it.
- Never mention patches, diffs, mutations, synthetic data, benchmarks, tests,
  assertions, test frameworks, test files, test names, or CI.
- Never state that code, a line, loop, assignment, class, or method was removed,
  deleted, missing, bypassed, modified, or changed.
- Do not expose the implementation defect, infer a root cause, or suggest a fix.
- Do not invent APIs, inputs, exceptions, or behavior not supported by evidence.
- Return an English Markdown title and body of at most 180 words.
"""

LEAKAGE_REVIEWER_SYSTEM_PROMPT = """You are the leakage gate for synthetic
software-engineering training data. Compare a candidate issue with the hidden
bug-inducing change. Reject direct and indirect clues to the changed code,
implementation defect, root cause, or likely repair.

The issue MUST identify the failing behavior. A symptom is not leakage merely
because it is closely related to the purpose of changed code. Public API names,
triggering inputs, exception classes, and externally visible outputs are allowed.
For example, these are allowed symptoms:
- "Locally defined functions are absent from tracing results."
- "AttributeError is raised when the environment option is unset."
- "New annotation imports remain outside TYPE_CHECKING."
These are leaks:
- "Previous stack frames are no longer searched."
- "The wrong branch calls split on None."
- "The import-removal helper stopped appending entries."

Return one JSON object and no Markdown. Use exactly this schema:
{
  "verdict": "accept" | "needs_review" | "reject",
  "checks": {
    "no_changed_identifier_leakage": true | false,
    "no_change_description": true | false,
    "no_root_cause_hint": true | false,
    "no_repair_hint": true | false,
    "no_test_leakage": true | false
  },
  "confidence": 0.0,
  "reasons": ["short reason"],
  "candidate_quotes": ["exact candidate phrase that leaks"],
  "leaked_terms": ["private identifier or mechanism exposed by that phrase"]
}

Treat an exact identifier from a deleted or replaced statement as leakage when
it exposes what must be restored, even if that identifier also appears in an
exception. Phrases such as "attempts to", "because", "due to", "missing method",
or descriptions of internal helper behavior are root-cause clues. Public API
names needed solely to reproduce the symptom are allowed. Use "accept" only if
every check is true. A reject verdict requires at least one exact quote from the
candidate; never attribute text from private evidence to the candidate. Use
"needs_review" below 0.85 confidence.
"""

FACTUALITY_REVIEWER_SYSTEM_PROMPT = """You are the factuality gate for synthetic
software-engineering training data. You receive execution output and behavioral
examples, but not the hidden code change. Check every claim in the candidate
issue against that evidence. Be skeptical: do not infer causes, downstream
consequences, affected inputs, or APIs that were not demonstrated.

Return one JSON object and no Markdown. Use exactly this schema:
{
  "verdict": "accept" | "needs_review" | "reject",
  "checks": {
    "observable_behavior_grounded": true | false,
    "expected_behavior_grounded": true | false,
    "reproduction_supported_and_complete": true | false,
    "no_unsupported_cause_or_consequence": true | false,
    "sufficiently_specific": true | false
  },
  "confidence": 0.0,
  "reasons": ["short reason"],
  "unsupported_claims": ["claim not established by evidence"]
}

Use "accept" only if every check is true. Reject invented consequences and
reproduction snippets with undefined names or omitted required setup. Use
"needs_review" when evidence is ambiguous or confidence is below 0.85. Fluency
is irrelevant. An import remaining at module scope supports a claim about import
placement; it does not support a claim about runtime import errors unless the
execution evidence actually shows an ImportError or runtime failure.
"""

FORBIDDEN_PATTERNS = {
    "test reference": r"\b(?:pytest|unit tests?|test suite|test case|assertions?|CI)\b|test_[A-Za-z0-9_]+|tests/",
    "data-generation reference": r"\b(?:patch(?:es)?|diff|mutation|benchmark|synthetic data|SWE-smith)\b",
    "implementation-change disclosure": r"\b(?:removed|deleted|modified|changed|restored|re-add(?:ed)?)\b",
    "repair instruction": r"\b(?:fix by|should restore|should add back|needs? to restore|reimplement)\b",
    "root-cause wording": r"\b(?:the (?:function|method|code|implementation) attempts? to|because|due to|originating from|internal helper)\b",
    "placeholder": r"\b(?:TODO|TBD|unknown version|insert .* here)\b",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--validation-dir", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--prepared-input", type=Path)
    parser.add_argument("--candidate-input", type=Path)
    parser.add_argument("--generator-model", default="qwen3.7-flash-2026-07-15")
    parser.add_argument("--leakage-reviewer-model", default="qwen3.7-flash-2026-07-15")
    parser.add_argument("--factuality-reviewer-model", default="qwen3.7-flash-2026-07-15")
    parser.add_argument("--base-url", default="https://dashscope.aliyuncs.com/compatible-mode/v1")
    parser.add_argument("--max-output-tokens", type=int, default=800)
    parser.add_argument("--max-failing-tests", type=int, default=3)
    parser.add_argument("--max-rewrites", type=int, default=1)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skip-reviewer", action="store_true")
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_records(path: Path) -> list[dict[str, Any]]:
    content = path.read_text(encoding="utf-8").strip()
    if content.startswith("["):
        return json.loads(content)
    return [json.loads(line) for line in content.splitlines() if line.strip()]


def select_diverse_tests(test_ids: list[str], limit: int) -> list[str]:
    selected: list[str] = []
    groups: set[str] = set()
    for test_id in test_ids:
        parts = test_id.split("::")
        group = "::".join(parts[:2])
        if group not in groups:
            selected.append(test_id)
            groups.add(group)
        if len(selected) == limit:
            return selected
    for test_id in test_ids:
        if test_id not in selected:
            selected.append(test_id)
        if len(selected) == limit:
            break
    return selected


def read_image_file(image: str, path: str) -> str:
    result = subprocess.run(
        ["docker", "run", "--rm", image, "cat", f"/testbed/{path}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result.stdout


def extract_test(source: str, test_id: str) -> str:
    target = test_id.split("::")[-1].split("[")[0]
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == target:
            segment = ast.get_source_segment(source, node)
            if segment:
                return segment
    return source[:12000]


def collect_test_context(row: dict[str, Any], test_ids: list[str]) -> str:
    sources: dict[str, str] = {}
    snippets = []
    for test_id in test_ids:
        test_file = test_id.split("::", 1)[0]
        if test_file not in sources:
            sources[test_file] = read_image_file(row["image_name"], test_file)
        snippets.append(
            f"Behavioral evidence {len(snippets) + 1}:\n"
            f"{extract_test(sources[test_file], test_id)}"
        )
    return "\n\n".join(snippets)


def reproduce_failures(row: dict[str, Any], test_ids: list[str]) -> str:
    pytest_args = " ".join(shlex.quote(test_id) for test_id in test_ids)
    script = (
        "set -e; cd /testbed; git apply -; "
        "source /opt/miniconda3/bin/activate; conda activate testbed; "
        f"pytest -vv --tb=short {pytest_args}"
    )
    result = subprocess.run(
        ["docker", "run", "--rm", "-i", row["image_name"], "bash", "-lc", script],
        input=row["patch"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode != 1:
        raise RuntimeError(
            f"Expected pytest exit 1 for {row['instance_id']}, got {result.returncode}:\n"
            f"{output[-3000:]}"
        )
    return output[-16000:]


def prepare_item(row: dict[str, Any], max_failing_tests: int) -> dict[str, Any]:
    selected_tests = select_diverse_tests(row.get("FAIL_TO_PASS", []), max_failing_tests)
    if not selected_tests:
        raise RuntimeError(f"No FAIL_TO_PASS tests for {row['instance_id']}")
    failure_details = reproduce_failures(row, selected_tests)
    test_context = collect_test_context(row, selected_tests)
    evidence = f"""<private_bug_inducing_patch>
{row['patch']}
</private_bug_inducing_patch>

<private_failure_execution>
{failure_details}
</private_failure_execution>

<private_behavioral_test_source>
{test_context}
</private_behavioral_test_source>"""
    return {
        "instance_id": row["instance_id"],
        "repo": row.get("repo"),
        "strategy": row.get("strategy"),
        "FAIL_TO_PASS": row.get("FAIL_TO_PASS", []),
        "PASS_TO_PASS_count": len(row.get("PASS_TO_PASS", [])),
        "selected_failure_tests": selected_tests,
        "failure_details": failure_details,
        "test_context": test_context,
        "evidence": evidence,
    }


def changed_identifiers(evidence: str) -> set[str]:
    patch_match = re.search(
        r"<private_bug_inducing_patch>\n(.*?)\n</private_bug_inducing_patch>",
        evidence,
        flags=re.DOTALL,
    )
    if not patch_match:
        return set()
    removed = "\n".join(
        line[1:]
        for line in patch_match.group(1).splitlines()
        if line.startswith("-") and not line.startswith("---")
    )
    identifiers = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b", removed))
    return identifiers - {
        "self", "None", "True", "False", "return", "class", "def", "from",
        "import", "with", "else", "elif", "raise", "yield", "async", "await",
    }


def rule_findings(candidate: str, evidence: str) -> dict[str, list[str]]:
    hard = [
        label
        for label, pattern in FORBIDDEN_PATTERNS.items()
        if re.search(pattern, candidate, flags=re.IGNORECASE)
    ]
    words = re.findall(r"\b\w+\b", candidate)
    if len(words) > 180:
        hard.append("over 180 words")
    candidate_exceptions = set(re.findall(r"\b[A-Z][A-Za-z]+(?:Error|Exception)\b", candidate))
    evidence_exceptions = set(re.findall(r"\b[A-Z][A-Za-z]+(?:Error|Exception)\b", evidence))
    unsupported_exceptions = sorted(candidate_exceptions - evidence_exceptions)
    if unsupported_exceptions:
        hard.append("unsupported exception: " + ", ".join(unsupported_exceptions))
    if re.search(r"\bruntime import errors?\b", candidate, flags=re.IGNORECASE) and "ImportError" not in evidence:
        hard.append("unsupported runtime import failure")
    warnings = []
    if len(candidate.strip()) < 80:
        warnings.append("too short")
    leaked = sorted(
        identifier
        for identifier in changed_identifiers(evidence)
        if re.search(rf"\b{re.escape(identifier)}\b", candidate)
    )
    if leaked:
        warnings.append("changed-symbol reference: " + ", ".join(leaked[:8]))
    return {"hard": hard, "warnings": warnings}


def patch_only(evidence: str) -> str:
    match = re.search(
        r"<private_bug_inducing_patch>\n(.*?)\n</private_bug_inducing_patch>",
        evidence,
        flags=re.DOTALL,
    )
    return match.group(1) if match else ""


def behavior_only(item: dict[str, Any]) -> str:
    if item.get("failure_details") or item.get("test_context"):
        return (
            "<failure_execution>\n"
            + item.get("failure_details", "")
            + "\n</failure_execution>\n\n<behavioral_source>\n"
            + item.get("test_context", "")
            + "\n</behavioral_source>"
        )
    return re.sub(
        r"<private_bug_inducing_patch>\n.*?\n</private_bug_inducing_patch>\n*",
        "",
        item["evidence"],
        flags=re.DOTALL,
    )


def call_model(client: OpenAI, model: str, messages: list[dict[str, str]], max_tokens: int) -> tuple[str, dict[str, Any]]:
    started = time.perf_counter()
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
        max_tokens=max_tokens,
        extra_body={"enable_thinking": False},
    )
    usage = response.usage
    return response.choices[0].message.content or "", {
        "prompt_tokens": usage.prompt_tokens if usage else None,
        "completion_tokens": usage.completion_tokens if usage else None,
        "latency_seconds": round(time.perf_counter() - started, 3),
    }


def parse_reviewer_json(content: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
        if not match:
            raise
        result = json.loads(match.group(0))
    if result.get("verdict") not in {"accept", "needs_review", "reject"}:
        raise ValueError(f"Invalid reviewer verdict: {result.get('verdict')!r}")
    return result


def reviewer_passed(review: dict[str, Any] | None) -> bool:
    if not review or review.get("verdict") != "accept":
        return False
    if float(review.get("confidence", 0)) < 0.85:
        return False
    checks = review.get("checks")
    return isinstance(checks, dict) and bool(checks) and all(value is True for value in checks.values())


def run_reviewer(
    client: OpenAI,
    model: str,
    system_prompt: str,
    private_evidence: str,
    candidate: str,
) -> dict[str, Any]:
    prompt = f"""<private_evidence>
{private_evidence}
</private_evidence>

<candidate_issue>
{candidate}
</candidate_issue>"""
    try:
        raw, usage = call_model(
            client,
            model,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            500,
        )
        return {"result": parse_reviewer_json(raw), "raw": raw, "usage": usage, "error": None}
    except Exception as error:
        return {
            "result": None,
            "raw": None,
            "usage": None,
            "error": f"{type(error).__name__}: {error}",
        }


def review_feedback(review_round: dict[str, Any]) -> list[str]:
    feedback = []
    for name in ("leakage", "factuality"):
        review = review_round[name]
        if review.get("error"):
            feedback.append(f"{name} review failed to run")
            continue
        result = review.get("result") or {}
        feedback.extend(f"{name}: {reason}" for reason in result.get("reasons", []))
        if float(result.get("confidence", 0)) < 0.85:
            feedback.append(f"{name}: reviewer confidence was below 0.85")
    return feedback


def process_item(
    item: dict[str, Any],
    args: argparse.Namespace,
    api_key: str,
) -> dict[str, Any]:
    client = OpenAI(api_key=api_key, base_url=args.base_url, timeout=120, max_retries=2)
    generation_prompt = (
        "Create a realistic issue report for the behavior supported by this private evidence. "
        "Return only the issue title and body.\n\n" + item["evidence"]
    )
    messages = [
        {"role": "system", "content": GENERATOR_SYSTEM_PROMPT},
        {"role": "user", "content": generation_prompt},
    ]
    drafts: list[dict[str, Any]] = []
    review_rounds: list[dict[str, Any]] = []
    rewrites_used = 0
    if item.get("fixed_candidate") is not None:
        candidate = item["fixed_candidate"]
        usage = None
    else:
        candidate, usage = call_model(
            client, args.generator_model, messages, args.max_output_tokens
        )
    findings = rule_findings(candidate, item["evidence"])
    drafts.append({"content": candidate, "rule_findings": findings, "usage": usage})

    while True:
        feedback: list[str] = []
        if findings["hard"]:
            status = "rejected_by_rules"
            feedback.extend(f"automatic rule: {flag}" for flag in findings["hard"])
        elif args.skip_reviewer:
            status = "rules_passed_unreviewed"
        else:
            review_round = {
                "leakage": run_reviewer(
                    client,
                    args.leakage_reviewer_model,
                    LEAKAGE_REVIEWER_SYSTEM_PROMPT,
                    patch_only(item["evidence"]),
                    candidate,
                ),
                "factuality": run_reviewer(
                    client,
                    args.factuality_reviewer_model,
                    FACTUALITY_REVIEWER_SYSTEM_PROMPT,
                    behavior_only(item),
                    candidate,
                ),
            }
            review_rounds.append(review_round)
            feedback.extend(review_feedback(review_round))
            if any(review_round[name].get("error") for name in ("leakage", "factuality")):
                status = "needs_review"
            elif all(
                reviewer_passed(review_round[name].get("result"))
                for name in ("leakage", "factuality")
            ):
                status = "accepted"
            elif any(
                (review_round[name].get("result") or {}).get("verdict") == "reject"
                for name in ("leakage", "factuality")
            ):
                status = "rejected_by_reviewers"
            else:
                status = "needs_review"

        if status in {"accepted", "rules_passed_unreviewed"}:
            break
        if rewrites_used >= args.max_rewrites:
            break
        if findings["warnings"]:
            feedback.extend(f"automatic warning: {flag}" for flag in findings["warnings"])
        rewrite_prompt = (
            "Rewrite the issue to address every quality-gate finding below. Do not "
            "repeat hidden identifiers, implementation mechanisms, or unsupported "
            "effects. Keep only a complete, evidence-backed external reproduction.\n- "
            + "\n- ".join(feedback)
            + "\nReturn only the revised title and body."
        )
        candidate, usage = call_model(
            client,
            args.generator_model,
            messages
            + [
                {"role": "assistant", "content": candidate},
                {"role": "user", "content": rewrite_prompt},
            ],
            args.max_output_tokens,
        )
        rewrites_used += 1
        findings = rule_findings(candidate, item["evidence"])
        drafts.append({"content": candidate, "rule_findings": findings, "usage": usage})

    return {
        "instance_id": item["instance_id"],
        "repo": item.get("repo"),
        "strategy": item.get("strategy"),
        "generator_model": args.generator_model,
        "leakage_reviewer_model": None if args.skip_reviewer else args.leakage_reviewer_model,
        "factuality_reviewer_model": None if args.skip_reviewer else args.factuality_reviewer_model,
        "generation_attempts": len(drafts),
        "drafts": drafts,
        "problem_statement": candidate if status == "accepted" else "",
        "candidate_problem_statement": candidate,
        "rule_findings": findings,
        "review_rounds": review_rounds,
        "review_status": status,
        "selected_failure_tests": item.get("selected_failure_tests", []),
        "FAIL_TO_PASS": item.get("FAIL_TO_PASS", []),
        "PASS_TO_PASS_count": item.get("PASS_TO_PASS_count"),
    }


def main() -> None:
    args = parse_args()
    if args.overwrite and args.resume:
        raise ValueError("--overwrite and --resume are mutually exclusive")
    if args.prepare_only and args.resume:
        raise ValueError("--resume is only supported for API generation/review output")
    if args.output.exists() and not (args.overwrite or args.resume):
        raise RuntimeError(f"Refusing to overwrite existing output: {args.output}")
    if args.workers < 1:
        raise ValueError("--workers must be at least 1")

    if args.prepared_input:
        items = load_jsonl(args.prepared_input)
    else:
        if not args.dataset or not args.validation_dir:
            raise ValueError("dataset and validation_dir are required without --prepared-input")
        rows = load_jsonl(args.dataset)
        if args.limit is not None:
            rows = rows[: args.limit]
        items = []
        for index, row in enumerate(rows, 1):
            print(f"preparing {index}/{len(rows)} {row['instance_id']}", flush=True)
            items.append(prepare_item(row, args.max_failing_tests))

    if args.candidate_input:
        candidates = {
            row["instance_id"]: row.get("candidate_problem_statement", row.get("problem_statement"))
            for row in load_records(args.candidate_input)
        }
        missing = [item["instance_id"] for item in items if not candidates.get(item["instance_id"])]
        if missing:
            raise ValueError(f"Candidate text is missing for {len(missing)} prepared items")
        for item in items:
            item["fixed_candidate"] = candidates[item["instance_id"]]

    completed_ids: set[str] = set()
    if args.resume and args.output.exists():
        completed_ids = {row["instance_id"] for row in load_jsonl(args.output)}
        items = [item for item in items if item["instance_id"] not in completed_ids]
        print(f"resuming after {len(completed_ids)} completed items", flush=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.prepare_only:
        with args.output.open("w", encoding="utf-8", newline="\n") as handle:
            for item in items:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"wrote {len(items)} prepared inputs to {args.output}")
        return

    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is not set")

    output_mode = "a" if args.resume else "w"
    with args.output.open(output_mode, encoding="utf-8", newline="\n") as handle:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            results = executor.map(lambda item: process_item(item, args, api_key), items)
            for index, result in enumerate(results, 1):
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
                handle.flush()
                print(
                    f"completed {index}/{len(items)} {result['instance_id']} "
                    f"status={result['review_status']}",
                    flush=True,
                )


if __name__ == "__main__":
    main()
