#!/usr/bin/env python3
"""Maintenance tool: add DeepSeek models to LiteLLM's bundled cost map.

SWE-agent refuses to run a model LiteLLM cannot price while a cost limit is
set. LiteLLM tries to fetch the live cost map from raw.githubusercontent.com on
import and falls back to the copy bundled in the package when that times out —
which this host does, unpredictably, because the fetch takes upwards of 12
seconds and often just hangs. The bundled copy predates deepseek-flash, so every
task died with "This model isn't mapped yet".

Writing the entries into the bundled copy makes pricing deterministic and lets
the run set LITELLM_LOCAL_MODEL_COST_MAP=True, skipping the network entirely.

Prices are DeepSeek's standard (peak) rates, matching what the live LiteLLM map
carries; off-peak billing is half, so reported costs are a conservative ceiling.
Re-running this script is safe: entries are only added when missing, unless
--force is given.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

ENTRIES = {
    "deepseek-flash": {
        "max_tokens": 393216,
        "max_input_tokens": 1000000,
        "max_output_tokens": 393216,
        "input_cost_per_token": 3e-07,
        "output_cost_per_token": 1.2e-06,
        "cache_creation_input_token_cost": 0.0,
        "cache_read_input_token_cost": 6e-09,
        "input_cost_per_token_cache_hit": 6e-09,
        "litellm_provider": "deepseek",
        "mode": "chat",
        "supported_endpoints": ["/v1/chat/completions"],
        "supports_assistant_prefill": True,
        "supports_function_calling": True,
        "supports_native_streaming": True,
        "supports_parallel_function_calling": True,
        "supports_prompt_caching": True,
        "supports_reasoning": True,
        "supports_response_schema": True,
        "supports_system_messages": True,
        "supports_tool_choice": True,
        "source": "https://api-docs.deepseek.com/quick_start/pricing",
    },
}


def bundled_cost_map(python_executable: Path) -> Path:
    import subprocess

    code = (
        "import litellm, os, json;"
        "print(os.path.join(os.path.dirname(litellm.__file__),"
        "'model_prices_and_context_window_backup.json'))"
    )
    result = subprocess.run(
        [str(python_executable), "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={"LITELLM_LOCAL_MODEL_COST_MAP": "True", "PATH": "/usr/bin:/bin"},
    )
    return Path(result.stdout.strip())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python",
        type=Path,
        default=Path("/data/venvs/sweagent/bin/python"),
        help="Interpreter whose LiteLLM installation should be patched",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing entries")
    args = parser.parse_args()

    path = bundled_cost_map(args.python)
    if not path.is_file():
        raise SystemExit(f"Bundled cost map not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    added, skipped = [], []
    for name, entry in ENTRIES.items():
        if name in data and not args.force:
            skipped.append(name)
            continue
        data[name] = entry
        added.append(name)

    if not added:
        print(f"Nothing to do; already present: {', '.join(skipped)}")
        return

    backup = path.with_name(path.name + f".bak-{datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(path, backup)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
    temporary.replace(path)

    print(f"Cost map: {path}")
    print(f"Backup:   {backup}")
    print(f"Added:    {', '.join(added)}")
    if skipped:
        print(f"Kept:     {', '.join(skipped)}")


if __name__ == "__main__":
    main()
