"""Summarize mini-SWE-agent trajectories without printing full prompt contents."""

import json
import re
import sys
from collections import Counter
from pathlib import Path


root = Path(sys.argv[1])
for path in sorted(root.glob("*/temp-*/*/*.traj.json")):
    data = json.loads(path.read_text(encoding="utf-8"))
    info = data.get("info", {})
    commands = []
    loop_count = 0
    first_loop = None
    first_loop_tokens = None
    format_errors = 0
    prompt_tokens = []
    assistant_calls = 0
    for message in data.get("messages", []):
        if message.get("role") != "assistant":
            continue
        assistant_calls += 1
        extra = message.get("extra") or {}
        if extra.get("format_error"):
            format_errors += 1
        usage = (extra.get("response") or {}).get("usage") or {}
        loop = extra.get("loop_intervention")
        if loop:
            loop_count += 1
            if first_loop is None:
                first_loop = assistant_calls
                first_loop_tokens = usage.get("prompt_tokens")
            actions = loop.get("original_actions", [])
        else:
            actions = extra.get("actions", [])
        commands.extend(action.get("command", "") for action in actions)
        if usage.get("prompt_tokens") is not None:
            prompt_tokens.append(usage["prompt_tokens"])

    counts = Counter(commands)
    source_write = sum(
        bool(re.search(r"\b(sed\s+-i|apply_patch|git\s+apply|perl\s+-pi|patch\s+-p)\b", c))
        for c in commands
    )
    tests = sum(bool(re.search(r"\b(pytest|tox|unittest|nosetests)\b", c)) for c in commands)
    diffs = sum(bool(re.search(r"\bgit\s+diff\b", c)) for c in commands)
    submit = sum("COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in c for c in commands)
    print(
        f"{path.parts[-4]}/{path.parts[-3]} {path.parts[-2]}: "
        f"status={info.get('exit_status')} calls={info.get('model_stats', {}).get('api_calls')} "
        f"loops={loop_count} first_loop_call={first_loop} "
        f"first_loop_prompt_tokens={first_loop_tokens} format_errors={format_errors} "
        f"source_write={source_write} tests={tests} git_diff={diffs} submit={submit} "
        f"prompt_tokens_last={prompt_tokens[-1] if prompt_tokens else '?'}"
    )
    for command, count in counts.most_common(3):
        print(f"  repeated {count}x: {command[:150]!r}")
    if commands:
        print("  last actions:", " | ".join(repr(c[:110]) for c in commands[-3:]))
