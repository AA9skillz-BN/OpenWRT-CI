#!/bin/bash
# SPDX-License-Identifier: MIT
#
# 扩展分发器（2026-10-25 改为目录扫描，各机型互不干扰）
#
# 由 Scripts/Packages.sh 末尾的官方扩展钩子 source：
#   if [ -f "$GITHUB_WORKSPACE/Scripts/PRIVATE.sh" ]; then source ...; fi
#
# 本文件只做一件事：把 Scripts/*/private.sh 全部 source 一遍。
# 每个 private.sh 自己判断 WRT_CONFIG，不属于自己的直接 return，
# 所以各机型（AP3000M / TR3000 / …）可以各自改自己的文件，不会互相覆盖。
#
# 历史教训：原先所有机型逻辑都堆在本文件里，两个会话同时改这一份时，
# 后提交的会把先提交的整段覆盖掉（TR3000 分支就这样丢过一次，构建全绿但产物残缺）。
#
# 2026-10-25 加固：每次 source 后不仅复位 -e，也复位 -u 与 pipefail。
# 因为 `set -euo pipefail` 里的 -u / pipefail 会残留在同一个 shell 中：
# 若某个 private.sh 设了它们，按字母序排在它后面的机型会继承 nounset，
# 出现"某个变量没定义就静默中断构建"的怪问题。

set +e

for _ext in "${GITHUB_WORKSPACE:-/github/workspace}"/Scripts/*/private.sh; do
	[ -f "$_ext" ] || continue
	# 每次 source 前后都复位 shell 选项，防止某个 private.sh 的副作用影响其它项目
	set +e
	set +u
	# shellcheck disable=SC1090
	source "$_ext"
	set +e
	set +u
	set +o pipefail
done

unset _ext
return 0 2>/dev/null || exit 0
