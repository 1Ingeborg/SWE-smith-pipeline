# 首轮最小实验

## 目标

先跑通数据构造闭环，不调用大模型，不追求数据规模：

```text
官方已有仓库环境
  -> procedural candidate patches
  -> 选取 10 个进入 validation
  -> baseline / post-patch validation
  -> 保留有效任务
  -> 人工检查有效与无效样本
```

以下是早期 `pilot.conf` 实验的历史参数；当前任务生成预设位于
`configs/task_generation/`，其中 `smoke.yaml` 用于 Agent 前的完整小样：

```text
仓库：Instagram__MonkeyType.70c3acf6
生成方式：Procedural Modification
随机种子：42
每种修改器最多生成：10
进入 validation 的候选数：10
Validation workers：2
```

## 开始前

```bash
cd /data/repos/swe-smith-lab
bash tools/setup/check-host.sh
bash scripts/check-install.sh

cd /data/repos/SWE-smith
source /data/venvs/swesmith/bin/activate
```

## 预期阶段

1. 拉取该仓库对应的 SWE-smith Docker image。
2. 在原始代码上执行测试，确认 baseline 健康。
3. 用 AST 规则生成 bug patches；`--max_bugs` 是每种修改器的上限，不是全局上限。
4. 收集 diff 与 metadata，并用 `--num_bugs 10` 选取 10 条。
5. 使用 2 个 worker 做 validation。
6. 使用 `src/swesmith_lab/pipeline/export.py` 汇总有效任务，并为淘汰样本记录原因。不要直接运行会向上游推送任务分支的 `swesmith.harness.gather`。
7. 人工检查至少 3 个有效样本和 3 个无效样本。

Docker image 可能有数 GiB。服务器使用公网流量计费时，应在拉取前确认预算和数据盘剩余空间。

## 首轮验收指标

```text
candidate_count
patch_apply_rate
baseline_pass_rate
test_break_rate
valid_rate
top_reject_reasons
```

本实验通过后，再扩大到 50 个候选，并加入 LM Modify、issue text 与 Agent trajectory。

本次实际运行结果见 [首轮实验结果](pilot-results.md)。
