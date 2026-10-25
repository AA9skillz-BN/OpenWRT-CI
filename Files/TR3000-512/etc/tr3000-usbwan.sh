#!/bin/sh
# SPDX-License-Identifier: MIT
#
# TR3000：把 USB 上网卡（F50 / 随身 WiFi 的 RNDIS、CDC-NCM、CDC-ECM 网卡）
# 自动接成 WAN。
#
# 旧固件的问题：network.F50 把网卡写死成 eth2，而 eth2 这个编号是上一次
# 枚举顺序的产物。换线、换口、换设备、或者 USB 重新枚举后设备号一变，
# 接口就变成「网络设备不存在」，DHCP 永远起不来，WiFi 客户端自然没网。
# 本脚本改成按 USB 总线实时探测，探测到谁就用谁，并且：
#   - 写 network.f50（DHCP 客户端，metric 10，优先于有线 wan）
#   - 并入 wan 防火墙区（wan 区自带 masquerade / mtu_fix；LAN→WAN 转发已存在）
#   - 清理旧固件遗留的 network.F50
#
# 触发点：
#   /etc/hotplug.d/net/20-tr3000-usbwan    插拔时
#   /etc/init.d/tr3000-usbwan              开机时（已经插好也能生效）
#   /etc/uci-defaults/99-tr3000-usbwan     首次开机补一次
#
# 用法：/etc/tr3000-usbwan.sh [网卡名]；不带参数则自行扫描。

log() { logger -t tr3000-usbwan "$*" 2>/dev/null; echo "$*"; }

# USB 总线上的以太网设备：/sys/class/net/<if>/device 解析后落在 /usbN/ 下
is_usb_netdev() {
	local ifc="$1"
	local real
	[ -n "$ifc" ] || return 1
	[ "$ifc" = "lo" ] && return 1
	[ -e "/sys/class/net/$ifc/device" ] || return 1
	case "$ifc" in
		wlan*|phy*|br-*|bat*|docker*|veth*|tun*|tap*|wg*) return 1 ;;
	esac
	real=$(readlink -f "/sys/class/net/$ifc/device")
	case "$real" in
		*/usb[0-9]*/*) return 0 ;;
	esac
	# 少数 USB 网卡挂在 platform 下，再按驱动名兜一层
	case "$(basename "$(readlink -f "/sys/class/net/$ifc/device/driver" 2>/dev/null)")" in
		rndis_host|cdc_ether|cdc_ncm|cdc_eem|cdc_subset|usbnet|huawei_cdc_ncm|r8152|ax88179_178a)
			return 0 ;;
	esac
	return 1
}

find_usb_netdev() {
	local ifc
	for ifc in /sys/class/net/*; do
		ifc="${ifc##*/}"
		is_usb_netdev "$ifc" && {
			echo "$ifc"
			return 0
		}
	done
	return 1
}

main() {
	local ifc="$1"
	local zone sec

	# 旧固件把 F50 写死成 eth2，先清掉，避免同名意图的两个接口并存
	if uci -q get network.F50 >/dev/null 2>&1; then
		uci -q delete network.F50
		uci -q commit network
		log "已清理遗留 network.F50"
	fi

	if [ -z "$ifc" ]; then
		ifc=$(find_usb_netdev) || {
			log "未发现 USB 网卡"
			return 0
		}
	fi

	is_usb_netdev "$ifc" || {
		log "$ifc 不是 USB 网卡，忽略"
		return 0
	}

	# 用户已经把它桥进 LAN 的，不抢
	if [ -d "/sys/class/net/br-lan/brif/$ifc" ]; then
		log "$ifc 已是 LAN 桥成员，跳过"
		return 0
	fi

	uci -q set network.f50=interface
	uci -q set network.f50.proto='dhcp'
	uci -q set network.f50.device="$ifc"
	uci -q set network.f50.metric='10'
	uci -q commit network
	log "network.f50 -> $ifc (dhcp, metric 10)"

	# 并入 wan 防火墙区
	zone=$(uci -q show firewall | sed -n "s/^\(firewall\.[^.]*\)\.name='wan'$/\1/p" | head -n1)
	if [ -n "$zone" ]; then
		sec="${zone#firewall.}"
		if ! uci -q get "firewall.$sec.network" 2>/dev/null | tr ' ' '\n' | grep -qx f50; then
			uci -q add_list "firewall.$sec.network='f50'"
			uci -q commit firewall
			log "f50 已并入 wan 防火墙区"
		fi
	else
		log "警告：找不到 wan 防火墙区，NAT 可能不生效"
	fi

	# 等 netifd 处理完设备事件，再拉起接口
	sleep 1
	ifup f50 >/dev/null 2>&1 || /sbin/ifup f50 >/dev/null 2>&1
	/etc/init.d/firewall reload >/dev/null 2>&1
	log "已启动 f50 并重载防火墙"
}

main "$@"
