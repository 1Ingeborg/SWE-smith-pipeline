import importlib.util
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "prepare-agent-pilot.py"
SPEC = importlib.util.spec_from_file_location("prepare_agent_pilot", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def make_task(repo, instance_id, strategy):
    return {
        "instance_id": instance_id,
        "repo": repo,
        "image_name": f"swebench/{repo}",
        "patch": "--- a/pkg.py\n+++ b/pkg.py\n@@ -1 +1 @@\n-good\n+bad\n",
        "FAIL_TO_PASS": ["tests/test_pkg.py::test_value"],
        "PASS_TO_PASS": ["tests/test_pkg.py::test_other"],
        "problem_statement": "The package returns an unexpected value.",
        "strategy": strategy,
        "rewrite": "private mutation output",
    }


def test_selection_is_balanced_diverse_and_deterministic():
    tasks = []
    for repo in ("owner__one.abc", "owner__two.def"):
        for index, strategy in enumerate(("const", "if", "call", "return")):
            tasks.append(make_task(repo, f"{repo}.{strategy}__{index}", strategy))

    first = MODULE.select_balanced_tasks(tasks, per_repository=3, seed=42)
    second = MODULE.select_balanced_tasks(tasks, per_repository=3, seed=42)

    assert [task["instance_id"] for task in first] == [
        task["instance_id"] for task in second
    ]
    assert MODULE.selection_summary(first)["repositories"] == {
        "owner__one.abc": 3,
        "owner__two.def": 3,
    }
    for repo in ("owner__one.abc", "owner__two.def"):
        strategies = {task["strategy"] for task in first if task["repo"] == repo}
        assert len(strategies) == 3


def test_agent_ids_and_public_row_do_not_leak_private_metadata():
    task = make_task(
        "owner__repo.abc",
        "owner__repo.abc.func_pm_op_change_const__secret",
        "func_pm_op_change_const",
    )
    selected = MODULE.select_balanced_tasks([task], per_repository=1, seed=42)
    selected_task = selected[0]
    agent_id = selected_task["_agent_instance_id"]
    public = MODULE.public_instance(selected_task, "swesmith-lab/agent-task:opaque")

    assert "owner" not in agent_id
    assert "change_const" not in agent_id
    assert set(public) == MODULE.PUBLIC_INSTANCE_KEYS
    rendered = repr(public)
    for secret in ("change_const", "secret", "private mutation output", "tests/test_pkg"):
        assert secret not in rendered


@pytest.mark.parametrize(
    "value",
    ["/absolute.py", "../escape.py", "pkg/../../escape.py", "", "."],
)
def test_safe_repo_path_rejects_unsafe_values(value):
    with pytest.raises(ValueError):
        MODULE.safe_repo_path(value)


def test_task_image_name_is_run_scoped_and_opaque():
    first = MODULE.task_image_name(
        "swesmith-lab/agent-task", "pilot-one", "agent-task-0001-abcdefghij"
    )
    second = MODULE.task_image_name(
        "swesmith-lab/agent-task", "pilot-two", "agent-task-0001-abcdefghij"
    )

    assert first != second
    assert first.startswith("swesmith-lab/agent-task:r")
    assert "pilot-one" not in first


def test_runtime_image_name_changes_with_agent_tool_dependencies():
    without_tools = MODULE.runtime_image_name(
        "swesmith-lab/agent-runtime", "sha256:1234567890abcdef", "swe-rex==1.4.0"
    )
    with_tools = MODULE.runtime_image_name(
        "swesmith-lab/agent-runtime",
        "sha256:1234567890abcdef",
        "swe-rex==1.4.0",
        ("tree-sitter==0.21.3", "tree-sitter-languages==1.10.2"),
    )

    assert without_tools != with_tools
    assert with_tools.endswith("-i1234567890abcdef")


def test_selection_requires_repository_quota():
    task = make_task("owner__repo.abc", "one", "const")
    with pytest.raises(ValueError, match="fewer than quota"):
        MODULE.select_balanced_tasks([task], per_repository=2, seed=42)
