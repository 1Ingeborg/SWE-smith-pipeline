# 实验结果目录

新实验按 `results/<run-id>/` 保存流水线产物、任务准备、两种 Agent 轨迹和测评。
`run-id` 标识一批题目；`rollout-id` 标识其中一次模型、温度和框架实验。

```text
<run-id>/
  meta/                       # 整批 manifest 与配置快照
  single/                     # 单 Bug 的候选、验证、工作区与日志
    candidates/               # 各仓库候选，以及 candidates.jsonl 和 summary
    validation/               # 各仓库验证，以及 accepted.jsonl 和 summary
    workspace/
    logs/
  combine/                    # 可选的 Combine，和 single 完全隔离
    candidates/
    validation/               # 各仓库验证，以及 accepted.jsonl 和 summary
    workspace/
    logs/
  issuegen/
  task-prep/<run-id>/
  rollouts/swe-agent/<rollout-id>/
  rollouts/mini-swe-agent/<rollout-id>/
  evaluations/swe-agent/<rollout-id>/
  evaluations/mini-swe-agent/<rollout-id>/
```

本目录只跟踪此说明文件，所有生成数据由 `.gitignore` 排除。历史结果继续位于
`/data/results`，已完成的旧布局 run 也不迁移。旧布局仍可读取、续跑。
新布局的问题描述输入由 single 的完整验证结果和可读、已完成的 combine 验证结果构成；
combine 不可用时跳过并在 `meta/manifest.json` 的 `issue_input` 中记录原因和数量。
运行方式见仓库根目录 README。
