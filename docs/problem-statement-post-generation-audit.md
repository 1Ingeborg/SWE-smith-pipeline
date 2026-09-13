# SWE-smith `problem_statement` 自动审核缺口与改进方案

## 1. 为什么只生成 1 条也要加载 59,136 条 SWE-smith 数据

这不是模型生成本身的需要，而是当前 SWE-smith 上游实现的固定初始化行为。

`swesmith/issue_gen/generate.py` 的 `IssueGen.__init__` 无条件执行：

```python
data_smith = [x for x in load_dataset(HF_DATASET, split="train")]
```

随后它把整个训练集转成 Python 对象，并用其中已经具有
`problem_statement` 的 `instance_id` 构造集合，过滤重复实例。这个动作发生在读取
`--instance_ids` 和处理自定义输入之前，因此即使输入 JSON 只有 1 条，仍会下载、解析并
遍历完整的 59,136 条。

另外，程序还会加载 500 条 `SWE-bench_Verified`，用于随机抽取两个真实 issue 作为写作
风格示例。也就是说：

- 59,136 条 SWE-smith：主要用于“是否已经生成过”的去重过滤；
- 500 条 SWE-bench Verified：用于 prompt 中的 demonstration；
- 真正生成当前 1 条 issue，并不需要把 59,136 条完整记录都放进内存。

这是上游为了使用 Hugging Face 主数据集而做的方便实现，对本地小批量输入并不高效。
本次没有修改上游源码，以保持基线可复现；已通过 Hugging Face 缓存消除重复下载。
后续若要优化启动时间，应给上游入口增加显式参数，例如
`--skip-existing-hf-check`，或者只流式读取 `instance_id` 与
`problem_statement` 两列，而不是加载完整记录。

## 2. 当前上游行为为什么不合理

上游 prompt 虽然明确写了 `DO NOT GIVE AWAY THE FIX`，但 prompt 只是软约束，不是质量
保证。当前流程在模型返回后会直接：

1. 取 `response.choices` 中的文本；
2. 写入 issue generation 日志；
3. 把第一条响应作为最终 `problem_statement` 写入数据集。

中间没有独立检查以下问题：

- 是否提到 patch、diff、测试、CI 或 synthetic benchmark；
- 是否说出了代码被删除、修改、遗留或恢复；
- 是否暴露被删除语句中的私有变量、内部方法或控制流；
- 是否推断了根因或给出修复方法；
- 是否应当隔离而不是进入训练数据。

本次原版运行已经产生了实际反例。它在 issue 中写出“recent refactor”、
“environment variable lookup ... removed”以及被删除赋值涉及的
`trace_modules_str`。这些内容违反了上游自己的 prompt，但仍被成功写入最终 JSON。

这种 fail-open 行为的主要风险不是文字风格不好，而是训练任务被污染：模型可能从 issue
直接恢复隐藏补丁，不再需要根据行为定位缺陷。

## 3. 已实施的轻量、可批量审核闸门

新增：`scripts/audit-problem-statements.py`

设计原则是“全量规则扫描，少量疑似项单次语义复核，任何未确认项目都隔离”，不再采用
多模型、多轮改写流水线。

### 第一级：确定性规则，全量运行

每条记录只进行本地字符串、正则和补丁标识符扫描，检查：

- 私有构造物：patch、diff、mutation、SWE-smith 等；
- 测试泄露：pytest、test suite、测试路径、测试函数名、CI 等；
- 代码变化披露：removed、deleted、recent refactor、left in place 等；
- 修复指令：restore、add back、reimplement、fix by 等；
- 删除行标识符与 issue 文本的重合；
- 空文本和异常长度。

高置信度违规直接标记为 `reject`，不调用模型。

公共环境变量、复现脚本里定义的局部属性和公开关键字参数不会仅因出现在补丁中就触发
模型复核，减少批量误报。

### 第二级：单次语义复核，仅处理规则不能定性的项目

只有状态为 `needs_semantic_review` 的项目才可通过
`--review-ambiguous` 调用一次 Qwen。审核器只做“是否泄露隐藏实现/根因/修复”的判定，
不重新生成、不自动改写，也不串联第二个模型。

如果模型调用失败或结果不确定，状态保持 `needs_review`，不会进入通过集。

### 第三级：强制拆分输出

审核后可同时输出：

- `--accepted-output`：只有 `final_status == accept` 的原始记录；
- `--quarantine-output`：拒绝、未决、审核失败的记录；
- audit JSONL：每条规则命中、候选文本哈希、语义判定；
- summary JSON：总数、状态计数、接受和隔离的 instance id。

使用 `--fail-on-findings` 时，只要存在非 accept 项，进程以状态码 2 退出，可直接接入批处理
或 CI，避免不合格数据被误认为成功产物。

复杂度为 `O(N)` 本地扫描加 `O(A)` 次模型调用，其中 `A` 仅为模糊项目数，正常情况下
远小于总记录数。这比“每条都生成 + 两个审核模型 + 可能再改写”的固定多调用方案更适合
几百到几千条数据。

## 4. 已执行结果

回归测试：

```text
5 passed in 0.02s
```

原版 SWE-smith 新生成的 1 条：

```text
accept: 0
reject: 1
semantic review calls: 0
```

它被规则直接隔离，命中包括：

- `implementation_change_disclosure`；
- `internal_mechanism_wording`；
- `removed_identifier_mentioned`；
- `changed_identifier_with_causal_or_change_clue`。

此前生成并审核过的 7 条：

```text
accept: 7
quarantine: 0
semantic review calls: 0
```

规则优化后，公开环境变量、关键字参数和示例局部属性不再产生不必要的模型调用。

服务器产物目录：

```text
/data/results/problem-statement-audit/
```

## 5. 推荐的正式生产顺序

```text
SWE-smith issue_gen
        |
        v
原始候选 JSON/JSONL（不可直接用于训练）
        |
        v
确定性全量审核
   |             |
 reject       ambiguous
   |             |
隔离区       单次语义复核
                 |
          accept / 隔离区
                 |
                 v
          accepted JSONL
```

正式批量运行时建议额外保留 1%～5% 的已通过项目进行人工抽样，用来估计规则漏检率；抽样
是质量度量，不应在每条数据上调用 Codex。若抽样发现新的稳定泄露模式，应把它转成新的
确定性规则并加入回归测试。

## 6. 当前边界

这条轻量闸门解决的是“隐藏补丁、根因和修复提示泄露”，并不宣称完成全部事实性验证。
它不会证明复现代码一定可运行，也不会证明 expected behavior 完全正确。后续如果要增加
事实性检查，应优先复用 SWE-smith 已有验证日志做确定性核对，而不是恢复为每条多模型审核。
