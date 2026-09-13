import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "audit-problem-statements.py"
SPEC = importlib.util.spec_from_file_location("audit_problem_statements", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def audit(candidate: str, patch: str) -> dict:
    return MODULE.deterministic_audit(
        {"instance_id": "example", "problem_statement": candidate, "patch": patch},
        "problem_statement",
        600,
    )


def test_rejects_change_disclosure_with_private_identifier() -> None:
    result = audit(
        "After a recent refactor, trace_modules_str was removed and left undefined.",
        "-trace_modules_str = os.environ.get('MODULES')",
    )
    assert result["final_status"] == "reject"
    assert any(
        finding["code"] == "changed_identifier_with_causal_or_change_clue"
        for finding in result["hard_findings"]
    )


def test_accepts_observable_public_api_reproduction() -> None:
    result = audit(
        """Calling default_code_filter raises NameError.
```python
os.environ.pop("MONKEYTYPE_TRACE_MODULES", None)
default_code_filter(code)
```""",
        "-trace_modules_str = os.environ.get('MONKEYTYPE_TRACE_MODULES')",
    )
    assert result["final_status"] == "accept"


def test_accepts_keyword_and_local_attribute_in_reproduction() -> None:
    result = audit(
        """The trace collection is empty.
```python
self.traces = []
with trace_calls(logger, max_typed_dict_size=0):
    run()
```""",
        "-self.traces.append(trace)\n-max_typed_dict_size = config.limit",
    )
    assert result["final_status"] == "accept"


def test_routes_ambiguous_causal_claim_to_semantic_review() -> None:
    result = audit(
        "The error occurs because an internal helper returns the wrong value.",
        "-return calculate_value()",
    )
    assert result["final_status"] == "needs_semantic_review"


def test_rejects_test_framework_leakage() -> None:
    result = audit("The pytest test suite fails.", "-return value")
    assert result["final_status"] == "reject"


def test_rejects_explicit_conditional_root_cause() -> None:
    result = audit(
        "The conditional logic is misplaced, and the early return prevents parsing.",
        "-if configured:\n-    parse_modules()",
    )
    assert result["final_status"] == "reject"
    assert any(
        finding["code"] == "root_cause_disclosure"
        for finding in result["hard_findings"]
    )


def test_rejects_private_identifier_with_chain_mechanism() -> None:
    result = audit(
        "The tracer cannot traverse the wrapper chain through __wrapped__.",
        "-while hasattr(func, '__wrapped__'):\n-    func = func.__wrapped__",
    )
    assert result["final_status"] == "reject"
    assert any(
        finding["code"] == "changed_identifier_with_causal_or_change_clue"
        for finding in result["hard_findings"]
    )
