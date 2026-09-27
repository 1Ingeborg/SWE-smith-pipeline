#!/usr/bin/env bash
# [PATCHED for SWE-smith pipeline]
#
# 原版：
#   pip install 'tree-sitter==0.21.3'
#   pip install 'tree-sitter-languages'
#
# 问题：SWE-agent 在 setup 阶段会先激活 repo 的 testbed conda 环境，
# 于是这里的 `pip` 解析到的是 testbed 的旧 pip。SWE-bench Verified 里
# 大量实例的 testbed 是 Python 3.5 甚至更老（如 django__django-10097 是
# Python 3.5 + pip 10.0.1），而 tree-sitter 0.21.3 要求 >=3.8，
# pip 10 也认不出新 wheel 格式 —— 必然失败。
#
# 而 tools.py:_install_commands 里 check="raise" 是写死的，
# 装不上就直接中止整个实例，没有容忍余地。
#
# 改为装到 SWE-ReX 的独立 Python（/root/python3.11，由派生的镜像提供）。
# 配套：bin/str_replace_editor 与 bin/_state_anthropic 的 shebang 也已
# 指向同一个解释器，否则工具跑在 testbed 的 3.5 上仍然 import 不到 tree_sitter。
# 这样 USE_FILEMAP 可以保持 true，与 SFT 训练时一致。

PY=/root/python3.11/bin

if [ ! -x "$PY/pip3" ]; then
    echo "install.sh: 未找到 $PY/pip3，跳过 tree-sitter 安装" >&2
    exit 1
fi

"$PY/pip3" install 'tree-sitter==0.21.3' 'tree-sitter-languages'
