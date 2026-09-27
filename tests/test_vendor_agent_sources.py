from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_agent_sources_and_licenses_are_vendored():
    swe_agent = ROOT / "vendor" / "SWE-agent"
    mini = ROOT / "vendor" / "mini-swe-agent"
    assert (swe_agent / "pyproject.toml").is_file()
    assert (swe_agent / "LICENSE").is_file()
    assert (swe_agent / "sweagent" / "run" / "run_batch.py").is_file()
    assert (mini / "pyproject.toml").is_file()
    assert (mini / "LICENSE.md").is_file()
    assert '__version__ = "2.4.6"' in (mini / "src" / "minisweagent" / "__init__.py").read_text(encoding="utf-8")
