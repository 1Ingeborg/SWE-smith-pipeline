# 新服务器环境配置

仓库自带 `vendor/SWE-smith`、`vendor/SWE-agent`（1.1.0，含本地输出命名补丁）和 `vendor/mini-swe-agent`（2.4.6）的源码。Git 克隆完成后，不需要再次克隆这三个上游仓库；但源码不等于可运行环境。

## 需要自己准备什么

- Linux x86_64、Git、Docker 服务和足够的磁盘空间。候选 Bug 验证、任务准备及评测均使用 Docker；确认当前用户能运行 `docker info`。
- 系统 Python 3.10+、`python3-venv`、pip。SWE-agent 安装脚本会用 uv 在数据目录内安装独立 Python 3.11；mini-swe-agent 使用系统 `python3`，如需换解释器可设置 `MINI_SWE_AGENT_PYTHON`。
- 可访问 Python 包索引、所选仓库的基础 Docker 镜像来源，以及所用模型 API 或本地推理服务。`configs/task_generation/*.yaml` 的 `source_image_name` 是基础镜像来源；`run-agent.sh --stage prepare` 在它们之上制作任务镜像。源码仓库不包含镜像。
- 实验输入数据。`results/` 中的真实任务、轨迹和评测默认不提交 Git；若要复现实验，需要先运行任务生成，或另外取得对应的输入数据与镜像。仅克隆代码不能直接重现历史 `run-id`。

## 从仓库安装

```bash
git clone https://github.com/1Ingeborg/swe-smith-lab.git
cd swe-smith-lab
cp .env.example .env
# 编辑 .env，按本机路径设置 SWE_LAB_DATA_ROOT，并在需要模型调用时填入 DEEPSEEK_API_KEY。
bash scripts/bootstrap-python.sh
bash tools/setup/bootstrap-swe-agent.sh
bash tools/setup/bootstrap-mini-swe-agent.sh
bash scripts/check-install.sh
```

四个 Python 环境位于 `SWE_LAB_DATA_ROOT/venvs/`（未设置时为仓库内 `.local/venvs/`），互不覆盖。安装脚本保存本机依赖列表到数据目录的 `versions/`，不把机器专属环境写回 Git。网络较慢时可用 `PIP_INDEX_URL`、`PIP_TRUSTED_HOST` 指定本机 pip 镜像；SWE-agent 的 uv 安装对应使用 `SWE_AGENT_UV_INDEX_URL`、`SWE_AGENT_UV_INSECURE_HOST`。

`bash scripts/check-install.sh` 会检查模块、Docker，并运行一项不调用模型的 SWE-smith 小测试。之后先用 `bash scripts/run-task-generation.sh --config configs/task_generation/smoke.yaml --run-id smoke-new --dry-run` 检查配置。实际任务生成、Agent rollout 和评测会分别使用 Docker、磁盘及可能的付费 API；运行 Agent 前需明确选择 `mini-swe-agent` 或 `swe-agent`，并在允许模型调用时显式加 `--allow-api-calls`。

不要提交 `.env`、虚拟环境、Docker 数据目录、模型权重或完整 `results/`。如需公开可复现样例，优先发布脱敏的小数据样例、配置快照、上游镜像名及可验证的版本信息，而不是服务器文件系统的压缩包。
