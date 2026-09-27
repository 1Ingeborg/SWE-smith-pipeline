# 本地 SWE-agent 修复与评测流水线

本文记录 `swe-smith-lab` 如何把本地生成的 SWE-smith 任务交给 Agent 修复，并在不依赖远程任务分支的情况下完成私有评分。

## 1. 对上游 Agent 流程的理解

SWE-smith 的 `patch` 是“正向注入 bug”的补丁，不是修复补丁。上游完整流程如下：

1. procedural generator 生成候选 bug patch。
2. validation 在原始代码和注入 bug 后的代码上运行测试，得到 `PASS_TO_FAIL` 与 `PASS_TO_PASS`。
3. `swesmith.harness.gather` 把 `PASS_TO_FAIL` 改名为 SWE-bench 风格的 `FAIL_TO_PASS`，然后在远程镜像仓库创建任务分支：
   - 第一层提交：`Bug Patch`；
   - 第二层提交：`Remove F2P Tests`；
   - 将分支推送到 GitHub。
4. SWE-agent 检出这个任务分支，只看到带 bug 的源码和被移除 F2P 测试后的工作区；Agent 最终输出正向修复补丁 `model_patch`。
5. `swesmith.harness.eval` 重新拉取分支，检出 `HEAD~1` 回到“bug 已注入、F2P 测试仍存在”的状态，应用 `model_patch`，恢复所有 F2P/P2P 测试文件，执行 profile 的测试命令并评分。

上游也提供了 `agent/_gen_trajs.sh`、SWE-agent 配置和训练文档，但它们主要面向已经通过 `gather` 推送了远程任务分支的公开 SWE-smith 数据。

## 2. 为什么本项目不能原样调用上游命令

本项目的 `accepted.jsonl` 是本地生成并审核后的数据，目前没有为每条任务创建和推送 GitHub 分支。上游评测器假定：

- `instance_id` 同时是可以 `git fetch`/`git checkout` 的远程分支名；
- 分支顶部恰好有 `Bug Patch`、`Remove F2P Tests` 两层提交；
- 评分时可以通过 `HEAD~1` 恢复隐藏测试。

直接运行上游 `swesmith.harness.eval` 会卡在这些假设上。此外，本项目的源 `instance_id` 包含 mutation strategy，例如 `func_pm_remove_wrapper`。如果直接交给 Agent，会把 bug 类型提示给模型；远程分支的 Git 历史也能通过 `git show` 暴露原始代码和 mutation diff。

因此，上游 SWE-smith 保持不修改，所有本地适配都放在 `swe-smith-lab`。

## 3. 新增流水线

```text
accepted.jsonl + audit.jsonl
        |
        v
固定 seed=42，按仓库配额选择并尽量覆盖不同 strategy
        |
        +--> private/selected.jsonl
        |      保存 patch、F2P/P2P、源 ID、strategy、audit 等私有真值
        |
        v
为每个仓库构建一次 runtime base
  - 隔离安装 swe-rex==1.4.0
  - 预装 SWE-agent 编辑工具的固定依赖
        |
        v
为每条任务构建 Agent task image
  - 正向应用 bug patch
  - 隐藏 F2P 测试文件
  - 删除原 .git
  - 创建只有一个根提交、无 remote 的新仓库
        |
        +--> public/instances.jsonl（仅五个公开字段）
        |
        v
SWE-agent -> preds.json / .traj
        |
        v
私有 evaluator
  - 从原始仓库镜像重新创建 buggy baseline
  - 应用 Agent 的正向 model_patch
  - 恢复 F2P/P2P 测试文件
  - 执行 profile 测试命令
  - 复用 SWE-smith grading 判定 resolved
```

### 3.1 公开与私有边界

Agent 只获得以下五个字段：

| 字段 | 含义 |
| --- | --- |
| `instance_id` | 随机、不可读的任务 ID，不含仓库或 mutation 名称 |
| `image_name` | 已注入 bug 且清洗历史的本地 Docker 镜像 |
| `problem_statement` | 给 Agent 的问题描述 |
| `repo_name` | 固定为 `testbed` |
| `base_commit` | 固定为 `HEAD` |

以下内容只能保存在权限为 `0600/0700` 的 `private/` 与评测目录中：源 `instance_id`、mutation patch、`FAIL_TO_PASS`、`PASS_TO_PASS`、strategy、rewrite、audit 和源镜像信息。

这条边界是安全约束：不要把 `private/selected.jsonl` 当作 SWE-agent 的输入。

### 3.2 镜像设计

`src/swesmith_lab/agent/prepare.py` 创建两层本地镜像：

- 每个仓库一个共享 runtime base。`swe-rex==1.4.0` 安装到 `/opt/swerex` 隔离环境，不改动项目 Python；SWE-agent v1.1.0 编辑工具所需的 `tree-sitter==0.21.3` 与 `tree-sitter-languages==1.10.2` 则装到项目的 testbed Python。
- 每条任务一个 task image。在 runtime base 上应用对应 bug、隐藏测试并清洗 Git 历史。

runtime 镜像标签的指纹同时包含源镜像 ID、SWE-ReX 版本和工具依赖列表。配置变化时不会错误复用旧 runtime。

预装工具依赖不会改变评分环境：私有 evaluator 始终从原始 SWE-smith 仓库镜像启动，而不是从 Agent task image 启动。

### 3.3 Agent 运行与费用保护

`src/swesmith_lab/agent/run.py` 固定使用独立安装的 SWE-agent v1.1.0：

- 默认 `smoke` 模式使用 `instant_empty_submit`，不调用外部模型 API；
- `model` 模式必须同时显式提供 `--allow-api-calls`、模型名、API base 和密钥环境变量；
- 每任务调用次数、每任务费用和总费用分别有限额；
- API 密钥值不会写入命令记录或结果文件；
- 每次运行生成独立的 `.traj`、`.pred`、`preds.json` 和 `lab-run-summary.json`。

### 3.4 私有评分

`src/swesmith_lab/agent/evaluate.py` 支持两种输入：

- 普通 `preds.json`：把 Agent 的 `model_patch` 正向应用到 buggy baseline；
- `gold`：把私有 mutation patch 反向应用，作为评测器自检，不把答案暴露给 Agent。

评测器不会执行 `git fetch`，也不要求远程任务分支。它支持 `--workers`、`--resume`、`--f2p-only`、超时和指定 opaque ID。单 vCPU 服务器建议保持 `--workers 1`。

## 4. 使用方法

### 4.1 安装固定版本 SWE-agent

```bash
cd /data/repos/swe-smith-lab
bash tools/setup/bootstrap-swe-agent.sh
```

安装脚本固定 SWE-agent v1.1.0 源码包及 SHA256，使用独立 Python 3.11 环境 `/data/venvs/sweagent`，不会污染 `/data/venvs/swesmith`。

### 4.2 选择 12 条 pilot 并构建镜像

先看计划：

```bash
/data/venvs/swesmith-lab-core/bin/python src/swesmith_lab/agent/prepare.py \
  --config configs/rollout/agent-pilot.yaml \
  --run-id agent-pilot-example \
  --dry-run
```

正式构建：

```bash
/data/venvs/swesmith-lab-core/bin/python src/swesmith_lab/agent/prepare.py \
  --config configs/rollout/agent-pilot.yaml \
  --run-id agent-pilot-example
```

中断后使用相同 run ID：

```bash
/data/venvs/swesmith-lab-core/bin/python src/swesmith_lab/agent/prepare.py \
  --config configs/rollout/agent-pilot.yaml \
  --run-id agent-pilot-example \
  --resume
```

### 4.3 零费用冒烟

```bash
/data/venvs/swesmith-lab-core/bin/python src/swesmith_lab/agent/run.py \
  --instances /data/results/agent-pilot-runs/agent-pilot-example/public/instances.jsonl \
  --run-id agent-smoke-example \
  --workers 1
```

冒烟模式只验证 Agent 能否启动任务镜像、安装工具、操作仓库、提交补丁并写出轨迹。它不代表 Agent 真的修复了问题。

### 4.4 评测器 gold 自检

```bash
/data/venvs/swesmith-lab-core/bin/python src/swesmith_lab/agent/evaluate.py \
  --dataset /data/results/agent-pilot-runs/agent-pilot-example/private/selected.jsonl \
  --predictions gold \
  --run-id agent-gold-example \
  --workers 1 \
  --require-all
```

### 4.5 评测真实 Agent 输出

```bash
/data/venvs/swesmith-lab-core/bin/python src/swesmith_lab/agent/evaluate.py \
  --dataset /data/results/agent-pilot-runs/agent-pilot-example/private/selected.jsonl \
  --predictions /data/trajectories/agent-pilot-runs/agent-model-example/preds.json \
  --run-id agent-eval-example \
  --workers 1 \
  --require-all
```

长时间评分建议放入 `nohup`/tmux 中；如果 SSH 中断，确认并清理精确的孤儿评测容器后，用同一个 run ID 和 `--resume` 继续。

## 5. 2026-09-15 实测

`agent-pilot-20260915-003`：

- 从本地 39 条 `accepted.jsonl` 中固定选择 12 条；
- 覆盖 4 个仓库，每仓库 3 条，共 7 种 mutation strategy；
- 生成 4 个共享 runtime base 和 12 个 task image；
- 构建总计约 188.5 秒，中位每条约 7.1 秒；
- 12 个镜像各检查 6 项不变量，共 72 项，0 失败；
- 公开 JSONL 中源 ID 泄漏 0、strategy 名称泄漏 0。

`agent-smoke-all12-preinstalled-20260915-001`：

- 12/12 状态为 `submitted`；
- 12/12 生成预测和轨迹；
- 外部模型费用为 0；
- 墙钟时间 86.65 秒；
- 日志中重复下载 tree-sitter 的次数由旧批次 24 次降为 0；
- 旧批次日志跨度约 499 秒，新批次约 81 秒，约快 5.8 倍。

此前单条自检已经确认：gold 修复为 1/1 resolved；零费用空修复为 0/1 resolved。这证明评分器能区分正确修复与无效输出。

完整 gold 自检 `agent-gold-all12-20260915-001`：

- 12/12 resolved；
- 12 条状态均为 `completed`；
- 0 missing prediction、0 unexpected prediction、0 timeout、0 error；
- SSH 中断后从已经落盘的 2 条报告继续，`--resume` 阶段墙钟 541.52 秒；
- 结束后没有残留 Docker 容器。

## 6. 已知边界

- 自动生成的 `problem_statement` 可能暗示文件、函数或修复方向。当前审核只能降低明显泄漏，不能保证语义层面零泄漏；这与“允许少量问题、先做规模化实验”的目标一致。
- opaque ID、隐藏测试和 Git 历史清洗防的是结构性直接泄漏，不等于问题描述完全无提示。
- `smoke` 模式不是修复能力测评；只有真实模型的 `preds.json` 经私有 evaluator 后的 resolved rate 才是修复成绩。
- 完整仓库测试通常是 CPU 密集型。单 vCPU 下提高 `workers` 只会争抢 CPU，可能更慢；扩容到多 vCPU 后再按核心数逐步提高。
- task image 体积显示为数 GB，但 Docker 层会在同一仓库镜像之间共享；不要把每个标签显示的 size 简单相加当作真实新增占用。
