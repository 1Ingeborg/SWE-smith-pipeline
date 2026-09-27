# 实验工作流

本目录收纳原先散落在服务器 `/data/configs` 中的可复现脚本、配置和题目清单：

- `mini-sweagent/`：DeepSeek mini-agent rollout、SFT 导出和小规模 Verified 检查。
- `verified50/`：固定 50 题的 SWE-agent/mini-swe-agent 启动、拆分、合并与自动评测。
- `swebench-eval/`：早期 SWE-agent 批量评测入口。

`verified50/verified_50_ids.txt` 与 `verified50/verified_50_manifest.json` 是 50 题的唯一权威文件。`mini-sweagent/verified_eval/` 中的同名路径是相对软链接，不维护第二份清单。

旧命令仍使用 `/data/configs/<工作流>`。服务器上的这些路径已改为指向本目录的软链接；在新机器上先运行 `bash tools/setup/link-legacy-paths.sh`。运行时标记、GPU 拆分计划、Python 缓存和旧备份留在服务器，但由 `.gitignore` 排除，不当作可复现配置提交。

本目录中的部分脚本仍包含 `/data/...` 绝对路径。复现前需按 `README.md` 建立依赖目录和链接；后续可逐步参数化路径，在确认与旧实验结果一致后再移除兼容链接。
