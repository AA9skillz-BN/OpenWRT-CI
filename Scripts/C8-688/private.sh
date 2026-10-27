#!/bin/bash
# SPDX-License-Identifier: MIT
# C8-688 机型钩子：把 mainline immortalwrt 里缺失的 C8-688 设备支持注入进去。
#
# 约定（Scripts/README.md）：
#   1) 守卫在 set -e 之前；不属于自己就 return
#   2) 变量统一 _c8_ 前缀
#   3) 由公共核心通过独立 bash 进程执行，失败立即终止
#
# 注入点（全部带幂等判断，锚点缺失就 fail fast，避免"编出来是个假固件"）：
#   A. target/linux/mediatek/dts/mt7981b-nradio-c8-688.dts        ← 整文件投放
#   B. target/linux/mediatek/image/filogic.mk                     ← 追加设备定义
#   C. target/linux/mediatek/filogic/base-files/etc/board.d/02_network
#        C1. LAN/WAN 分支加 nradio,c8-688
#        C2. MAC 分支加 nradio,c8-688（fac_mac 从 eMMC 的 bdinfo 分区读）
#   D. Files/C8-688 覆盖层（风扇曲线脚本 + 首启）→ package/base-files/files/

if [[ "${WRT_CONFIG:-}" != "C8-688" ]]; then
	return 0 2>/dev/null || exit 0
fi

set -euo pipefail

_c8_src="${GITHUB_WORKSPACE:-/github/workspace}/Scripts/C8-688"
_c8_dst="./target/linux/mediatek"

_c8_die() { echo "::error::C8-688 注入失败：$*" >&2; exit 1; }

# Source selection is complete. Keep upstream firewall4 patches intact.
# auto follows the branch; fullcone was repaired upstream in 21ae803c.

# ---- A. DTS ---------------------------------------------------------------
_c8_dts="$_c8_dst/dts/mt7981b-nradio-c8-688.dts"
[ -f "$_c8_src/dts/mt7981b-nradio-c8-688.dts" ] ||
	{ _c8_die "缺少 $_c8_src/dts/mt7981b-nradio-c8-688.dts"; }
[ -d "$_c8_dst/dts" ] || { _c8_die "上游没有 $_c8_dst/dts（源码结构变了？）"; }
cp -f "$_c8_src/dts/mt7981b-nradio-c8-688.dts" "$_c8_dts"
	echo "C8-688: DTS 已投放 -> $_c8_dts"

# ---- B. filogic.mk --------------------------------------------------------
_c8_mk="$_c8_dst/image/filogic.mk"
[ -f "$_c8_mk" ] || { _c8_die "上游没有 $_c8_mk"; }
if grep -q 'Device/nradio_c8-688' "$_c8_mk"; then
	echo "C8-688: 设备定义已存在，跳过"
else
	printf '\n# ---- C8-688 (injected by Scripts/C8-688/private.sh) ----\n' >>"$_c8_mk"
	cat "$_c8_src/device-nradio_c8-688.mk" >>"$_c8_mk"
	grep -q 'TARGET_DEVICES += nradio_c8-688' "$_c8_mk" ||
		{ _c8_die "设备定义追加后校验失败"; }
	echo "C8-688: 设备定义已追加 -> $_c8_mk"
fi

# ---- C. 02_network -------------------------------------------------------
_c8_net="$_c8_dst/filogic/base-files/etc/board.d/02_network"
[ -f "$_c8_net" ] || { _c8_die "上游没有 $_c8_net"; }

# C1：LAN/WAN —— 4 个网口全 LAN，WAN 走 5G 模组的 eth1
if grep -q 'nradio,c8-688|' "$_c8_net"; then
	echo "C8-688: LAN/WAN 分支已存在，跳过"
else
	grep -q 'nradio,c8-668gl|' "$_c8_net" ||
		{ _c8_die "02_network 找不到 nradio,c8-668gl 锚点"; }
	sed -i 's/^\(\s*\)nradio,c8-668gl|/\1nradio,c8-688|\\\n\1nradio,c8-668gl|/' "$_c8_net"
		echo "C8-688: 已加入 LAN/WAN 分支"
fi

# C2：MAC —— 与原厂一致：lan_mac 取 bdinfo 的 fac_mac，wan 取 +1（实测 eth1 = fac_mac+1）
if grep -q '^	nradio,c8-688)' "$_c8_net"; then
	echo "C8-688: MAC 分支已存在，跳过"
else
	grep -q '^	nradio,c8-668gl)' "$_c8_net" ||
		{ _c8_die "02_network 找不到 MAC 锚点 nradio,c8-668gl)"; }
	python3 - "$_c8_net" <<'PY'
import re
import sys

path = sys.argv[1]
text = open(path, encoding="utf-8").read()
block = (
	"\tnradio,c8-688)\n"
	# 实机 bdinfo 是 "fac_mac = FC:83:C6:14:9F:A0"；
	# get_mac_ascii 先 tr -d ' \t' 再按 "fac_mac=" 匹配 => key 不能带尾空格。
	# 原厂 eth1 = fac_mac+1（实测 fc:83:c6:14:9f:a1），这里保持一致。
	'\t\tlan_mac=$(mmc_get_mac_ascii bdinfo fac_mac)\n'
	'\t\twan_mac=$(macaddr_add "$lan_mac" 1)\n'
	"\t\tlabel_mac=$lan_mac\n"
	"\t\t;;\n"
)
text = text.replace("\tnradio,c8-668gl)\n", block + "\tnradio,c8-668gl)\n", 1)
open(path, "w", encoding="utf-8").write(text)
PY
	grep -q 'nradio,c8-688)' "$_c8_net" ||
		{ _c8_die "MAC 分支插入失败"; }
	echo "C8-688: 已加入 MAC 分支"
fi

# ---- D. 覆盖层（Files/C8-688 -> package/base-files/files） ----------------
_c8_files="${GITHUB_WORKSPACE:-/github/workspace}/Files/C8-688"
if [ -d "$_c8_files" ]; then
	cp -a "$_c8_files/." ./package/base-files/files/ ||
		{ _c8_die "覆盖层投放失败"; }
	chmod 755 ./package/base-files/files/usr/sbin/c8-fan 2>/dev/null
	chmod 755 ./package/base-files/files/usr/sbin/c8-env-detect 2>/dev/null
	chmod 755 ./package/base-files/files/usr/sbin/c8-boot-official 2>/dev/null
	chmod 755 ./package/base-files/files/etc/init.d/c8-fan 2>/dev/null
	chmod 755 ./package/base-files/files/etc/init.d/c8-wifi-mac 2>/dev/null
	chmod 755 ./package/base-files/files/etc/uci-defaults/99-c8-688 2>/dev/null
	echo "C8-688: 覆盖层已投放（风扇 / WiFi MAC / 首启 / env）"
else
	_c8_die "缺少覆盖层 $_c8_files"
fi

# MT5700M packages are installed once from Profiles/packages.lock.json.
test -f package/luci-app-mt5700m/Makefile
test -f package/mt5700m-transport/Makefile
printf '%s\n' "${WRT_RELEASE_TAG:?missing release tag}" > package/base-files/files/etc/c8-688-build.tag
echo "C8-688: device source and overlay ready"
