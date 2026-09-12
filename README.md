# SWE-smith Lab

用于复现 SWE-smith 数据构造、清洗、验证、训练数据整理和 bad case 迭代的实验工程。

本仓库只保存可复现所需的小文件，不保存私钥、API Token、Docker 镜像、虚拟环境、模型、原始数据集、轨迹或运行日志。

## 当前架构

```text
腾讯云 CPU CVM
  - SWE-smith / SWE-bench
  - Docker 仓库环境
  - candidate patch 生成与 validation
  - 数据清洗和评测

AutoDL GPU
  - 7B-9B 模型训练
  - vLLM/SGLang 推理服务
  - 通过 SSH 隧道向 CPU 节点提供 OpenAI-compatible API
```

## 已固定版本

- Ubuntu 22.04 LTS
- Python 3.10
- SWE-smith commit: `9b74ac08118a85c39c356802f7961893af73e07f`
- SWE-bench: `4.1.0`

SWE-smith 当前提交仍使用 `swebench.harness.constants.DOCKER_USER`。该符号在 SWE-bench 5.x 中不可用，因此 CPU 环境暂时固定为 SWE-bench 4.1.0。

## 新服务器恢复

先将数据盘挂载到 `/data`，然后安装 Git 并克隆本仓库：

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/1Ingeborg/swe-smith-lab.git /data/repos/swe-smith-lab
cd /data/repos/swe-smith-lab
```

私有仓库需要先在新服务器配置 GitHub 身份，或者从已登录的本机完成克隆和传输。

检查服务器：

```bash
bash scripts/check-host.sh
```

首次初始化宿主机 Docker：

```bash
DOCKER_REGISTRY_MIRROR=https://mirror.ccs.tencentyun.com \
  bash scripts/bootstrap-host.sh
```

安装 SWE-smith Python 环境：

```bash
bash scripts/bootstrap-python.sh
```

验证安装：

```bash
bash scripts/check-install.sh
```

## 当前服务器目录

```text
/data/repos/swe-smith-lab   本实验仓库
/data/repos/SWE-smith       SWE-smith 上游源码
/data/venvs/swesmith        Python 虚拟环境
/data/docker                Docker 数据
/data/containerd            containerd 数据
/data/datasets              数据集
/data/tasks                 构造后的任务
/data/trajectories          Agent 轨迹
/data/results               validation / evaluation 结果
/data/huggingface           Hugging Face 缓存
/data/cache/pip             pip 缓存
```

## 首轮实验

首轮只使用一个 Python 仓库和程序化修改，不调用大模型：

```text
1 个仓库
10 个 candidate patches
2 个 validation workers
人工检查有效与无效样本
```

执行前阅读 [首轮实验说明](docs/first-experiment.md)。
