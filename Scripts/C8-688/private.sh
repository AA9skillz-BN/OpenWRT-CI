#!/bin/bash
# SPDX-License-Identifier: MIT
# C8-688 机型钩子：把 mainline immortalwrt 里缺失的 C8-688 设备支持注入进去。
#
# 约定（Scripts/README.md）：
#   1) 守卫在 set -e 之前；不属于自己就 return
#   2) 变量统一 _c8_ 前缀
#   3) 收尾复位 shell 选项
#
# 注入点（全部带幂等判断，锚点缺失就 fail fast，避免"编出来是个假固件"）：
#   A. target/linux/mediatek/dts/mt7981b-nradio-c8-688.dts        ← 整文件投放
#   B. target/linux/mediatek/image/filogic.mk                     ← 追加设备定义
#   C. target/linux/mediatek/filogic/base-files/etc/board.d/02_network
#        C1. LAN/WAN 分支加 nradio,c8-688
#        C2. MAC 分支加 nradio,c8-688（fac_mac 从 eMMC 的 bdinfo 分区读）
#   D. Files/C8-688 覆盖层（风扇曲线脚本 + 首启）→ package/base-files/files/

if [[ "${WRT_CONFIG,,}" != *"c8-688"* ]]; then
	return 0 2>/dev/null || exit 0
fi

_c8_src="${GITHUB_WORKSPACE:-/github/workspace}/Scripts/C8-688"
_c8_dst="./target/linux/mediatek"

_c8_die() { echo "::error::C8-688 注入失败：$*" >&2; return 1; }

# ---- 0. 源码 pin -----------------------------------------------------------
# 2026-10-27：上游 master 把 firewall4 升到 2026.10.26~bac1d656，而它自带的
# 001-firewall4-add-support-for-fullcone-nat.patch 对不上新源码（8 个 hunk 全失败），
# package/network/config/firewall4 因此编译失败。这里钉到上次验证通过的 commit
# b16f9456（run 40322929807 就是用它编出可用镜像的）；钉不上就退回 HEAD 并摘掉补丁。
_c8_pin="b16f9456d846adc171ab5eafa3c9600907adeff8"
_c8_head="$(git rev-parse HEAD 2>/dev/null || true)"
if [ -n "$_c8_pin" ] && [ "$_c8_head" != "$_c8_pin" ]; then
	echo "C8-688: 源码 pin -> ${_c8_pin:0:8}（当前 ${_c8_head:0:8}）"
	if git fetch --depth 1 -q origin "$_c8_pin" >/dev/null 2>&1 &&
		git checkout -q -f FETCH_HEAD; then
		echo "C8-688: 已切到 $(git rev-parse --short HEAD)"
	else
		echo "::warning::C8-688: pin 失败，留在 ${_c8_head:0:8}，改为摘掉失效补丁"
		_c8_fw4_patch="package/network/config/firewall4/patches/001-firewall4-add-support-for-fullcone-nat.patch"
		if [ -f "$_c8_fw4_patch" ]; then
			rm -f "$_c8_fw4_patch" &&
				echo "C8-688: 已移除 $_c8_fw4_patch"
		fi
	fi
fi

# ---- A. DTS ---------------------------------------------------------------
_c8_dts="$_c8_dst/dts/mt7981b-nradio-c8-688.dts"
[ -f "$_c8_src/dts/mt7981b-nradio-c8-688.dts" ] ||
	{ _c8_die "缺少 $_c8_src/dts/mt7981b-nradio-c8-688.dts"; return 1; }
[ -d "$_c8_dst/dts" ] || { _c8_die "上游没有 $_c8_dst/dts（源码结构变了？）"; return 1; }
cp -f "$_c8_src/dts/mt7981b-nradio-c8-688.dts" "$_c8_dts" &&
	echo "C8-688: DTS 已投放 -> $_c8_dts"

# ---- B. filogic.mk --------------------------------------------------------
_c8_mk="$_c8_dst/image/filogic.mk"
[ -f "$_c8_mk" ] || { _c8_die "上游没有 $_c8_mk"; return 1; }
if grep -q 'Device/nradio_c8-688' "$_c8_mk"; then
	echo "C8-688: 设备定义已存在，跳过"
else
	printf '\n# ---- C8-688 (injected by Scripts/C8-688/private.sh) ----\n' >>"$_c8_mk"
	cat "$_c8_src/device-nradio_c8-688.mk" >>"$_c8_mk"
	grep -q 'TARGET_DEVICES += nradio_c8-688' "$_c8_mk" ||
		{ _c8_die "设备定义追加后校验失败"; return 1; }
	echo "C8-688: 设备定义已追加 -> $_c8_mk"
fi

# ---- C. 02_network -------------------------------------------------------
_c8_net="$_c8_dst/filogic/base-files/etc/board.d/02_network"
[ -f "$_c8_net" ] || { _c8_die "上游没有 $_c8_net"; return 1; }

# C1：LAN/WAN —— 4 个网口全 LAN，WAN 走 5G 模组的 eth1
if grep -q 'nradio,c8-688|' "$_c8_net"; then
	echo "C8-688: LAN/WAN 分支已存在，跳过"
else
	grep -q 'nradio,c8-668gl|' "$_c8_net" ||
		{ _c8_die "02_network 找不到 nradio,c8-668gl 锚点"; return 1; }
	sed -i 's/^\(\s*\)nradio,c8-668gl|/\1nradio,c8-688|\\\n\1nradio,c8-668gl|/' "$_c8_net" &&
		echo "C8-688: 已加入 LAN/WAN 分支"
fi

# C2：MAC —— 与原厂一致：lan_mac 取 bdinfo 的 fac_mac，wan 取 +1（实测 eth1 = fac_mac+1）
if grep -q '^	nradio,c8-688)' "$_c8_net"; then
	echo "C8-688: MAC 分支已存在，跳过"
else
	grep -q '^	nradio,c8-668gl)' "$_c8_net" ||
		{ _c8_die "02_network 找不到 MAC 锚点 nradio,c8-668gl)"; return 1; }
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
		{ _c8_die "MAC 分支插入失败"; return 1; }
	echo "C8-688: 已加入 MAC 分支"
fi

# ---- D. 覆盖层（Files/C8-688 -> package/base-files/files） ----------------
_c8_files="${GITHUB_WORKSPACE:-/github/workspace}/Files/C8-688"
if [ -d "$_c8_files" ]; then
	cp -a "$_c8_files/." ./package/base-files/files/ ||
		{ _c8_die "覆盖层投放失败"; return 1; }
	chmod 755 ./package/base-files/files/usr/sbin/c8-fan 2>/dev/null
	chmod 755 ./package/base-files/files/usr/sbin/c8-env-detect 2>/dev/null
	chmod 755 ./package/base-files/files/usr/sbin/c8-boot-official 2>/dev/null
	chmod 755 ./package/base-files/files/etc/init.d/c8-fan 2>/dev/null
	chmod 755 ./package/base-files/files/etc/init.d/c8-wifi-mac 2>/dev/null
	chmod 755 ./package/base-files/files/etc/uci-defaults/99-c8-688 2>/dev/null
	echo "C8-688: 覆盖层已投放（风扇 / WiFi MAC / 首启 / env）"
fi

# ---- E. MT5700M 管理插件（FAN789/luci-app-mt5700m，钉 v3.0.7） -------------
# Packages.sh 会先拉 LianXia233 的同名包 + QModem：包名重复会直接编不过，
# 而且它们会抢同一个 AT 口，所以这里先清掉再放我们的版本。
for _c8_drop in luci-app-mt5700m luci-app-mt5700 QModem luci-app-qmodem; do
	if [ -e "./package/$_c8_drop" ]; then
		rm -rf "./package/$_c8_drop" &&
			echo "C8-688: 移除冲突包 $_c8_drop"
	fi
done

_c8_mt_pkg="./package/luci-app-mt5700m"
_c8_mt_tr="./package/mt5700m-transport"
if [ -f "$_c8_mt_pkg/Makefile" ] && [ -f "$_c8_mt_tr/Makefile" ]; then
	echo "C8-688: MT5700M 插件已就位，跳过克隆"
else
	_c8_mt_repo="https://github.com/FAN789/luci-app-mt5700m.git"
	_c8_mt_tag="v3.0.7"
	rm -rf /tmp/c8-mt5700m
	git clone --depth 1 --single-branch --branch "$_c8_mt_tag" \
		"$_c8_mt_repo" /tmp/c8-mt5700m ||
		{ _c8_die "克隆 luci-app-mt5700m($_c8_mt_tag) 失败"; return 1; }
	cp -a /tmp/c8-mt5700m/luci-app-mt5700m ./package/ ||
		{ _c8_die "复制 luci-app-mt5700m 失败"; return 1; }
	cp -a /tmp/c8-mt5700m/mt5700m-transport ./package/ ||
		{ _c8_die "复制 mt5700m-transport 失败"; return 1; }
	rm -rf /tmp/c8-mt5700m
	[ -f "$_c8_mt_pkg/Makefile" ] && [ -f "$_c8_mt_tr/Makefile" ] ||
		{ _c8_die "MT5700M 包结构不符合预期（仓库改版？）"; return 1; }
	echo "C8-688: 已放入 luci-app-mt5700m + mt5700m-transport ($_c8_mt_tag)"
fi

# 收尾：复位 shell 选项（分发器也会复位，这里不依赖它）
set +eu
set +o pipefail
return 0
