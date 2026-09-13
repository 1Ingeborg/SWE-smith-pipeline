#!/usr/bin/env python3
"""Fail-closed post-generation audit for SWE-smith problem statements.

The fast path is deterministic and suitable for every generated row.  An
optional single LLM call is reserved for rows whose rule findings are ambiguous.
The auditor never rewrites a candidate and never silently promotes rejected
content into the accepted dataset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any


HARD_PATTERNS: dict[str, str] = {
    "private_artifact_reference": (
        r"\b(?:patch(?:es)?|diff|mutation|SWE-smith|SWE-bench|synthetic dataset)\b"
    ),
    "test_or_ci_reference": (
        r"\b(?:pytest|unit tests?|test suite|test case|assertions?|CI pipeline)\b"
        r"|(?:^|[/\\])tests?[/\\]|\btest_[A-Za-z0-9_]+\b"
    ),
    "implementation_change_disclosure": (
        r"\b(?:was|were|has been|have been|got|is|are)?\s*"
        r"(?:removed|deleted|modified|changed|restored|re-added|left in place)\b"
        r"|\b(?:recent refactor|after (?:a|the) refactor|no longer searches?|"
        r"stopped (?:calling|checking|using|appending)|accidentally left)\b"
    ),
    "repair_instruction": (
        r"\b(?:fix by|should restore|should add back|needs? to restore|"
        r"reimplement|re-add|change the implementation|update the code to)\b"
    ),
    "root_cause_disclosure": (
        r"\b(?:conditional|branch|fallback|else)\s+(?:logic|block)\b"
        r"[^.\n]{0,80}\b(?:misplaced|inverted|reversed|swapped)\b"
        r"|\bearly return\b[^.\n]{0,80}\b(?:prevents|bypasses|skips)\b"
    ),
}

AMBIGUOUS_PATTERNS: dict[str, str] = {
    "causal_wording": r"\b(?:because|due to|root cause|caused by|originating from)\b",
    "internal_mechanism_wording": (
        r"\b(?:internal helper|implementation detail|wrong branch|missing assignment|"
        r"environment variable lookup|stack frames? (?:are|is) not searched)\b"
        r"|\b(?:cannot|fails? to|does not)\s+(?:traverse|walk|search)"
        r"\s+(?:the\s+)?[A-Za-z_-]+\s+chain\b"
    ),
    "suspicious_missing_wording": (
        r"\b(?:missing (?:method|function|assignment|lookup|branch)|"
        r"undefined internal (?:name|variable))\b"
    ),
}

IDENTIFIER_STOPWORDS = {
    "False", "None", "True", "and", "as", "assert", "async", "await",
    "break", "class", "continue", "def", "del", "elif", "else", "except",
    "finally", "for", "from", "global", "if", "import", "in", "is",
    "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "self",
    "try", "while", "with", "yield",
}

BALANCED_BLOCKING_CODES = {
    "private_artifact_reference",
    "test_or_ci_reference",
    "repair_instruction",
    "empty_problem_statement",
    "excessive_length",
}

REVIEW_SYSTEM_PROMPT = """You are a strict leakage gate for synthetic GitHub
issues. Compare the candidate issue with the private bug-inducing patch. Decide
whether the issue reveals a changed/deleted identifier, implementation change,
root cause, or likely repair. Observable symptoms, public API names needed for a
reproduction, exception classes, and actual output are allowed. Do not judge
writing style and do not rewrite the issue.

Return exactly one JSON object:
{"verdict":"accept|reject|needs_review","confidence":0.0,
 "reasons":["short reason"],"candidate_quotes":["exact risky phrase"]}

Use reject only when candidate text itself supplies a meaningful clue to the
private change or repair. Use needs_review when uncertain. Accept only when no
such clue is present.
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="JSON array or JSONL candidates")
    parser.add_argument("output", type=Path, help="JSONL audit records")
    parser.add_argument("--summary", type=Path, help="optional JSON summary")
    parser.add_argument("--accepted-output", type=Path, help="write accepted rows as JSONL")
    parser.add_argument(
        "--quarantine-output", type=Path, help="write rejected/unresolved rows as JSONL"
    )
    parser.add_argument("--candidate-field", default="problem_statement")
    parser.add_argument(
        "--policy",
        choices=("strict", "balanced"),
        default="strict",
        help=(
            "strict quarantines implementation/root-cause leakage; balanced "
            "records it as a warning and blocks only severe leakage"
        ),
    )
    parser.add_argument("--review-ambiguous", action="store_true")
    parser.add_argument("--review-model", default="qwen3.7-flash-2026-07-15")
    parser.add_argument(
        "--base-url", default="https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    parser.add_argument("--max-words", type=int, default=600)
    parser.add_argument("--fail-on-findings", action="store_true")
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


def removed_identifiers(patch: str) -> set[str]:
    removed_lines = []
    for line in patch.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            removed_lines.append(line[1:])
    names = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b", "\n".join(removed_lines)))
    return names - IDENTIFIER_STOPWORDS


def identifier_is_public_reproduction_usage(name: str, candidate: str) -> bool:
    """Recognize common public/example uses that should not trigger review alone."""
    if name.isupper():
        return True
    if re.search(rf"\bself\.{re.escape(name)}\b", candidate):
        return True
    if re.search(rf"\b{re.escape(name)}\s*=", candidate):
        return True
    return False


def identifier_mentions(candidate: str, patch: str) -> list[str]:
    return sorted(
        name
        for name in removed_identifiers(patch)
        if ("_" in name or any(character.isupper() for character in name[1:]))
        and re.search(rf"\b{re.escape(name)}\b", candidate)
        and not identifier_is_public_reproduction_usage(name, candidate)
    )


def regex_findings(candidate: str, patterns: dict[str, str]) -> list[dict[str, str]]:
    findings = []
    for code, pattern in patterns.items():
        match = re.search(pattern, candidate, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            findings.append({"code": code, "quote": match.group(0)})
    return findings


def deterministic_audit(
    row: dict[str, Any],
    candidate_field: str,
    max_words: int,
    policy: str = "strict",
) -> dict[str, Any]:
    candidate = row.get(candidate_field) or ""
    patch = row.get("patch") or ""
    hard = regex_findings(candidate, HARD_PATTERNS)
    ambiguous = regex_findings(candidate, AMBIGUOUS_PATTERNS)
    words = len(re.findall(r"\b\w+\b", candidate))

    if not candidate.strip():
        hard.append({"code": "empty_problem_statement", "quote": ""})
    if words > max_words:
        hard.append({"code": "excessive_length", "quote": f"{words} words"})

    changed_names = identifier_mentions(candidate, patch)
    if changed_names:
        ambiguous.append(
            {
                "code": "removed_identifier_mentioned",
                "quote": ", ".join(changed_names[:10]),
            }
        )

    # A changed identifier plus causal/change language is sufficiently concrete
    # to reject without spending an LLM call.
    hard_codes = {item["code"] for item in hard}
    ambiguous_codes = {item["code"] for item in ambiguous}
    if changed_names and (
        "implementation_change_disclosure" in hard_codes
        or "causal_wording" in ambiguous_codes
        or "internal_mechanism_wording" in ambiguous_codes
    ):
        hard.append(
            {
                "code": "changed_identifier_with_causal_or_change_clue",
                "quote": ", ".join(changed_names[:10]),
            }
        )

    warnings = list(ambiguous)
    if policy == "balanced":
        blocking = [
            finding
            for finding in hard
            if finding["code"] in BALANCED_BLOCKING_CODES
        ]
        warnings.extend(
            finding
            for finding in hard
            if finding["code"] not in BALANCED_BLOCKING_CODES
        )
        hard = blocking
        if hard:
            status = "reject"
        elif warnings:
            status = "accept_with_warnings"
        else:
            status = "accept"
    elif hard:
        status = "reject"
    elif ambiguous:
        status = "needs_semantic_review"
    else:
        status = "accept"

    return {
        "instance_id": row.get("instance_id"),
        "candidate_sha256": hashlib.sha256(candidate.encode("utf-8")).hexdigest(),
        "word_count": words,
        "deterministic_status": status,
        "hard_findings": hard,
        "ambiguous_findings": ambiguous,
        "warnings": warnings,
        "semantic_review": None,
        "final_status": status,
    }


def parse_review_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    result = json.loads(match.group(0) if match else cleaned)
    if result.get("verdict") not in {"accept", "reject", "needs_review"}:
        raise ValueError(f"invalid verdict: {result.get('verdict')!r}")
    return result


def semantic_review(client: Any, model: str, patch: str, candidate: str) -> dict[str, Any]:
    response = client.chat.completions.create(
        model=model,
        temperature=0,
        max_tokens=400,
        extra_body={"enable_thinking": False},
        messages=[
            {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "<private_patch>\n" + patch + "\n</private_patch>\n\n"
                    "<candidate_issue>\n" + candidate + "\n</candidate_issue>"
                ),
            },
        ],
    )
    result = parse_review_json(response.choices[0].message.content or "")
    usage = response.usage
    result["usage"] = {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
    }
    return result


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    args = parse_args()
    rows = load_records(args.input)
    audits = [
        deterministic_audit(
            row, args.candidate_field, args.max_words, policy=args.policy
        )
        for row in rows
    ]

    review_count = 0
    if args.policy == "strict" and args.review_ambiguous and any(
        item["final_status"] == "needs_semantic_review" for item in audits
    ):
        from openai import OpenAI

        api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("DASHSCOPE_API_KEY is required for --review-ambiguous")
        client = OpenAI(api_key=api_key, base_url=args.base_url, timeout=120, max_retries=2)
        for row, audit in zip(rows, audits):
            if audit["final_status"] != "needs_semantic_review":
                continue
            review_count += 1
            try:
                review = semantic_review(
                    client,
                    args.review_model,
                    row.get("patch") or "",
                    row.get(args.candidate_field) or "",
                )
                audit["semantic_review"] = review
                audit["final_status"] = review["verdict"]
            except Exception as error:
                audit["semantic_review"] = {
                    "error": f"{type(error).__name__}: {error}"
                }
                audit["final_status"] = "needs_review"

    accepted_statuses = {"accept", "accept_with_warnings"}
    counts = Counter(item["final_status"] for item in audits)
    summary = {
        "input": str(args.input),
        "total": len(audits),
        "counts": dict(sorted(counts.items())),
        "semantic_review_calls": review_count,
        "accepted_instance_ids": [
            item["instance_id"]
            for item in audits
            if item["final_status"] in accepted_statuses
        ],
        "quarantined_instance_ids": [
            item["instance_id"]
            for item in audits
            if item["final_status"] not in accepted_statuses
        ],
    }

    write_jsonl(args.output, audits)
    if args.accepted_output:
        write_jsonl(
            args.accepted_output,
            [
                row
                for row, audit in zip(rows, audits)
                if audit["final_status"] in accepted_statuses
            ],
        )
    if args.quarantine_output:
        write_jsonl(
            args.quarantine_output,
            [
                row
                for row, audit in zip(rows, audits)
                if audit["final_status"] not in accepted_statuses
            ],
        )
    if args.summary:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        args.summary.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.fail_on_findings and any(
        item["final_status"] not in accepted_statuses for item in audits
    ):
        raise SystemExit(2)


if __name__ == "__main__":
    main()
