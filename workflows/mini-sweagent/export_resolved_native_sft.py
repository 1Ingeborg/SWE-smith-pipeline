#!/usr/bin/env python3
"""Export resolved mini-SWE-agent rollouts as OpenAI/ShareGPT SFT data."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import Counter
from pathlib import Path


DATASET_NAME = "swe_smith_mini_native_resolved"


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_messages(instance_id: str, messages: list[dict]) -> None:
    if len(messages) < 3 or len(messages) % 2 != 1:
        raise ValueError(f"{instance_id}: invalid message count")
    if [message.get("role") for message in messages[:2]] != ["system", "user"]:
        raise ValueError(f"{instance_id}: missing system/user prefix")
    for index, message in enumerate(messages):
        expected = "system" if index == 0 else "user" if index % 2 else "assistant"
        if message.get("role") != expected:
            raise ValueError(f"{instance_id}: expected {expected} at message {index}")
        if not isinstance(message.get("content"), str) or not message["content"].strip():
            raise ValueError(f"{instance_id}: empty content at message {index}")
        if expected == "assistant" and "```mswea_bash_command" not in message["content"]:
            raise ValueError(f"{instance_id}: assistant message {index} lacks mini action marker")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    source_rows = read_jsonl(args.source)
    if not source_rows:
        raise ValueError("source contains no SFT rows")
    evaluation = json.loads(args.evaluation.read_text(encoding="utf-8"))
    expected = int(evaluation["resolved"])
    if len(source_rows) != expected:
        raise ValueError(f"source has {len(source_rows)} rows but evaluation resolved {expected}")

    ids: set[str] = set()
    role_counts: Counter[str] = Counter()
    conversation_chars: list[int] = []
    sft_rows: list[dict] = []
    index_rows: list[dict] = []
    for position, source in enumerate(source_rows):
        instance_id = source["instance_id"]
        if instance_id in ids:
            raise ValueError(f"duplicate instance_id: {instance_id}")
        ids.add(instance_id)
        if source.get("resolved") is not True or not str(source.get("patch") or "").strip():
            raise ValueError(f"{instance_id}: not a resolved trajectory with a patch")
        messages = source["messages"]
        validate_messages(instance_id, messages)
        role_counts.update(message["role"] for message in messages)
        conversation_chars.append(sum(len(message["content"]) for message in messages))
        sft_rows.append({"messages": messages})
        index_rows.append(
            {
                "row": position,
                "instance_id": instance_id,
                "traj_id": source.get("traj_id"),
                "assistant_turns": sum(message["role"] == "assistant" for message in messages),
                "conversation_chars": conversation_chars[-1],
            }
        )

    args.out_dir.mkdir(parents=True, exist_ok=True)
    data_path = args.out_dir / "resolved_chat.jsonl"
    index_path = args.out_dir / "sample_index.jsonl"
    write_jsonl(data_path, sft_rows)
    write_jsonl(index_path, index_rows)

    dataset_info = {
        DATASET_NAME: {
            "file_name": data_path.name,
            "formatting": "sharegpt",
            "columns": {"messages": "messages"},
            "tags": {
                "role_tag": "role",
                "content_tag": "content",
                "user_tag": "user",
                "assistant_tag": "assistant",
                "system_tag": "system",
            },
        }
    }
    (args.out_dir / "dataset_info.json").write_text(
        json.dumps(dataset_info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lengths = sorted(conversation_chars)
    manifest = {
        "schema_version": 1,
        "dataset_name": DATASET_NAME,
        "format": "OpenAI messages / LLaMA-Factory ShareGPT",
        "style": "mini-swe-agent native bash code blocks",
        "source": str(args.source.resolve()),
        "evaluation": str(args.evaluation.resolve()),
        "rows": len(sft_rows),
        "unique_instance_ids": len(ids),
        "resolved_in_evaluation": expected,
        "role_counts": dict(role_counts),
        "conversation_chars": {
            "min": lengths[0],
            "median": statistics.median(lengths),
            "p90": lengths[int((len(lengths) - 1) * 0.90)],
            "p95": lengths[int((len(lengths) - 1) * 0.95)],
            "max": lengths[-1],
        },
        "data_file": data_path.name,
        "data_sha256": sha256(data_path),
        "index_file": index_path.name,
        "full_trajectories_preserved": True,
        "sequence_truncation_applied": False,
    }
    (args.out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
