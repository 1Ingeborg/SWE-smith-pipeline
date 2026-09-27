# SWE-smith Lab

用于复现 SWE-smith 数据构造、清洗、验证、训练数据整理和 bad case 迭代的实验工程。

本仓库保存实验脚本、配置及运行所需的 SWE-smith、SWE-agent、mini-swe-agent 源码快照；Git 不跟踪私钥、API Token、Docker 镜像、虚拟环境、模型、原始数据集、轨迹或运行日志。新实验可在仓库的 `results/` 下产生本地文件，但不会提交到 Git。克隆本仓库无需再单独克隆这三个上游仓库。

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
- 内置 SWE-smith 源码：官方提交 `9b74ac08118a85c39c356802f7961893af73e07f`，加上已应用到 `vendor/SWE-smith/` 的本地改动。补丁原件仅在本机 `patches/` 留存，不上传 GitHub。此前实验记录的基线为 `ad37c380ec8c7b775cdb11a074e408f90c874dad`；新快照并不自动等同于旧实验环境。
- SWE-bench: `4.1.0`
- SWE-agent: `v1.1.0`（独立 Python 3.11 环境）
- mini-swe-agent: `v2.4.6`（独立 Python 3.10+ 环境）
- SWE-ReX: `1.4.0`

SWE-smith 当前提交仍使用 `swebench.harness.constants.DOCKER_USER`。该符号在 SWE-bench 5.x 中不可用，因此 CPU 环境暂时固定为 SWE-bench 4.1.0。

## 新服务器恢复

下面的 `/data` 和宿主机初始化脚本是当前腾讯云机器的示例；其他机器可将仓库和 `SWE_LAB_DATA_ROOT` 放在适合自己的位置。更详细的环境说明仅在本机 `docs/` 留存，不上传 GitHub。

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
bash tools/setup/check-host.sh
```

首次初始化宿主机 Docker：

```bash
DOCKER_REGISTRY_MIRROR=https://mirror.ccs.tencentyun.com \
  bash tools/setup/bootstrap-host.sh
```

从本仓库内置源码安装 SWE-smith Python 环境（不再下载第二份源码）：

```bash
cp .env.example .env
# 在 .env 中填入 DEEPSEEK_API_KEY；腾讯云保持 SWE_LAB_DATA_ROOT=/data。
bash scripts/bootstrap-python.sh
```

该脚本新建 `$SWE_LAB_DATA_ROOT/venvs/swesmith-lab-core` 与
`$SWE_LAB_DATA_ROOT/venvs/swesmith-lab-llm`，不覆盖旧 venv。前者负责程序化
生成、验证及 DeepSeek 三重审核；后者供可选 LiteLLM Bug 生成策略使用，
隔离依赖。未设置数据根目录时默认使用仓库内忽略 Git 的 `.local/`。
环境变量优先于 `.env`；运行日志和 manifest 不保存 API 密钥。

验证安装：

```bash
bash scripts/check-install.sh
```

## 当前服务器目录

```text
/data/repos/swe-smith-lab   本实验仓库
/data/repos/swe-smith-lab/vendor/SWE-smith   纳入 Git 跟踪的官方源码快照及本地改动
/data/repos/SWE-smith       可选的历史路径兼容软链接，不是第二份源码
/data/venvs/swesmith-lab-core   新的核心 Python 虚拟环境
/data/venvs/swesmith-lab-llm    新的 LiteLLM 虚拟环境
/data/docker                Docker 数据
/data/containerd            containerd 数据
/data/datasets              数据集
/data/tasks                 构造后的任务
/data/trajectories          Agent 轨迹
/data/results               历史实验结果（保留原位置）
/data/huggingface           Hugging Face 缓存
/data/cache/pip             pip 缓存
```

旧 mini-swe-agent、SWE-bench Verified 50 和评测脚本仍在本机 `workflows/` 留存，
但已排除 Git，不属于新服务器上的入口。新的生成、验证配置直接指向
`vendor/SWE-smith`，不需要 `/data/repos/SWE-smith` 兼容链接。
新实验结果位于仓库的 `results/<run-id>/`；生成数据被 Git 忽略，不提交。
历史 `/data/results` 保留原位置，不自动迁移。

## 命令与实现的位置

- `scripts/` 只放日常入口：Python 环境安装与检查、多仓库任务生成及其便携启动脚本、单独的 DeepSeek 题目描述生成、Agent 运行。
- `src/swesmith_lab/pipeline/` 放候选选择、导出、合并与验证等内部步骤。
- `src/swesmith_lab/issuegen/` 放当前 DeepSeek 问题描述生成与审核代码。
- `src/swesmith_lab/agent/` 放任务准备、SWE-agent 调用和评测。
- `tools/setup/`、`tools/maintenance/` 分别放宿主机安装与低频维护脚本。

从仓库根目录执行文档中的命令。内部脚本可直接通过其新路径执行；不要再使用旧的 `scripts/<内部步骤>.py` 路径。
旧路径与新路径的完整对照仅在本机 `docs/script-layout.md` 留存。

运行预设按环节放在 `configs/task_generation/`（Agent 前的任务生成）和
`configs/rollout/`（Agent 运行）；问题描述生成由任务生成配置的 `issue_generation` 段控制。
`configs/task_generation/full.yaml` 是当前的 40 仓库程序化生产预设；
`smoke.yaml` 用于 Agent 前的小规模全流程冒烟测试（包括 DeepSeek 问题描述与审核）。
启动时必须显式传 `--config`，不会默认选择生产规模。

## 首轮实验

首轮只使用一个 Python 仓库和程序化修改，不调用大模型：

```text
1 个仓库
10 个 candidate patches
2 个 validation workers
人工检查有效与无效样本
```

首轮实验说明与结果仅在本机 `docs/` 留存，不上传 GitHub。

历史模型对比及 problem statement 质量控制记录也仅在本机 `docs/` 留存。

## 多仓库任务生成

`scripts/run-task-generation.sh` 自动定位本机 core 虚拟环境，再调用
`scripts/run-task-generation.py`，将 Agent 前的以下阶段串成一次可续跑的任务生成流程：

```text
确认或拉取镜像
  -> 从镜像 /testbed 恢复仓库源码（不依赖 GitHub clone）
  -> procedural mutation 生成
  -> 按 mutation strategy 选择候选
  -> Docker validation
  -> 本地导出与多仓库合并
  -> DeepSeek Flash problem_statement 生成
  -> 规则检查、泄漏审核、事实审核与 accepted/quarantine 分流
```

先只检查配置和将要执行的计划，不运行生产命令：

```bash
cd /data/repos/swe-smith-lab
bash scripts/run-task-generation.sh \
  --config configs/task_generation/smoke.yaml \
  --run-id smoke-plan-001 \
  --dry-run
```

正式运行时换一个新的 `run-id` 并去掉 `--dry-run`：

```bash
cd /data/repos/swe-smith-lab
bash scripts/run-task-generation.sh \
  --config configs/task_generation/smoke.yaml \
  --run-id smoke-example-001
```

如果 SSH、服务器或进程中断，使用完全相同的配置和 `run-id` 续跑：

```bash
cd /data/repos/swe-smith-lab
bash scripts/run-task-generation.sh \
  --config configs/task_generation/smoke.yaml \
  --run-id smoke-example-001 \
  --resume
```

新实验每个阶段结束后都会更新 `results/<run-id>/meta/manifest.json`。旧实验仍使用根目录的 `manifest.json`，不会迁移。
各仓库依次运行，默认 validation workers 为 4；问题描述生成完成后，最终数据位于：

```text
results/<run-id>/single/candidates/candidates.jsonl
results/<run-id>/single/candidates/candidates.summary.json
results/<run-id>/single/validation/accepted.jsonl
results/<run-id>/single/validation/accepted.summary.json
results/<run-id>/combine/validation/accepted.jsonl   # 启用且完成 Combine 时
results/<run-id>/issuegen/accepted.jsonl
results/<run-id>/issuegen/quarantine.jsonl
```

`accepted.jsonl` 只收录审核通过且描述非空的任务；Docker 准备失败、
规则拒绝、审核拒绝或待人工确认的任务进入 `quarantine.jsonl`。
旧运行目录和其中的配置快照不改动；新模型配置请使用新的 run-id，
旧 run-id 不会在模型已改变时被静默续跑。

Agent 之前先跑单仓库完整冒烟（15 个候选上限、Docker 验证、DeepSeek
Flash 问题描述及审核；不自动启动 Agent）：

```bash
bash scripts/run-task-generation.sh \
  --config configs/task_generation/smoke.yaml \
  --run-id smoke-before-agent-20260927-001 \
  --dry-run

bash scripts/run-task-generation.sh \
  --config configs/task_generation/smoke.yaml \
  --run-id smoke-before-agent-20260927-001

cat results/smoke-before-agent-20260927-001/manifest.json
wc -l results/smoke-before-agent-20260927-001/issuegen/accepted.jsonl
```

实际运行需要本机 Docker 镜像和 `DEEPSEEK_API_KEY`。若中断，在正式命令后加
`--resume`。`target_validated: 3` 是验证目标，不保证有 3 条审核通过的题目；
只有 `accepted.jsonl` 非空，才继续使用下文的 Agent 准备入口。

本次只验证了 3 题，没有启动完整生产。以后要启动 40 仓库生产实验，请确认 API
余额、镜像与剩余空间，再使用新的 run-id：

```bash
cd /data/repos/swe-smith-lab
bash scripts/run-task-generation.sh \
  --config configs/task_generation/full.yaml \
  --run-id procedural-deepseek-20260927-001
```

任务生成入口不会检查 `/data` 剩余空间，也不会自动删除 Docker 镜像或历史产物。
在其他服务器上只需改为自己的仓库克隆目录，并在本机 `.env` 设置
`SWE_LAB_DATA_ROOT`（或使用仓库内默认的 `.local`）；命令不依赖 `/data/venvs`。

## 本地 Agent 修复与评测

本仓库可以把 `accepted.jsonl` 转成防直接泄漏的本地 Agent 任务镜像，运行固定版本 SWE-agent 或 mini-swe-agent，并用私有 F2P/P2P 真值评分。三个上游项目的源码均已内置于 `vendor/`，版本与许可证见 [`vendor/README.md`](vendor/README.md)；SWE-smith 兼容改动已包含在内置源码中，补丁原件仅在本机留存。本地任务不依赖 Hugging Face 数据，也不要求将每条任务分支推送到 GitHub。

```text
accepted.jsonl
  -> 私有选样与 opaque ID
  -> 注入 bug、隐藏 F2P 测试、清洗 Git 历史
  -> 公开五字段 instances.jsonl
  -> SWE-agent preds.json / trajectories
  -> 私有 Docker evaluator
  -> resolved / unresolved
```

在新服务器上先安装系统 Python 3、`venv`、Docker 和 Git；将 `.env.example` 复制为未提交的 `.env`，按需设置 `SWE_LAB_DATA_ROOT` 与模型 API 密钥。再从仓库中的固定版本源码分别创建三个独立环境：

```bash
bash scripts/bootstrap-python.sh
bash tools/setup/bootstrap-swe-agent.sh
bash tools/setup/bootstrap-mini-swe-agent.sh
bash scripts/check-install.sh
```

入口会把环境装在 `SWE_LAB_DATA_ROOT/venvs/`（未设置时为仓库内 `.local/venvs/`），无需提交虚拟环境。mini-swe-agent 要求 Python 3.10+；若系统 `python3` 不符合要求，可在安装前设置 `MINI_SWE_AGENT_PYTHON`。Docker 镜像也不存 Git：宿主机需能运行 Docker，按任务生成配置准备基础镜像，再由 `run-agent.sh` 的 `prepare` 阶段构建每题任务镜像。镜像名称随配置和任务变化，不能靠复制两个 Agent 源码获得。安装脚本只准备 Python 环境，不触发模型 API 调用或构建任务镜像。

新实验统一使用下方的 `run-agent.sh` 配置入口；它必须显式指定 rollout YAML。
`--dry-run` 不调用模型 API；实际启动 Agent 必须加 `--allow-api-calls`，并受到调用次数与费用上限保护。

本地 SWE-agent 修复与评测的详细记录仅在本机 `docs/local-agent-evaluation.md` 留存。

新实验使用统一入口和 `configs/rollout/<实验>.yaml`。省略 `--stage` 等同于
`--stage agent`：依次检查/执行任务准备、所选框架的 rollout、模型评测、SFT 导出。
已完整完成的阶段会跳过，缺失的后续阶段会补齐；部分完成的 rollout 需要显式
`--resume`。`gold` 默认不运行，只有添加 `--with-gold` 或单独指定 `--stage gold`
才检查。gold 在默认完整流程中位于模型评测与 SFT 导出之间。
一个 YAML 指向已有任务生成 `run-id`，并提供两个可选框架；
`--experiment` 选择框架，YAML 中的 `rollout_id` 区分模型、温度或重复实验。
两个框架共享一次 `prepare` 产生的题目镜像和私有真值。当前 `smoke` 的命令为：

SWE-agent 的编辑工具使用仓库自带的 bundle（来源与许可见 `THIRD_PARTY_NOTICES.md`）；运行时将 `lab:` 路径解析成
仓库绝对路径，工具依赖和命令选用任务容器内同一个 Python。当前 smoke 的
SWE-agent 仍使用官方多题运行命令；仓库内固定的源码已将其三个
输出文件名改为 `agent.log`、`agent.config.yaml`、`agent_exit_statuses.yaml`；
启动前会检查补丁是否安装，避免新结果目录再次出现带 `batch` 的文件名。
历史失败结果保留；腾讯云实验通过原生配置指定可用的 pip 镜像源。
`smoke-swe-toolcheck.yaml` 通过 `max_instances: 1` 只检查第一题；正式实验应使用
独立的 `rollout_id`，不要覆盖单题或历史结果。

```bash
bash scripts/run-agent.sh --config configs/rollout/smoke.yaml \
  --experiment mini-swe-agent --allow-api-calls
# 若要运行 SWE-agent，另行明确选择：
bash scripts/run-agent.sh --config configs/rollout/smoke.yaml \
  --experiment swe-agent --allow-api-calls
# 如需额外执行 gold 检查：
bash scripts/run-agent.sh --config configs/rollout/smoke.yaml \
  --experiment swe-agent --allow-api-calls --with-gold
# 可选：只执行指定阶段：
bash scripts/run-agent.sh --config configs/rollout/smoke.yaml --stage prepare
bash scripts/run-agent.sh --config configs/rollout/smoke.yaml --stage rollout \
  --experiment mini-swe-agent --allow-api-calls
bash scripts/run-agent.sh --config configs/rollout/smoke.yaml --stage eval \
  --experiment mini-swe-agent
bash scripts/run-agent.sh --config configs/rollout/smoke.yaml --stage sft \
  --experiment mini-swe-agent
bash scripts/run-agent.sh --config configs/rollout/smoke.yaml --stage gold
```

按需逐条执行；不会默认运行两个框架。`prepare` 只需一次，两个框架共享；
即使同时启动也会等待同一个准备锁，避免重复制作镜像。`gold` 不调用 Agent 或模型 API。当前 gold
评测在原始基础镜像中重建 Bug 并反向应用变异补丁，**不能据此证明 Agent 任务
镜像的环境相同**。`agent` 阶段只有显式加 `--allow-api-calls` 才会调用模型。
参数由 YAML 统一决定，框架原生配置只保留提示词、工具等设置；密钥仍由未提交的
`.env` 或环境变量提供。已经完整结束的 rollout 仅补齐后续阶段时不需要
`--allow-api-calls`；任何会新启动/续跑模型调用的命令仍必须提供它。新配置不能与旧的 `--run-dir`、`--framework` 等参数混用，
旧参数形式仍可用于新入口；旧脚本文件名不再保留。

框架原生配置分别是 `configs/agent/swe-agent.yaml` 和
`configs/agent/mini-swe-agent.yaml`，不再固定 DeepSeek 模型名或 API 地址。
`configs/agent/README.md` 解释两份文件的分工。历史 Verified 对照配置仅在本机
`workflows/verified50/` 留存，不属于新实验。
模型、地址、温度和上限写在所选 rollout YAML 的实验项中。SWE-agent 的
`completion_kwargs` 是可选的模型专属请求参数：当前 DeepSeek Flash 实验使用
`thinking: {type: disabled}`，切换到其他模型时应检查其 API 是否支持，并删除或调整该项。
更换原生配置文件也会改变有效配置快照；已有结果应使用新的 `rollout_id`，
不要在旧 ID 下续跑。

`--dry-run` 不写文件、不构建镜像、不调用模型。中断后在同一命令加 `--resume`；
更换模型、温度或其他实验参数时应使用新的 `rollout_id`，不在原 ID 上续跑。
有效配置快照保存在 `results/<run-id>/meta/rollout-configs/`，轨迹和测评分别在
`results/<run-id>/rollouts/<framework>/<rollout_id>/` 和
`results/<run-id>/evaluations/<framework>/<rollout_id>/`；gold 在
`results/<run-id>/evaluations/gold/`。历史 Agent 脚本和 `/data/results` 结果不迁移。
SFT 输出在 `results/<run-id>/sft/<framework>/<rollout_id>/`：包含带 resolved 标记的
`all.jsonl`、已解决的 `resolved.jsonl`、训练用 `resolved_chat.jsonl`、
`dataset_info.json` 和核对清单。SWE-agent 使用 XML 动作格式；mini-swe-agent
保留框架原生的 bash 动作格式。SWE-agent 导出直接调用内置 SWE-smith 的轨迹收集与
XML 转换代码；mini-swe-agent 导出使用 `src/swesmith_lab/agent/mini_sft_converter.py`，
不依赖本机历史 `workflows/`。导出发现轨迹或评测缺题时会报错，不会静默丢弃。
