# SWE-smith pipeline

用于复现 SWE-smith 数据构造、清洗、验证、训练数据整理和 bad case 迭代的实验工程。

本仓库保存实验脚本、配置及运行所需的 SWE-smith、SWE-agent、mini-swe-agent 源码快照。克隆本仓库无需再单独克隆这三个上游仓库。

## 已固定版本

- Ubuntu 22.04 LTS
- Python 3.10
- SWE-bench: `4.1.0`
- SWE-agent: `v1.1.0`（独立 Python 3.11 环境）
- mini-swe-agent: `v2.4.6`（独立 Python 3.10+ 环境）
- SWE-ReX: `1.4.0`

## 新服务器恢复

在具备 Python 3、Git 和 Docker 的 Linux 主机上克隆本仓库；运行目录可自行选择：

```bash
git clone https://github.com/1Ingeborg/SWE-smith-pipeline.git
cd SWE-smith-pipeline
```

如需将 Docker 数据目录迁至单独挂载的磁盘，可显式设置 `DATA_ROOT` 后运行
`tools/setup/bootstrap-host.sh`。该脚本会修改宿主机 Docker 配置；已有 Docker
环境无需运行。

从本仓库内置源码安装 SWE-smith Python 环境：

```bash
cp .env.example .env
# 按需在 .env 中设置 SWE_LAB_DATA_ROOT 和 DEEPSEEK_API_KEY。
bash scripts/bootstrap-python.sh
```

验证安装：

```bash
bash scripts/check-install.sh
```

## Bug任务和problem_statement生成

当前 `smoke.yaml` 处理两个 Python 仓库；Bug 生成使用程序化方法，问题描述阶段调用 DeepSeek Flash：

```text
确认或拉取镜像
  -> 从镜像 /testbed 恢复仓库源码（不依赖 GitHub clone）
  -> procedural mutation 生成单 Bug 候选
  -> 选择单 Bug 候选
  -> Docker 验证单 Bug
  -> combine_file 合并同文件内已验证的单 Bug（满足条件时）
  -> 选择合成候选
  -> Docker 验证合成 Bug
  -> DeepSeek Flash 生成 problem_statement（基于已验证的 single 与可用的 combine）
  -> 规则检查、泄漏审核、事实审核与 accepted/quarantine 分流
```

先只检查配置和将要执行的计划，不运行生产命令：

```bash
bash scripts/run-task-generation.sh \
  --config configs/task_generation/smoke.yaml \
  --run-id smoke \
  --dry-run
```

正式运行时去掉 `--dry-run`：

```bash
bash scripts/run-task-generation.sh \
  --config configs/task_generation/smoke.yaml \
  --run-id smoke
```

如果 SSH、服务器或进程中断，使用完全相同的配置和 `run-id` 续跑：

```bash
bash scripts/run-task-generation.sh \
  --config configs/task_generation/smoke.yaml \
  --run-id smoke \
  --resume
```



## 本地 Agent 修复与评测

```text
已验证且有问题描述的 accepted.jsonl
  -> 按配置选题并分配新的任务 ID
  -> 在基础镜像中应用 Bug 补丁，隐藏 F2P 测试并重置 Git 历史
  -> 生成带 Bug 的任务镜像与 Agent 可见的 instances.jsonl
  -> 选择 SWE-agent 或 mini-swe-agent 生成修复补丁与操作轨迹
  -> Docker 使用保留的 F2P/P2P 测试评测补丁
  -> 输出修复结果并导出 SFT 数据
```

基础镜像在任务生成时不会永久写入 Bug，因此 Agent 运行前需要制作带 Bug 的任务镜像。`instances.jsonl` 仅供 Agent 读取；原始任务、Bug 补丁和评测测试保存在私有目录。默认只运行选定的一个框架，不执行可选的 gold 标准答案检查。

完成前面的基础环境安装后，按需安装 Agent 环境：

```bash
bash tools/setup/bootstrap-swe-agent.sh
bash tools/setup/bootstrap-mini-swe-agent.sh
```

先预览 Agent 流程，不制作镜像、不调用模型 API：

```bash
bash scripts/run-agent.sh --config configs/rollout/smoke.yaml \
  --experiment mini-swe-agent --dry-run
```

已有同一 `rollout_id` 的配置快照且参数或引用文件发生变化时，dry-run 也会拒绝复用；需恢复原配置或为新实验设置新的 `rollout_id`。

```bash
# 选择 mini-SWE-agent：
bash scripts/run-agent.sh \
  --config configs/rollout/smoke.yaml \
  --experiment mini-swe-agent --allow-api-calls
```
```bash
# 选择 SWE-agent：
bash scripts/run-agent.sh \
  --config configs/rollout/smoke.yaml \
  --experiment swe-agent --allow-api-calls
```

每条命令依次执行准备、rollout、评测和 SFT 导出；`--allow-api-calls` 确认允许模型调用。需要 gold 检查时加 `--with-gold`，只运行单个阶段时使用 `--stage prepare|rollout|eval|sft|gold`。
