#!/bin/bash
# SPDX-License-Identifier: MIT
# AirPi AP3000M 专属扩展（2026-10-25 从 Scripts/PRIVATE.sh 拆出）
#
# 由公共核心通过独立 bash 进程执行，必需文件投放失败即终止。
#
set -euo pipefail

# 仅 AP3000M 配置生效（WRT_CONFIG 为空时也跳过）
if [[ "${WRT_CONFIG:-}" != "AP3000M" ]]; then
	return 0 2>/dev/null || exit 0
fi

_ap3000m_log() { echo "==> [AP3000M] $*"; }

# Rust and locked plugin installation are handled by the common build stages.

# Adapt the reviewed, locked fan plugin without changing its source lock.
python3 "$GITHUB_WORKSPACE/Scripts/AP3000M/fancontrol/fix-confirm.py" \
	package/luci-app-airpi3000m-fancontrol

# Keep IPv6 DNS out of the PDP activation/address fallback in the locked plugin.
python3 "$GITHUB_WORKSPACE/Scripts/AP3000M/fm350/fix-pdp-dns.py" \
	package/luci-app-fm350

# 6) 投放 AP3000M 专属的首次开机配置（FM350 的 AT 口 / APN / CID 自动纠正）
#    真机实测：插件默认 at_port=/dev/ttyUSB1 在本机是哑口（真 AT 口是 USB 接口 :1.6
#    = /dev/ttyUSB3），且默认 cid=1 会拨到运营商的 ims 承载，表现为「能读到模组却上不了网」。
_ap3000m_uci="$GITHUB_WORKSPACE/Scripts/AP3000M/fm350-ap3000m"
if [ -f "$_ap3000m_uci" ]; then
	_ap3000m_dst="package/base-files/files/etc/uci-defaults/99-ap3000m-fm350"
	mkdir -p "$(dirname "$_ap3000m_dst")"
	cp -f "$_ap3000m_uci" "$_ap3000m_dst"
	chmod 0755 "$_ap3000m_dst"
	_ap3000m_log "已投放开机自动配置: $_ap3000m_dst"
else
	echo "Missing required FM350 first-boot defaults" >&2
	exit 1
fi

# 7) 一键 OTA：CLI 脚本 + 构建标签 + 升级结果回写
#    DEVICE-CORE 在「Initialization Values」中写入 WRT_RELEASE_TAG，
#    固件与发布 job 使用同一个标签。
_ap3000m_ota_dir="$GITHUB_WORKSPACE/Scripts/AP3000M/ota"
_ap3000m_bf="package/base-files/files"
if [ -d "$_ap3000m_ota_dir" ]; then
	mkdir -p "$_ap3000m_bf/usr/sbin" "$_ap3000m_bf/etc/config" \
		"$_ap3000m_bf/etc/init.d" "$_ap3000m_bf/etc/uci-defaults"

	cp -f "$_ap3000m_ota_dir/ap3000m-ota" "$_ap3000m_bf/usr/sbin/ap3000m-ota"
	chmod 0755 "$_ap3000m_bf/usr/sbin/ap3000m-ota"
	mkdir -p "$_ap3000m_bf/usr/share/ap3000m-ota"
	cp -f "$_ap3000m_ota_dir/runtime-abi" "$_ap3000m_bf/usr/share/ap3000m-ota/runtime-abi"
	chmod 0644 "$_ap3000m_bf/usr/share/ap3000m-ota/runtime-abi"

	cp -f "$_ap3000m_ota_dir/ap3000m_ota.config" "$_ap3000m_bf/etc/config/ap3000m_ota"
	chmod 0644 "$_ap3000m_bf/etc/config/ap3000m_ota"

	# 每日自动检查（写 /tmp 缓存给 LuCI 页面秒开），auto_check=0 时脚本自己跳过
	cp -f "$_ap3000m_ota_dir/99-ap3000m-ota-cron" "$_ap3000m_bf/etc/uci-defaults/99-ap3000m-ota-cron"
	chmod 0755 "$_ap3000m_bf/etc/uci-defaults/99-ap3000m-ota-cron"

	_ap3000m_tag="${WRT_RELEASE_TAG:?missing release tag}"
	printf '%s\n' "$_ap3000m_tag" >"$_ap3000m_bf/etc/ap3000m-build.tag"
	chmod 0644 "$_ap3000m_bf/etc/ap3000m-build.tag"

	# 升级状态要跨越 sysupgrade 保留（只保留这两个文件，build.tag 必须用新镜像里的）
	if ! grep -q '^/etc/ap3000m-pending$' "$_ap3000m_bf/etc/sysupgrade.conf" 2>/dev/null; then
		printf '/etc/ap3000m-pending\n/etc/ap3000m-result\n' >>"$_ap3000m_bf/etc/sysupgrade.conf"
	fi

	# 开机回写升级结果：自带 init.d 脚本 + 构建期建 rc.d 软链（不碰 rc.local）
	cp -f "$_ap3000m_ota_dir/ap3000m-ota-boot" "$_ap3000m_bf/etc/init.d/ap3000m-ota-boot"
	chmod 0755 "$_ap3000m_bf/etc/init.d/ap3000m-ota-boot"
	mkdir -p "$_ap3000m_bf/etc/rc.d"
	ln -sf ../init.d/ap3000m-ota-boot "$_ap3000m_bf/etc/rc.d/S99ap3000m-ota-boot"

	_ap3000m_log "已投放 OTA：ap3000m-ota（构建标签 $_ap3000m_tag）"
else
	echo "Missing required OTA files" >&2
	exit 1
fi

# Optional, user-enabled USB 2.0 compatibility. Run before fm350d (S95).
# Preserve the user's UCI choice; fresh firmware keeps USB 3.0 available.
_ap3000m_usb_compat="$GITHUB_WORKSPACE/Scripts/AP3000M/ap3000m-fm350-usb-compat"
cp -f "$_ap3000m_usb_compat" "$_ap3000m_bf/etc/init.d/ap3000m-fm350-usb-compat"
chmod 0755 "$_ap3000m_bf/etc/init.d/ap3000m-fm350-usb-compat"
ln -sf ../init.d/ap3000m-fm350-usb-compat "$_ap3000m_bf/etc/rc.d/S90ap3000m-fm350-usb-compat"

# 8) 开机 RNDIS 卡死自愈
#    历史真机复现：eth2 有地址但 rx_packets=0、tx_errors 增长。
#    模组重启并非每次有效；恢复结果仍须实际探测，失败不能当作联网成功。
_ap3000m_guard_dir="$GITHUB_WORKSPACE/Scripts/AP3000M/rdnis-guard"
if [ -d "$_ap3000m_guard_dir" ]; then
	cp -f "$_ap3000m_guard_dir/ap3000m-rdnis-guard" "$_ap3000m_bf/usr/sbin/ap3000m-rdnis-guard"
	chmod 0755 "$_ap3000m_bf/usr/sbin/ap3000m-rdnis-guard"

	cp -f "$_ap3000m_guard_dir/ap3000m-rdnis-guard-boot" "$_ap3000m_bf/etc/init.d/ap3000m-rdnis-guard"
	chmod 0755 "$_ap3000m_bf/etc/init.d/ap3000m-rdnis-guard"

	mkdir -p "$_ap3000m_bf/etc/rc.d"
	ln -sf ../init.d/ap3000m-rdnis-guard "$_ap3000m_bf/etc/rc.d/S98ap3000m-rdnis-guard"

	# 运行期巡检：RNDIS 卡死也会发生在运行中（不只开机），每 5 分钟检查一次
	cp -f "$_ap3000m_guard_dir/99-ap3000m-rdnis-cron" "$_ap3000m_bf/etc/uci-defaults/99-ap3000m-rdnis-cron"
	chmod 0755 "$_ap3000m_bf/etc/uci-defaults/99-ap3000m-rdnis-cron"

	_ap3000m_log "已投放 RNDIS 自愈（开机自检 + 每 5 分钟运行期巡检，单级恢复 + 失败提示）"
else
	echo "Missing required RNDIS guard" >&2
	exit 1
fi

# 9) LuCI OTA 页面（luci-app-ap3000m-ota：JS 视图 + rpcd ucode 后端）
_ap3000m_luci="$GITHUB_WORKSPACE/Scripts/AP3000M/luci-app-ap3000m-ota"
if [ -d "$_ap3000m_luci" ]; then
	rm -rf package/luci-app-ap3000m-ota
	cp -rf "$_ap3000m_luci" package/luci-app-ap3000m-ota
	_ap3000m_log "已投放 LuCI OTA 页面（package/luci-app-ap3000m-ota）"
else
	echo "Missing required LuCI OTA package" >&2
	exit 1
fi

# 10) RNDIS 数据面调优（RPS/XPS + txqueuelen + MTU 实测 + BBR sysctl + flow offload）
_ap3000m_tune="$GITHUB_WORKSPACE/Scripts/AP3000M/net-tune"
if [ -d "$_ap3000m_tune" ]; then
	mkdir -p "$_ap3000m_bf/usr/sbin" "$_ap3000m_bf/etc/hotplug.d/net" \
		"$_ap3000m_bf/etc/sysctl.d" "$_ap3000m_bf/etc/uci-defaults"

	cp -f "$_ap3000m_tune/ap3000m-net-tune" "$_ap3000m_bf/usr/sbin/ap3000m-net-tune"
	chmod 0755 "$_ap3000m_bf/usr/sbin/ap3000m-net-tune"

	cp -f "$_ap3000m_tune/20-ap3000m-net-tune" "$_ap3000m_bf/etc/hotplug.d/net/20-ap3000m-net-tune"
	chmod 0755 "$_ap3000m_bf/etc/hotplug.d/net/20-ap3000m-net-tune"

	cp -f "$_ap3000m_tune/99-ap3000m-tune.conf" "$_ap3000m_bf/etc/sysctl.d/99-ap3000m-tune.conf"
	cp -f "$_ap3000m_tune/99-ap3000m-flowoffload" "$_ap3000m_bf/etc/uci-defaults/99-ap3000m-flowoffload"
	chmod 0755 "$_ap3000m_bf/etc/uci-defaults/99-ap3000m-flowoffload"

	_ap3000m_log "已投放 RNDIS 调优（hotplug + sysctl + flow offload）"
else
	echo "Missing required network tuning" >&2
	exit 1
fi

# Upstream refreshed fullcone in 21ae803c. Preserve its patches.
# Future patch incompatibilities must fail the build instead of losing features.
_ap3000m_log "扩展准备完成"
