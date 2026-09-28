# Python 依赖

通过仓库根目录的 `bash scripts/bootstrap-python.sh` 创建两个独立环境。脚本先安装内置 SWE-smith 的 `generate,validate,test` 依赖，再安装对应的附加依赖；两次安装都使用 `constraints.txt`。

| 文件 | 用途 |
| --- | --- |
| `constraints.txt` | 两个环境共用的 SWE-bench 版本约束。 |
| `core.txt` | 程序化生成、Docker 验证和 DeepSeek 问题描述审核所需的附加包。 |
| `llm.txt` | 可选的 LiteLLM Bug 生成或官方 issuegen 所需的附加包。 |

完整的 `pip freeze` 由安装脚本写到本机数据目录的 `results/env-manifests/`，不作为安装输入。
