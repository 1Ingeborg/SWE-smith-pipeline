#!/usr/bin/env python3
"""Convert evaluated mini-SWE-agent trajectories to SWE-agent XML SFT JSONL."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import yaml
from jinja2 import StrictUndefined, Template


ACTION_BLOCK = re.compile(r"```mswea_bash_command\s*\n.*?\n```", re.DOTALL)


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)


def xml_action(thought: str, command: str) -> str:
    action = f"<function=bash>\n<parameter=command>{command}</parameter>\n</function>"
    return f"{thought}\n\n{action}".strip()


def convert_messages_xml(
    trajectory: dict,
    *,
    system_prompt: str,
    instance_prompt: str,
) -> tuple[list[dict], Counter]:
    source = trajectory.get("messages", [])
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": instance_prompt},
    ]
    stats: Counter = Counter()
    index = 2
    while index < len(source):
        message = source[index]
        role = message.get("role")
        if role == "exit":
            break
        if role != "assistant":
            index += 1
            continue

        actions = message.get("extra", {}).get("actions", [])
        if len(actions) != 1 or not isinstance(actions[0].get("command"), str):
            stats["invalid_assistant_turns_dropped"] += 1
            index += 1
            if index < len(source) and source[index].get("role") == "user" and "Format error:" in str(source[index].get("content", "")):
                stats["format_error_feedback_dropped"] += 1
                index += 1
            continue

        content = str(message.get("content", ""))
        thought = ACTION_BLOCK.sub("", content).strip()
        command = actions[0]["command"]
        messages.append({"role": "assistant", "content": xml_action(thought, command)})
        stats["valid_actions"] += 1
        index += 1

        if index < len(source) and source[index].get("role") == "user":
            observation = source[index]
            raw_output = observation.get("extra", {}).get("raw_output")
            if isinstance(raw_output, str):
                rendered = "OBSERVATION:\n" + raw_output
            else:
                rendered = str(observation.get("content", ""))
                if "Format error:" in rendered:
                    stats["format_error_feedback_dropped"] += 1
                    index += 1
                    continue
                if not rendered.startswith("OBSERVATION:"):
                    rendered = "OBSERVATION:\n" + rendered
            messages.append({"role": "user", "content": rendered})
            index += 1
    return messages, stats


def convert_messages_native(trajectory: dict) -> tuple[list[dict], Counter]:
    source = trajectory.get("messages", [])
    if len(source) < 2 or source[0].get("role") != "system" or source[1].get("role") != "user":
        raise ValueError("mini trajectory has no system/user prefix")
    messages = [
        {"role": "system", "content": str(source[0].get("content", ""))},
        {"role": "user", "content": str(source[1].get("content", ""))},
    ]
    stats: Counter = Counter()
    index = 2
    while index < len(source):
        message = source[index]
        role = message.get("role")
        if role == "exit":
            break
        if role != "assistant":
            index += 1
            continue
        actions = message.get("extra", {}).get("actions", [])
        content = str(message.get("content", ""))
        if len(actions) != 1 or not isinstance(actions[0].get("command"), str) or "```mswea_bash_command" not in content:
            stats["invalid_assistant_turns_dropped"] += 1
            index += 1
            if index < len(source) and source[index].get("role") == "user" and "Format error:" in str(source[index].get("content", "")):
                stats["format_error_feedback_dropped"] += 1
                index += 1
            continue
        messages.append({"role": "assistant", "content": content})
        stats["valid_actions"] += 1
        index += 1
        if index < len(source) and source[index].get("role") == "user":
            observation = str(source[index].get("content", ""))
            if "Format error:" in observation:
                stats["format_error_feedback_dropped"] += 1
                index += 1
                continue
            messages.append({"role": "user", "content": observation})
            index += 1
    return messages, stats


def validate_row(row: dict, *, style: str) -> None:
    messages = row["messages"]
    if len(messages) < 4 or messages[0]["role"] != "system" or messages[1]["role"] != "user":
        raise ValueError(f"{row['instance_id']}: invalid message prefix or too few messages")
    for index, message in enumerate(messages):
        if message.get("role") not in {"system", "user", "assistant"}:
            raise ValueError(f"{row['instance_id']}: invalid role at {index}")
        if not isinstance(message.get("content"), str) or not message["content"].strip():
            raise ValueError(f"{row['instance_id']}: empty content at {index}")
        if message["role"] == "assistant":
            marker = "```mswea_bash_command" if style == "native" else "<function=bash>"
            if marker not in message["content"]:
                raise ValueError(f"{row['instance_id']}: assistant action has wrong style at {index}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traj-dir", type=Path, required=True)
    parser.add_argument("--eval-dir", type=Path, required=True)
    parser.add_argument("--instances", type=Path, required=True)
    parser.add_argument("--agent-config", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--model", default="deepseek/deepseek-flash")
    parser.add_argument("--style", choices=("native", "sweagent-xml"), default="native")
    args = parser.parse_args()

    instances = {row["instance_id"]: row for row in read_jsonl(args.instances)}
    predictions = json.loads((args.traj_dir / "preds.json").read_text(encoding="utf-8"))
    system_prompt = None
    instance_template = None
    if args.style == "sweagent-xml":
        config = yaml.safe_load(args.agent_config.read_text(encoding="utf-8"))
        templates = config["agent"]["templates"]
        system_prompt = templates["system_template"]
        instance_template = Template(templates["instance_template"], undefined=StrictUndefined)
    run_hash = hashlib.sha256(str(args.traj_dir.resolve()).encode()).hexdigest()[:8]

    rows: list[dict] = []
    skipped: Counter = Counter()
    conversion_stats: Counter = Counter()
    for path in sorted(args.traj_dir.glob("*/*.traj.json")):
        instance_id = path.parent.name
        report_path = args.eval_dir / instance_id / "report.json"
        if instance_id not in instances:
            skipped["missing_instance"] += 1
            continue
        if not report_path.exists():
            skipped["missing_report"] += 1
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        prediction = predictions.get(instance_id, {})
        patch = prediction.get("model_patch") or ""
        trajectory = json.loads(path.read_text(encoding="utf-8"))
        if args.style == "native":
            messages, stats = convert_messages_native(trajectory)
        else:
            messages, stats = convert_messages_xml(
                trajectory,
                system_prompt=system_prompt,
                instance_prompt=instance_template.render(
                    working_dir="/testbed",
                    problem_statement=instances[instance_id]["problem_statement"],
                ),
            )
        conversion_stats.update(stats)
        row = {
            "messages": messages,
            "instance_id": instance_id,
            "resolved": bool(report.get("resolved", False)),
            "model": args.model,
            "traj_id": f"{instance_id}.mini-{run_hash}",
            "patch": patch,
            "exit_status": trajectory.get("info", {}).get("exit_status"),
        }
        try:
            validate_row(row, style=args.style)
        except ValueError:
            skipped["invalid_converted_row"] += 1
            continue
        rows.append(row)

    rows.sort(key=lambda row: row["instance_id"])
    resolved = [row for row in rows if row["resolved"]]
    all_path = args.out_dir / "all.jsonl"
    resolved_path = args.out_dir / "resolved.jsonl"
    write_jsonl(all_path, rows)
    write_jsonl(resolved_path, resolved)

    if len({row["instance_id"] for row in rows}) != len(rows):
        raise RuntimeError("Duplicate instance_id in converted output")
    manifest = {
        "schema_version": 1,
        "style": args.style,
        "trajectory_dir": str(args.traj_dir),
        "evaluation_dir": str(args.eval_dir),
        "source_trajectories": len(list(args.traj_dir.glob("*/*.traj.json"))),
        "converted_all": len(rows),
        "converted_resolved": len(resolved),
        "skipped": dict(sorted(skipped.items())),
        "conversion_stats": dict(sorted(conversion_stats.items())),
        "all_output": str(all_path),
        "resolved_output": str(resolved_path),
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
