#!/usr/bin/env python3
"""Export evaluated Agent trajectories as checked, framework-native SFT data."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


LAB_ROOT = Path(__file__).resolve().parents[3]


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def evaluation_ids(eval_dir: Path, ids: list[str]) -> set[str]:
    summary = read_json(eval_dir / "report.json")
    if summary.get("gold") is not False or summary.get("selected_tasks") != len(ids):
        raise ValueError("Model evaluation does not match the selected Agent tasks")
    if summary.get("evaluated") != len(ids) or summary.get("missing_predictions") or summary.get("unexpected_predictions"):
        raise ValueError("Model evaluation is incomplete")
    resolved = set()
    for iid in ids:
        report = read_json(eval_dir / iid / "report.json")
        if report.get("status") not in {"completed", "timeout", "patch_apply_error", "empty_prediction"}:
            raise ValueError(f"Evaluation did not complete for {iid}")
        if report.get("resolved") is True:
            resolved.add(iid)
    if summary.get("resolved") != len(resolved):
        raise ValueError("Evaluation summary and per-task resolved IDs disagree")
    return resolved


def export_swe(rollout_dir: Path, eval_dir: Path, ids: list[str]) -> list[dict]:
    from swesmith.train.traj_mgr.collect_trajs import process_single_trajectory
    from swesmith.train.traj_mgr.utils import transform_traj_xml

    rows = []
    for iid in ids:
        result = process_single_trajectory(iid, rollout_dir, eval_dir, transform_traj_xml)
        if result is None:
            raise ValueError(f"Cannot convert SWE-agent trajectory: {iid}")
        rows.append(result[1])
    return rows


def export_mini(rollout_dir: Path, eval_dir: Path, instances: Path,
                native_config: Path, model: str, work_dir: Path) -> list[dict]:
    converter = LAB_ROOT / "src/swesmith_lab/agent/mini_sft_converter.py"
    subprocess.run([sys.executable, str(converter), "--traj-dir", str(rollout_dir),
                    "--eval-dir", str(eval_dir), "--instances", str(instances),
                    "--agent-config", str(native_config), "--out-dir", str(work_dir),
                    "--model", model, "--style", "native"], cwd=LAB_ROOT, check=True)
    return read_jsonl(work_dir / "all.jsonl")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--framework", choices=("swe-agent", "mini-swe-agent"), required=True)
    parser.add_argument("--rollout-dir", type=Path, required=True)
    parser.add_argument("--eval-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--instances", type=Path, required=True)
    parser.add_argument("--native-config", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.framework == "mini-swe-agent" and args.native_config is None:
        parser.error("mini-swe-agent requires --native-config")

    instances = read_jsonl(args.instances)
    if args.limit is not None:
        instances = instances[:args.limit]
    ids = [row["instance_id"] for row in instances]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("Prepared public instances are empty or duplicated")
    resolved_ids = evaluation_ids(args.eval_dir, ids)
    predictions = read_json(args.rollout_dir / "preds.json")
    if set(predictions) != set(ids):
        raise ValueError("Prediction IDs differ from selected public tasks")

    args.output_dir.parent.mkdir(parents=True, exist_ok=True)
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise FileExistsError(f"SFT output already exists: {args.output_dir}")
    with tempfile.TemporaryDirectory(prefix=".sft-", dir=args.output_dir.parent) as temporary:
        work = Path(temporary)
        if args.framework == "swe-agent":
            rows = export_swe(args.rollout_dir, args.eval_dir, ids)
        else:
            rows = export_mini(args.rollout_dir, args.eval_dir, args.instances,
                               args.native_config, args.model, work)
        by_id = {row["instance_id"]: row for row in rows}
        if len(rows) != len(ids) or set(by_id) != set(ids):
            raise ValueError(f"SFT trajectory IDs do not match evaluation; got {len(rows)}/{len(ids)}")
        for iid in ids:
            row = by_id[iid]
            if row.get("resolved") is not (iid in resolved_ids):
                raise ValueError(f"Resolved flag disagrees with evaluation: {iid}")
            if not isinstance(row.get("messages"), list) or not row["messages"]:
                raise ValueError(f"Empty SFT conversation: {iid}")
        rows = [by_id[iid] for iid in sorted(ids)]
        resolved = [row for row in rows if row["resolved"]]
        if args.framework == "mini-swe-agent" and any(not str(row.get("patch") or "").strip() for row in resolved):
            raise ValueError("Resolved mini trajectory has an empty patch")
        write_jsonl(work / "all.jsonl", rows)
        write_jsonl(work / "resolved.jsonl", resolved)
        write_jsonl(work / "resolved_chat.jsonl", [{"messages": row["messages"]} for row in resolved])
        dataset_name = f"swe_smith_{args.framework.replace('-', '_')}_resolved"
        (work / "dataset_info.json").write_text(json.dumps({dataset_name: {
            "file_name": "resolved_chat.jsonl", "formatting": "sharegpt",
            "columns": {"messages": "messages"},
            "tags": {"role_tag": "role", "content_tag": "content", "user_tag": "user",
                     "assistant_tag": "assistant", "system_tag": "system"},
        }}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest = {
            "schema_version": 1,
            "framework": args.framework,
            "style": "xml" if args.framework == "swe-agent" else "native",
            "source_ids": sorted(ids),
            "evaluation_resolved": len(resolved_ids),
            "converted_all": len(rows),
            "converted_resolved": len(resolved),
            "rollout_dir": str(args.rollout_dir.resolve()),
            "evaluation_dir": str(args.eval_dir.resolve()),
            "files": ["all.jsonl", "resolved.jsonl", "resolved_chat.jsonl", "dataset_info.json"],
        }
        (work / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.output_dir.exists():
            args.output_dir.rmdir()  # An empty directory only; never replace prior data.
        work.rename(args.output_dir)
    print(f"Exported {len(rows)} trajectories, {len(resolved)} resolved: {args.output_dir}")


if __name__ == "__main__":
    main()
