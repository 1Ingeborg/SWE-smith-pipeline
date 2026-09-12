# 首轮最小实验

## 目标

先跑通数据构造闭环，不调用大模型，不追求数据规模：

```text
官方已有仓库环境
  -> 10 个 procedural candidate patches
  -> baseline / post-patch validation
  -> 保留有效任务
  -> 人工检查有效与无效样本
```

实验参数位于 `configs/experiments/pilot.conf`：

```text
仓库：Instagram__MonkeyType.70c3acf6
生成方式：Procedural Modification
候选数：10
Validation workers：2
```

## 开始前

```bash
cd /data/repos/swe-smith-lab
bash scripts/check-host.sh
bash scripts/check-install.sh

cd /data/repos/SWE-smith
source /data/venvs/swesmith/bin/activate
```

## 预期阶段

1. 拉取该仓库对应的 SWE-smith Docker image。
2. 在原始代码上执行测试，确认 baseline 健康。
3. 用 AST 规则生成 10 个 bug patches。
4. 收集 diff 与 metadata。
5. 使用 2 个 worker 做 validation。
6. 汇总有效任务，并为每个淘汰样本记录原因。
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
