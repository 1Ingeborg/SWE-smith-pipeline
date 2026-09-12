# MonkeyType 首轮实验结果

## 实验参数

- 服务器：8 vCPU、32 GiB RAM、Ubuntu 22.04、300 GiB 数据盘
- SWE-smith：`9b74ac08118a85c39c356802f7961893af73e07f`
- 仓库：`Instagram__MonkeyType.70c3acf6`
- 镜像：`swebench/swesmith.x86_64.instagram_1776_monkeytype.70c3acf6`
- 生成方式：procedural，seed 42，interleave
- Validation：10 条候选，2 workers

## 实际结果

```text
baseline: 379 passed, 2 skipped, 1 xpassed
生成候选: 93
送入 validation: 10
有效: 7
无效: 3
超时或运行异常: 0
有效率: 70%
```

`--max_bugs 10` 表示每种修改器最多保留 10 条。本次 13 种修改器合计生成了 93 条；收集阶段再用 `--num_bugs 10` 控制 validation 规模。

有效样本保存在：

```text
/data/datasets/swe-smith-lab/monkeytype-pilot-valid.jsonl
```

3 条无效样本的共同原因是 `FAIL_TO_PASS` 数量为 0，也就是补丁没有让任何原本通过的测试失败。

## 人工抽查与第一次清洗迭代

有效样本抽查：

- 删除 `trace_modules_str` 的赋值，后续读取未定义变量，破坏 4 个配置测试。
- 删除查找本地函数的循环，破坏 1 个 tracing 测试。
- 删除 `handle_call` 方法，破坏 15 个数据库和 tracing 测试。

无效样本抽查：

- 将 `co_argcount + co_kwonlyargcount` 交换为相反顺序，加法结果不变，属于等价补丁。
- 调整 Python 类中方法的排列顺序，没有改变运行语义。
- 删除一个 `try/except` 包装后没有测试失败，说明该路径未被现有测试覆盖，不能作为当前测试集下的可验证任务。

第一次清洗规则因此确定为：必须有至少 1 个 `FAIL_TO_PASS`，同时至少保留 1 个 `PASS_TO_PASS`。后续扩大数据规模时，可在 validation 前预先过滤明显的交换律等价变换和纯方法重排，减少无效容器运行。

## 实际命令

```bash
cd /data/repos/SWE-smith
source /data/venvs/swesmith/bin/activate

docker pull swebench/swesmith.x86_64.instagram_1776_monkeytype.70c3acf6

python -m swesmith.bug_gen.procedural.generate \
  Instagram__MonkeyType.70c3acf6 \
  --max_bugs 10 --seed 42 --interleave

python -m swesmith.bug_gen.collect_patches \
  logs/bug_gen/Instagram__MonkeyType.70c3acf6 \
  --num_bugs 10

python -m swesmith.harness.valid \
  logs/bug_gen/Instagram__MonkeyType.70c3acf6_all_patches_n10.json \
  --workers 2

python /data/repos/swe-smith-lab/scripts/export-valid-local.py \
  logs/bug_gen/Instagram__MonkeyType.70c3acf6_all_patches_n10.json \
  logs/run_validation/Instagram__MonkeyType.70c3acf6 \
  /data/datasets/swe-smith-lab/monkeytype-pilot-valid.jsonl \
  --image-name swebench/swesmith.x86_64.instagram_1776_monkeytype.70c3acf6
```

## 资源占用

`sar` 每 2 秒或 5 秒采样整机资源。短任务的峰值只代表采样点，不是硬件上限。

| 阶段 | 平均 CPU | 峰值 CPU | 平均 RAM | 峰值 RAM |
|---|---:|---:|---:|---:|
| 拉取镜像 | 11.25% | 27.40% | 1.23 GiB | 1.27 GiB |
| Baseline | 13.61% | 13.61% | 1.29 GiB | 1.29 GiB |
| 生成 93 条 | 8.25% | 17.78% | 1.37 GiB | 1.38 GiB |
| Validation，2 workers | 4.95% | 26.42% | 1.29 GiB | 1.38 GiB |

本轮负载远低于 8 vCPU、32 GiB RAM 的容量。以后换更大的仓库、提高 workers 或运行 issue generation 时需重新测量。

## 实时查看

```bash
# 最直观：每个逻辑核、内存和进程
htop

# 每秒显示所有 vCPU；最后一列 idle 越低，CPU 越忙
mpstat -P ALL 1

# 内存重点看 available，不要只看 free
watch -n 1 free -h

# 当前所有 Docker 容器的 CPU 和内存
docker stats

# 数据盘空间
watch -n 5 df -h /data
```

在 `htop` 中按 `1` 展开 8 个 vCPU，按 `F6` 可按 CPU 或内存排序，按 `q` 退出。
