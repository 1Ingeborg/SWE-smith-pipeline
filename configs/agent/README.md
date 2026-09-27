# Agent 原生配置

这里仅保留当前任务生成实验使用的两份框架原生配置。选择模型、API 地址、温度、调用上限与并发时，请编辑 `configs/rollout/<实验>.yaml`，不要在此重复填写。

| 文件 | 用途 |
| --- | --- |
| `swe-agent.yaml` | SWE-agent 的 SWE-smith 任务提示词、工具与容器内编辑命令。 |
| `mini-swe-agent.yaml` | mini-swe-agent 的步数、容器和 LiteLLM 文本交互方式的基础设置。 |
