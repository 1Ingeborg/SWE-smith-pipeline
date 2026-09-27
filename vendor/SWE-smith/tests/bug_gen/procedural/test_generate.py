import json
from types import SimpleNamespace

from swesmith.bug_gen.procedural.generate import _process_candidate


def test_process_candidate_logs_and_skips_modifier_exception(tmp_path):
    candidate = SimpleNamespace(
        file_path="package/module.py",
        line_start=10,
        line_end=12,
    )

    class BrokenModifier:
        name = "broken_modifier"

        @staticmethod
        def modify(_candidate):
            raise RuntimeError("bad transformation")

    result = _process_candidate(candidate, BrokenModifier(), tmp_path, "owner__repo")

    assert result is False
    rows = [
        json.loads(line)
        for line in (tmp_path / "candidate_errors.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert rows[0]["repo"] == "owner__repo"
    assert rows[0]["modifier"] == "broken_modifier"
    assert rows[0]["file_path"] == "package/module.py"
    assert rows[0]["exception_type"] == "RuntimeError"
    assert rows[0]["exception"] == "bad transformation"
