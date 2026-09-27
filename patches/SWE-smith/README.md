# 内置 SWE-smith 源码与本地改动

`vendor/SWE-smith/` 已纳入本仓库 Git 跟踪，不是子模块，也不需要单独克隆或上传。它来自官方 [SWE-bench/SWE-smith](https://github.com/SWE-bench/SWE-smith) 的公开提交 `9b74ac08118a85c39c356802f7961893af73e07f`，按原 [MIT 许可证](../../vendor/SWE-smith/LICENSE) 再分发。

`working-tree.patch` 是先前实验对生成器、测试路径处理及部分仓库配置的本地修改；它已经应用到 `vendor/SWE-smith/`。`untracked/` 保存当时另外两个本地文件，它们也已复制到内置源码目录。保留补丁和原文件仅供审计，不要再次应用补丁，否则会重复修改。

内置源码的 `tests/profiles/test_base.py` 还对镜像文件列表加入了单测 mock，避免测试时实际访问 Docker 或网络。这是导入时追加的测试修正，不在旧 `working-tree.patch` 中。

先前实验记录使用的上游基线是 `ad37c380ec8c7b775cdb11a074e408f90c874dad`；该提交目前不能从官方 GitHub 按提交号获取。现有补丁能直接应用到公开提交 `9b74ac0...`，但这不等于两个上游版本在所有行为上相同。新实验应记录内置版本并先跑冒烟测试。
