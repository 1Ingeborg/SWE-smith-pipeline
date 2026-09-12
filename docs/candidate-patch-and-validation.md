# SWE-smith：Candidate Patch、任务来源与 Validation

## 1. 核心概念

SWE-smith 中的 `candidate patch` 通常不是修复补丁，而是用于向正确代码中注入缺陷的 **bug patch**。

整体流程如下：

```text
正确代码仓库
  ↓ 应用 candidate bug patch
带 bug 的代码仓库
  ↓ 运行测试并确认已有行为被破坏
有效的合成任务
  ↓ 根据代码变化和失败测试生成问题描述
problem statement + 带 bug 的仓库
  ↓ Agent 尝试修复
solution patch
  ↓ Evaluation
判断修复是否成功且没有引入回归
```

一个任务中可能涉及三类 patch：

| Patch | 作用 |
|---|---|
| Candidate bug patch | 向原始正确代码中注入 bug |
| Gold patch | 撤销注入的 bug，作为参考修复 |
| Model solution patch | Agent 实际生成的修复补丁 |

## 2. Candidate Patch 示例

原始正确代码：

```python
def divide(a, b):
    if b == 0:
        raise ValueError("division by zero")
    return a / b
```

SWE-smith 生成的 candidate bug patch：

```diff
 def divide(a, b):
-    if b == 0:
-        raise ValueError("division by zero")
     return a / b
```

应用后，原本验证除零行为的测试会失败。这个行为变化可以被转化成一个任务：

> 当除数为 0 时，函数没有抛出正确异常，请修复该问题。

Agent 接收到的是已经应用上述 bug patch 的仓库。Agent 需要生成新的 solution patch，使失败测试恢复通过。

## 3. Candidate Patch 的生成依据

这里的“生成依据”可以拆成三个问题：

1. 系统根据什么选择要修改的代码位置？
2. 系统根据什么决定如何修改？
3. 系统如何判断这次修改值得保留？

SWE-smith 主要提供五种思路：Procedural Modification、LM Modify、LM Rewrite、PR Mirroring 和 Combining Bugs。

| 方法 | 选择修改位置的依据 | 如何修改 | 是否需要大模型 | 适合用途 |
|---|---|---|---|---|
| Procedural Modification | 函数、类、条件、循环等代码结构 | 应用固定的 AST 变异规则 | 否 | 低成本、大批量造简单 bug |
| LM Modify | 选中的函数或类 | 大模型进行局部修改 | 是 | 生成比固定规则更自然的 bug |
| LM Rewrite | 选中的函数或类 | 大模型重写整个代码实体 | 是 | 生成语义变化更大的 bug |
| PR Mirroring | 真实 issue、PR 和提交历史 | 撤销真实修复 | 是，通常用于辅助反向修改 | 构造更接近真实开发场景的数据 |
| Combining Bugs | 已通过验证的简单 patch | 合并同一文件或模块中的多个 bug | 否 | 提高任务复杂度 |

### 3.1 几个基础术语

- `函数（function）`：一段完成特定功能的代码，例如 `calculate_total()`。
- `类（class）`：把数据和相关操作组织在一起的代码结构。
- `方法（method）`：定义在类里面的函数。
- `代码实体（programmatic entity）`：函数、类、方法等可以单独分析和修改的代码单元。
- `AST`：抽象语法树，是计算机理解代码结构的形式。例如系统可以准确识别 `if`、`for`、比较运算符和函数调用。
- `变异规则（mutation rule）`：人为定义的造错规则，例如把 `>=` 改成 `>`。
- `diff`：修改前后代码的差异记录。
- `candidate`：候选项，表示它刚被生成，还没有证明是有效任务。

测试在普通流程中主要负责筛选，而不是决定具体改哪一行：

```text
代码规则或大模型生成 candidate patch
  ↓
Validation 运行测试
  ↓
保留能够稳定破坏已有测试的 patch
```

### 3.2 Procedural Modification：按照固定规则造错

Procedural Modification 根据代码结构和预定义变异规则生成 bug。

基本过程：

1. 找到仓库中的函数、类和方法。
2. 将代码解析为 AST。
3. 判断代码实体是否满足某条规则的适用条件。
4. 修改 AST 并重新生成代码。
5. 比较修改前后的代码，生成 candidate patch。

#### 例子一：修改边界条件

原始代码：

```python
def is_adult(age):
    return age >= 18
```

应用“把 `>=` 替换为 `>`”的规则：

```diff
 def is_adult(age):
-    return age >= 18
+    return age > 18
```

修改后，18 岁会被错误判断为未成年。

生成依据是：

```text
代码中存在 >= 运算符
+
预设变异规则允许将 >= 改成 >
```

#### 例子二：删除条件保护

原始代码：

```python
def get_first(items):
    if not items:
        return None
    return items[0]
```

应用“删除 `if` 条件分支”的规则后：

```python
def get_first(items):
    return items[0]
```

输入空列表时，函数会抛出 `IndexError`。

#### 例子三：修改布尔逻辑

```diff
 def can_access(is_admin, is_owner):
-    return is_admin or is_owner
+    return is_admin and is_owner
```

原来满足任意一个条件即可访问，修改后要求两个条件同时满足。

#### 例子四：修改常量

```diff
 def calculate_timeout(retries):
-    return retries * 30
+    return retries * 3
```

这个修改是否有效，取决于现有测试是否检查超时时间。如果没有任何测试失败，它会在 validation 阶段被淘汰。

Procedural Modification 的特点：

- 优点：速度快、成本低、结果容易解释和复现。
- 缺点：生成的 bug 可能比较机械，真实感有限。

### 3.3 LM Modify：让大模型做局部错误修改

LM Modify 会选择一个函数或类，让大模型在保持整体结构的情况下引入一个局部 bug。

提示词的含义可能类似：

```text
请对下面函数进行小范围修改，引入一个合理且可修复的 bug。
不要修改函数签名，不要产生语法错误，不要删除整个函数。
```

原始代码：

```python
def normalize_score(score):
    if score < 0:
        return 0
    if score > 100:
        return 100
    return score
```

大模型可能生成：

```diff
     if score > 100:
-        return 100
+        return score
```

此时输入 `101` 会错误返回 `101`，而不是被限制为 `100`。

生成依据是：

```text
选中的函数代码
+
bug 生成提示词
+
配置中的修改范围和质量约束
+
大模型对代码语义的判断
```

大模型生成 patch 不代表 patch 一定有效。如果仓库没有测试 `score > 100` 的情况，这个候选仍会被 validation 淘汰。

LM Modify 的特点：

- 优点：局部修改容易控制，bug 通常比固定规则更自然。
- 缺点：需要模型调用成本，结果不完全稳定，仍可能生成无效修改。

### 3.4 LM Rewrite：让大模型重写整个代码实体

LM Rewrite 会让大模型重新实现整个函数或类，因此变化通常比 LM Modify 更大。

原始代码：

```python
def unique_items(items):
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result
```

大模型重写为：

```python
def unique_items(items):
    return list(set(items))
```

新实现可以去重，但可能破坏元素的原始顺序：

```python
unique_items(["b", "a", "b"])
```

原实现返回 `["b", "a"]`，重写后可能返回 `["a", "b"]`。如果测试要求保持顺序，这个变化就会被检测出来。

LM Rewrite 的特点：

- 优点：容易产生重构、算法替换等更复杂的语义错误。
- 缺点：修改范围较大，容易产生不相关变化，数据清洗难度更高。

### 3.5 PR Mirroring：撤销真实修复

PR Mirroring 使用真实 GitHub PR 构造任务。

假设真实项目中曾出现以下错误代码：

```python
def parse_name(name):
    return name.split(" ")
```

对应 issue 是：

> 用户名中包含连续空格时，解析结果会出现空字符串。

开发者通过 PR 修复为：

```python
def parse_name(name):
    return name.split()
```

PR Mirroring 在修复后的版本上撤销这次修改，把代码恢复成错误状态，从而重新引入真实 bug。

生成依据是：

```text
真实 issue
+
真实修复 PR
+
仓库提交历史
+
对修复改动的反向操作
```

候选 PR 通常需要至少关联一个 issue，并且修改过代码文件。

PR Mirroring 的特点：

- 优点：bug 和问题描述来自真实开发过程，真实性较高。
- 缺点：仓库历史、依赖和环境可能复杂，反向修改也不一定能够稳定恢复原 bug。

### 3.6 Combining Bugs：组合多个已验证 Bug

Combining Bugs 会把多个已经通过 validation 的简单 patch 合并为一个更复杂的任务。

例如已有两个有效 patch：

```text
Patch A：把订单金额边界 >= 改为 >
Patch B：删除优惠券为空时的保护逻辑
```

如果它们位于同一个文件或同一个模块中，而且能够顺利一起应用，系统可以将它们组合为一个 candidate patch。

生成依据是：

```text
已经通过验证的简单 patch
+
相同文件或模块关系
+
patch 之间没有应用冲突
```

Combining Bugs 的特点：

- 优点：可以构造需要多处定位和修改的复杂任务。
- 缺点：多个错误可能互相影响，问题描述和测试归因更困难。

### 3.7 如何选择生成方法

第一次体验 SWE-smith 数据构造时，建议按以下顺序进行：

1. 先用 Procedural Modification 生成 20 个候选，理解 patch 和 validation。
2. 再用 LM Modify 生成 10～20 个候选，对比规则造错和模型造错。
3. 环境稳定后尝试 PR Mirroring，观察真实 issue、修复 PR 和反向 patch 的关系。
4. 最后尝试 LM Rewrite 和 Combining Bugs，因为这两种方法的清洗难度更高。

可以用下面的判断快速选择：

```text
想低成本跑通流程       → Procedural Modification
想生成更自然的局部 bug  → LM Modify
想生成较大的语义变化     → LM Rewrite
想获得更真实的开发问题   → PR Mirroring
想提高任务复杂度         → Combining Bugs
```

## 4. Problem Statement 从哪里来

对于 Procedural Modification 和 LM Generated 数据，常见顺序是：

```text
先生成 bug patch
  ↓
通过测试确定被破坏的行为
  ↓
再生成 problem statement
```

问题描述可以来自：

- 大模型根据代码差异、测试信息和任务元数据生成；
- 从 `FAIL_TO_PASS` 测试名称或失败信息中提取；
- 使用静态模板生成；
- 对于部分 PR Mirror，使用原始 GitHub issue。

高质量 problem statement 应满足：

- 描述可观察到的错误行为；
- 提供足够的定位信息，但不直接给出答案；
- 不暴露 gold patch、具体修改行或内部生成过程；
- 与实际失败测试保持一致。

## 5. Validation 如何判断 Patch 有效

Validation 的核心是对同一套测试做前后对照。

### 5.1 运行原始仓库测试

在没有应用 bug patch 时运行测试：

```text
test_divide_normal   PASS
test_divide_by_zero  PASS
test_other_feature   PASS
```

这一步确认：

- Docker 环境可用；
- 依赖安装正确；
- 测试命令正确；
- 原始仓库在当前环境下基本健康。

### 5.2 应用 Candidate Bug Patch

应用 patch 后，再运行相同测试：

```text
test_divide_normal   PASS
test_divide_by_zero  FAIL
test_other_feature   PASS
```

由此生成：

```json
{
  "FAIL_TO_PASS": ["test_divide_by_zero"],
  "PASS_TO_PASS": [
    "test_divide_normal",
    "test_other_feature"
  ]
}
```

字段是站在后续修复任务的角度命名的：

- `FAIL_TO_PASS`：带 bug 时失败，Agent 修复后必须通过的测试。
- `PASS_TO_PASS`：带 bug 时通过，Agent 修复后仍必须通过的测试。

SWE-smith 的收集阶段要求任务至少包含：

- 1 个 `FAIL_TO_PASS`；
- 1 个 `PASS_TO_PASS`。

### 5.3 使用 Gold Patch 做 Sanity Check

对有效候选还可以执行：

```text
带 bug 的仓库
  ↓ 应用 gold patch
FAIL_TO_PASS 全部恢复通过
PASS_TO_PASS 继续通过
```

如果 gold patch 都不能稳定恢复测试，说明任务、测试映射、环境或 patch 本身存在问题。

## 6. 为什么会生成 200 个 Candidates

`200` 不是 SWE-smith 的固定要求，只是一个适合小规模实验的候选预算。

生成的 candidate 不会全部成为训练数据，常见淘汰原因包括：

- patch 无法正确应用；
- 修改后出现语法错误或导入错误；
- 没有任何测试失败，说明测试没有覆盖该变化；
- 原始仓库测试本身无法稳定运行；
- 导致大量无关测试失败；
- 测试超时或资源消耗异常；
- 测试具有随机性，结果不能复现；
- bug 太简单、太怪异或缺乏真实感；
- problem statement 泄露答案或与实际行为不一致。

一个假设性的数据漏斗如下：

```text
200 个 candidate patches
  ↓ patch 应用、语法和执行检查
160 个可执行候选
  ↓ 测试能够检测到行为变化
70 个至少破坏一个测试
  ↓ 稳定性、质量和泄露过滤
40～60 个有效任务
```

实际有效率取决于仓库质量、测试覆盖率和 bug 生成方式，不能事先固定。

更合理的渐进方式是：

1. 先生成 20 个，确认端到端流程能够运行。
2. 再生成 50 个，统计每类淘汰原因。
3. 环境和规则稳定后扩大到 200 个。
4. 根据真实有效率反推后续候选数量。

例如目标是获得 100 个有效任务，试运行得到有效率为 25%，则候选预算约为：

```text
100 / 25% = 400 个 candidates
```

## 7. Validation 不能证明什么

测试验证只能说明：

> 该 patch 产生了可被现有测试检测、并有机会被修复的行为变化。

它不能独立证明：

- bug 符合真实开发场景；
- problem statement 准确且清楚；
- 任务难度适合目标模型；
- gold patch 是唯一合理解；
- 测试覆盖了完整语义；
- Agent 不会通过删除测试或修改配置作弊。

因此还需要额外的数据清洗和人工质检。

## 8. 建议增加的质量检查

- 对同一任务重复运行测试 2～3 次，过滤 flaky tests。
- 原始仓库的 baseline 测试必须稳定通过。
- 限制 candidate patch 修改测试文件、CI 配置和依赖锁文件。
- 限制修改文件数、代码行数和 patch 大小。
- 过滤纯格式化、注释、重命名和无行为变化的 patch。
- 检查是否出现大面积无关测试失败。
- 检查 problem statement 是否泄露文件名、函数名或修改方案。
- 人工抽查 bug patch、失败日志、problem statement 和 gold patch。
- 使用一个 Agent 实际求解部分任务，判断任务是否可理解、可定位、可修复。
- 为每个被拒绝样本保留明确的 `reject_reason`。

## 9. 推荐的第一次实验

第一次不需要直接追求大规模训练数据，可以选择一个测试完善的 Python 仓库：

```text
1 个仓库
  ↓
生成 20 个 procedural candidates
  ↓
运行 validation
  ↓
统计有效率和淘汰原因
  ↓
人工检查 5～10 个有效任务
  ↓
为有效任务生成 problem statement
  ↓
用 gold patch 做 evaluation sanity check
```

建议记录以下指标：

| 指标 | 说明 |
|---|---|
| candidate_count | 生成的候选总数 |
| patch_apply_rate | patch 成功应用比例 |
| baseline_pass_rate | 原始仓库测试稳定通过比例 |
| test_break_rate | 应用 bug patch 后至少破坏一个测试的比例 |
| valid_rate | 最终有效任务比例 |
| flaky_rate | 多次运行结果不一致的比例 |
| issue_leakage_rate | 问题描述泄露答案的比例 |
| top_reject_reasons | 最常见的淘汰原因 |

## 10. 总结

SWE-smith 的主要合成流程不是“根据需求生成修复 patch”，而是：

> 从正确仓库中生成注入 bug 的 candidate patch，通过测试验证它确实破坏了已有行为，然后为这个行为变化生成问题描述，最终让 Agent 学习如何修复。

候选数量应由目标有效任务数和实际有效率决定。`200` 只是适合流程稳定后进行小规模统计的实验量，不是固定标准。

## 参考资料

- [SWE-smith：Create Instances](https://swesmith.com/guides/create_instances/)
- [SWE-smith：Validation & Evaluation](https://swesmith.com/guides/harnesses/)
- [SWE-smith：Generate Issue Text](https://swesmith.com/guides/issue_gen/)
- [SWE-smith GitHub](https://github.com/SWE-bench/SWE-smith)
