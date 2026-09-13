import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "check-task-usability.py"
SPEC = importlib.util.spec_from_file_location("check_task_usability", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_extracts_python_fence():
    blocks = MODULE.extract_python_blocks(
        "Example:\n```python\nprint('hello')\n```\n"
    )
    assert blocks == ["print('hello')"]


def test_reports_python_syntax_error_without_rejecting():
    result = MODULE.check_row(
        {
            "instance_id": "example",
            "problem_statement": "```python\n>>> value = 1\n```",
        },
        execute=False,
        timeout=1,
    )
    assert result["status"] == "checked_with_warnings"
    assert "syntax error" in result["warnings"][0]


def test_missing_reproduction_is_warning_only():
    result = MODULE.check_row(
        {
            "instance_id": "example",
            "problem_statement": "Calling the function returns the wrong value.",
        },
        execute=False,
        timeout=1,
    )
    assert result["status"] == "checked_with_warnings"
    assert result["warnings"] == ["no fenced Python reproduction"]
