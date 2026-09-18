# SWE-smith Lab

用于复现 SWE-smith 数据构造、清洗、验证、训练数据整理和 bad case 迭代的实验工程。

本仓库只保存可复现所需的小文件，不保存私钥、API Token、Docker 镜像、虚拟环境、模型、原始数据集、轨迹或运行日志。

## 当前架构

```text
腾讯云 CPU CVM
  - SWE-smith / SWE-bench
  - Docker 仓库环境
  - candidate patch 生成与 validation
  - 数据清洗和评测

AutoDL GPU
  - 7B-9B 模型训练
  - vLLM/SGLang 推理服务
  - 通过 SSH 隧道向 CPU 节点提供 OpenAI-compatible API
```

## 已固定版本

- Ubuntu 22.04 LTS
- Python 3.10
- SWE-smith commit: `ad37c380ec8c7b775cdb11a074e408f90c874dad`
- SWE-bench: `4.1.0`
- SWE-agent: `v1.1.0`（独立 Python 3.11 环境）
- SWE-ReX: `1.4.0`

SWE-smith 当前提交仍使用 `swebench.harness.constants.DOCKER_USER`。该符号在 SWE-bench 5.x 中不可用，因此 CPU 环境暂时固定为 SWE-bench 4.1.0。

## 新服务器恢复

先将数据盘挂载到 `/data`，然后安装 Git 并克隆本仓库：

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/1Ingeborg/swe-smith-lab.git /data/repos/swe-smith-lab
cd /data/repos/swe-smith-lab
```

私有仓库需要先在新服务器配置 GitHub 身份，或者从已登录的本机完成克隆和传输。

检查服务器：

```bash
bash scripts/check-host.sh
```

首次初始化宿主机 Docker：

```bash
DOCKER_REGISTRY_MIRROR=https://mirror.ccs.tencentyun.com \
  bash scripts/bootstrap-host.sh
```

安装 SWE-smith Python 环境：

```bash
bash scripts/bootstrap-python.sh
```

验证安装：

```bash
bash scripts/check-install.sh
```

## 当前服务器目录

```text
/data/repos/swe-smith-lab   本实验仓库
/data/repos/SWE-smith       SWE-smith 上游源码
/data/venvs/swesmith        Python 虚拟环境
/data/docker                Docker 数据
/data/containerd            containerd 数据
/data/datasets              数据集
/data/tasks                 构造后的任务
/data/trajectories          Agent 轨迹
/data/results               validation / evaluation 结果
/data/huggingface           Hugging Face 缓存
/data/cache/pip             pip 缓存
```

## 首轮实验

首轮只使用一个 Python 仓库和程序化修改，不调用大模型：

```text
1 个仓库
10 个 candidate patches
2 个 validation workers
人工检查有效与无效样本
```

执行前阅读 [首轮实验说明](docs/first-experiment.md)，本次实测记录见 [首轮实验结果](docs/pilot-results.md)。

问题描述生成的模型对比见 [Qwen 模型对比](docs/issue-model-comparison.md)，
批量生成与自动审核方案见 [problem statement 质量控制](docs/problem-statement-quality-control.md)。

## 多仓库单命令流水线

`scripts/run-multirepo-pipeline.py` 将以下阶段串成一次可续跑的统一流水线：

```text
确认或拉取镜像
  -> 从镜像 /testbed 恢复仓库源码（不依赖 GitHub clone）
  -> procedural mutation 生成
  -> 按 mutation strategy 选择候选
  -> Docker validation
  -> 本地导出与多仓库合并
  -> Qwen problem_statement 生成
  -> 本地规则审核与 accepted/quarantine 分流
```

先只检查配置和将要执行的计划，不运行生产命令：

```bash
cd /data/repos/swe-smith-lab
/data/venvs/swesmith/bin/python scripts/run-multirepo-pipeline.py \
  --config configs/experiments/multirepo-procedural.yaml \
  --run-id multirepo-smoke-001 \
  --dry-run
```

正式运行时换一个新的 `run-id` 并去掉 `--dry-run`：

```bash
cd /data/repos/swe-smith-lab
/data/venvs/swesmith/bin/python scripts/run-multirepo-pipeline.py \
  --config configs/experiments/multirepo-procedural.yaml \
  --run-id multirepo-20260914-001
```

如果 SSH、服务器或进程中断，使用完全相同的配置和 `run-id` 续跑：

```bash
cd /data/repos/swe-smith-lab
/data/venvs/swesmith/bin/python scripts/run-multirepo-pipeline.py \
  --config configs/experiments/multirepo-procedural.yaml \
  --run-id multirepo-20260914-001 \
  --resume
```

每个阶段结束后都会更新 `/data/results/multirepo-runs/<run-id>/manifest.json`。
各仓库依次运行，默认 validation workers 为 4；问题描述生成完成后，最终数据位于：

```text
/data/results/multirepo-runs/<run-id>/issuegen/accepted.jsonl
/data/results/multirepo-runs/<run-id>/issuegen/quarantine.jsonl
```

流水线不会检查 `/data` 剩余空间，也不会自动删除 Docker 镜像或历史产物。

## 本地 Agent 修复与评测

本仓库现在可以把 `accepted.jsonl` 转成防直接泄漏的本地 SWE-agent 任务镜像，运行固定版本 Agent，并用私有 F2P/P2P 真值评分。上游 SWE-smith 源码保持不修改；本地任务不依赖 Hugging Face 数据，也不要求将每条任务分支推送到 GitHub。

```text
accepted.jsonl
  -> 私有选样与 opaque ID
  -> 注入 bug、隐藏 F2P 测试、清洗 Git 历史
  -> 公开五字段 instances.jsonl
  -> SWE-agent preds.json / trajectories
  -> 私有 Docker evaluator
  -> resolved / unresolved
```

先安装固定版本 Agent：

```bash
cd /data/repos/swe-smith-lab
bash scripts/bootstrap-swe-agent.sh
```

然后构建 pilot 并执行零费用冒烟：

```bash
/data/venvs/swesmith/bin/python scripts/prepare-agent-pilot.py \
  --config configs/experiments/agent-pilot.yaml \
  --run-id agent-pilot-example

/data/venvs/swesmith/bin/python scripts/run-agent-pilot.py \
  --instances /data/results/agent-pilot-runs/agent-pilot-example/public/instances.jsonl \
  --run-id agent-smoke-example \
  --workers 1
```

默认冒烟模式不调用外部模型 API。真实模型模式必须显式使用 `--allow-api-calls`，并受到调用次数与费用上限保护。

源码逻辑、隐私边界、运行命令和实测结果见 [本地 SWE-agent 修复与评测流水线](docs/local-agent-evaluation.md)。
