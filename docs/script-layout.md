# 脚本目录与迁移对照

`scripts/` 只保留 6 个日常入口：`bootstrap-python.sh`、`check-install.sh`、
`run-task-generation.py`、`run-task-generation.sh`、`run-reviewed-issuegen.py`
和 `run-agent.sh`。两个 Shell 入口会定位本机 core 虚拟环境并转发参数。
从仓库根目录执行。

原 `scripts/` 中的内部 Python 文件移入 `src/swesmith_lab/`：

| 原文件名 | 新路径 |
| --- | --- |
| `build-swesmith-repo-catalog.py` | `pipeline/catalog.py` |
| `combine-task-jsonl.py` | `pipeline/combine.py` |
| `export-valid-local.py` | `pipeline/export.py` |
| `prepare-combine-candidates.py` | `pipeline/prepare_combine.py` |
| `run-validation.py` | `pipeline/validation.py` |
| `select-diverse-candidates.py` | `pipeline/select.py` |
| `audit-problem-statements.py` | `issuegen/audit.py` |
| `check-task-usability.py` | `issuegen/usability.py` |
| `generate-problem-statements.py` | `issuegen/generate.py` |
| `issuegen-compat-launcher.py` | `issuegen/official_launcher.py` |
| `run-task-production.py` | `issuegen/official.py` |
| `build-tasks-for-agent.py` | `agent/build_tasks.py` |
| `evaluate-agent-predictions.py` | `agent/evaluate.py` |
| `prepare-agent-pilot.py` | `agent/prepare.py` |
| `run-agent-pilot.py` | `agent/run.py` |

表中“新路径”均以 `src/swesmith_lab/` 为前缀。
新实验的统一目录入口内部实现在 `src/swesmith_lab/agent/experiment.py`；
历史 `run-agent-rollout.sh` 已移至 `tools/legacy/`，仅用于原有 `/data/results` 运行目录。
旧的 `run-pipeline.sh`、`run-multirepo-pipeline.py` 和 `run-agent-experiment.sh`
文件名不再保留；Agent 前的数据生产入口称为“任务生成”。

宿主机安装与检查脚本 `bootstrap-host.sh`、`bootstrap-swe-agent.sh`、
`check-host.sh` 和 `link-legacy-paths.sh` 移入 `tools/setup/`；重试准备与合并、
`patch-litellm-cost-map.py`、`select-reviewed-smoke.py`、`launch-run.sh` 移入
`tools/maintenance/`；2026-09-18 的独立实验脚本 `run-problem-statements.sh`
移入 `tools/legacy/`。

这次只调整代码和调用路径，不移动数据集、镜像、模型、轨迹或结果目录；已有 run-id
和配置快照保持原状。迁移前的 25 个脚本保存在
`runs/script-layout-backup-20260927/scripts/`，如需排查历史路径可从那里恢复。
