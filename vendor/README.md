# 第三方源码

`SWE-smith/` 是 [SWE-bench/SWE-smith](https://github.com/SWE-bench/SWE-smith) 的源码快照，固定于官方提交 `9b74ac08118a85c39c356802f7961893af73e07f`，并带有本实验的本地补丁。它是本仓库的普通文件目录，不是 Git 子模块，也不含上游 `.git` 目录。

`SWE-agent/` 是 [SWE-agent/SWE-agent](https://github.com/SWE-agent/SWE-agent) 的 `v1.1.0` 源码快照，包含本实验的输出文件命名补丁；许可证见 [`SWE-agent/LICENSE`](SWE-agent/LICENSE)，补丁见 [`patches/SWE-agent/output-artifacts.patch`](../patches/SWE-agent/output-artifacts.patch)。

`mini-swe-agent/` 是 [SWE-agent/mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) 的 `v2.4.6` 源码快照；许可证见 [`mini-swe-agent/LICENSE.md`](mini-swe-agent/LICENSE.md)。

两个 Agent 的源码随仓库保存，但 Python 虚拟环境、模型、数据和 Docker 镜像仍须在每台机器本地准备。不要把 `vendor/` 当作可直接运行的虚拟环境。

上游许可证为 MIT，全文见 [`SWE-smith/LICENSE`](SWE-smith/LICENSE)。本地改动、旧实验基线及注意事项见 [`patches/SWE-smith/README.md`](../patches/SWE-smith/README.md)。更新上游快照时必须重新检查补丁、依赖版本和实验行为。
