#!/bin/bash
# SPDX-License-Identifier: MIT
#
# Legacy generic workflow hook, sourced by Scripts/Packages.sh.
# The three dedicated device workflows use DEVICE-CORE and execute only their
# own Scripts/<device>/private.sh in a separate process; they do not use this
# scanner. Keep this hook for generic workflows such as WRT-TEST.
# Each sourced hook guards WRT_CONFIG; reset shell options between hooks.

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
