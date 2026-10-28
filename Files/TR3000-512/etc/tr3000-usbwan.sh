#!/bin/sh
# SPDX-License-Identifier: MIT
#
# TR3000：把 USB 上网卡（F50 / 随身 WiFi 的 RNDIS、CDC-NCM、CDC-ECM 网卡）
# 自动接成 WAN。
#
# 旧固件的问题：network.F50 把网卡写死成 eth2，而 eth2 这个编号是上一次
# 枚举顺序的产物。换线、换口、换设备、或者 USB 重新枚举后设备号一变，
# 接口就变成「网络设备不存在」，DHCP 永远起不来，WiFi 客户端自然没网。
# 按 USB 总线实时探测，只更新设备绑定；首次创建时填 DHCP、metric 和 DNS。
# 已有协议、DNS、路由优先级、停用开关及防火墙区域保留。
# 旧 network.F50 改名为 f50，同时迁移区域引用，不丢弃用户设置。
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
	local zone sec candidate initial=0 network_changed=0 firewall_changed=0 masq migrated=0

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

	# Migrate the old name without discarding user protocol/DNS/route settings.
	if ! uci -q get network.f50 >/dev/null 2>&1; then
		if uci -q get network.F50 >/dev/null 2>&1; then
			uci -q rename network.F50=f50 || return 1
			migrated=1
			uci -q delete network.f50.ifname
		else
			initial=1
			uci -q set network.f50=interface
			uci -q set network.f50.proto=dhcp
			uci -q set network.f50.metric=10
			# Default DNS avoids unreliable modem DNS; only set on creation.
			uci -q set network.f50.peerdns=0
			uci -q add_list network.f50.dns=114.114.114.114
			uci -q add_list network.f50.dns=223.5.5.5
			uci -q add_list network.f50.dns=119.29.29.29
		fi
		network_changed=1
	fi
	if [ "$migrated" = 1 ]; then
		for candidate in $(uci -q show firewall | sed -n 's/^\(firewall\.[^.]*\)\.network=.*/\1/p'); do
			if uci -q get "$candidate.network" | tr ' ' '\n' | grep -qx F50; then
				uci -q del_list "$candidate.network=F50"
				uci -q get "$candidate.network" | tr ' ' '\n' | grep -qx f50 || uci -q add_list "$candidate.network=f50"
				firewall_changed=1
			fi
		done
	fi
	if [ "$(uci -q get network.f50.disabled)" = 1 ] ||
	   [ "$(uci -q get network.f50.auto)" = 0 ] ||
	   [ "$(uci -q get network.f50.proto)" = none ]; then
		[ "$network_changed" = 1 ] && uci -q commit network
		[ "$firewall_changed" = 1 ] && uci -q commit firewall
		log 'f50 自动启动已关闭，保留用户配置'
		return 0
	fi
	if [ "$(uci -q get network.f50.device)" != "$ifc" ]; then
		uci -q set network.f50.device="$ifc"
		network_changed=1
	fi
	[ "$network_changed" = 1 ] && uci -q commit network
	log "network.f50 -> $ifc（初始化=$initial，保留已有协议/DNS/metric）"

	# 并入 wan 防火墙区
	# Respect an existing user-selected firewall zone.
	zone=
	for candidate in $(uci -q show firewall | sed -n 's/^\(firewall\.[^.]*\)\.network=.*/\1/p'); do
		if uci -q get "$candidate.network" | tr ' ' '\n' | grep -qx f50; then zone=$candidate; break; fi
	done
	[ -n "$zone" ] || zone=$(uci -q show firewall | sed -n "s/^\(firewall\.[^.]*\)\.name='wan'$/\1/p" | head -n1)
	if [ -n "$zone" ]; then
		sec="${zone#firewall.}"
		if ! uci -q get "firewall.$sec.network" 2>/dev/null | tr ' ' '\n' | grep -qx f50; then
			# 注意：值里不要再套一层引号，否则 UCI 会存成带引号的字面量 'f50'，
			# 既进不了区域匹配、又会随每次 hotplug 累积脏条目。
			uci -q add_list "firewall.$sec.network=f50"
			firewall_changed=1
			log "f50 已并入 wan 防火墙区"
		fi
		# 兜底开启 masquerade：没有 SNAT 的话，LAN 侧（192.168.6.x）访问
		# F50 内网（192.168.0.1）时源地址还是 192.168.6.x，F50 没有回程
		# 路由，表现就是「后台打不开」。
		masq=$(uci -q get "firewall.$sec.masq")
		if [ -z "$masq" ] && [ "$(uci -q get "firewall.$sec.name")" = wan ]; then
			uci -q set "firewall.$sec.masq=1"
			firewall_changed=1
		fi
		case "$masq" in
			"'1'")
				uci -q set "firewall.$sec.masq=1"
				firewall_changed=1
				;;
		esac
		[ "$firewall_changed" = 1 ] && uci -q commit firewall
	else
		log "警告：找不到 wan 防火墙区，NAT 可能不生效"
	fi

	# 等 netifd 处理完设备事件，再拉起接口
	sleep 1
	ifup f50 >/dev/null 2>&1 || /sbin/ifup f50 >/dev/null 2>&1

	# USB 网卡的接收默认全压在 CPU0 上。MT7981 是双核，实测单核只有
	# 10~40 Mbps，而同一张 F50 用 WiFi 能跑 262 Mbps —— 差的就是这一口气。
	# 把 RPS 铺到两核，并放开每队列的流表，让软中断并行处理。
	#
	# 关键：必须在 netifd 把接口完全拉起之后再写。实测 ifup 刚返回就写，
	# 值会随后被 netifd 重置回 0（日志显示设置成功但实际是 0）。
	# 所以先睡 3 秒，再由 /etc/hotplug.d/iface/30-tr3000-rps 在 ifup 事件里兜一次。
	sleep 3
	if [ -d "/sys/class/net/$ifc/queues" ]; then
		local q
		for q in /sys/class/net/"$ifc"/queues/rx-*/rps_cpus; do
			[ -w "$q" ] && echo 3 >"$q" 2>/dev/null
		done
		for q in /sys/class/net/"$ifc"/queues/rx-*/rps_flow_cnt; do
			[ -w "$q" ] && echo 4096 >"$q" 2>/dev/null
		done
		log "已为 $ifc 启用双核 RPS（$(cat /sys/class/net/"$ifc"/queues/rx-0/rps_cpus 2>/dev/null)）"
	fi

	[ "$firewall_changed" = 1 ] && /etc/init.d/firewall reload >/dev/null 2>&1
	log "已启动 f50"
}

main "$@"
