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

set +e

for _ext in "${GITHUB_WORKSPACE:-/github/workspace}"/Scripts/*/private.sh; do
	[ -f "$_ext" ] || continue
	# 每次 source 前后都重置，防止某个 private.sh 的 set -e 影响其它项目
	set +e
	# shellcheck disable=SC1090
	source "$_ext"
	set +e
done

unset _ext
return 0 2>/dev/null || exit 0
