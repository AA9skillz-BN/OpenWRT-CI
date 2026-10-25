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
#   - 不用 F50 自带 DNS，改用公共 DNS（见下方注释，这是「没有下载速度」的元凶）
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
	# 关键：不用 F50 自带的 DNS。实测 192.168.0.1 连测 5 次会失败 1 次，
	# 而且对 CNAME 链（如 mirrors.aliyun.com → w.alikunlun.com）经常只回 CNAME
	# 不回 A 记录，客户端拿到「Could not resolve host」，表现为「没有下载速度」。
	# 换成三个公共 DNS 后：解析 10/10 成功，镜像站下载 5.2 MB/s。
	uci -q set network.f50.peerdns='0'
	uci -q delete network.f50.dns
	uci -q add_list network.f50.dns='114.114.114.114'
	uci -q add_list network.f50.dns='223.5.5.5'
	uci -q add_list network.f50.dns='119.29.29.29'
	uci -q commit network
	log "network.f50 -> $ifc (dhcp, metric 10, 公共 DNS)"

	# 并入 wan 防火墙区
	zone=$(uci -q show firewall | sed -n "s/^\(firewall\.[^.]*\)\.name='wan'$/\1/p" | head -n1)
	if [ -n "$zone" ]; then
		sec="${zone#firewall.}"
		if ! uci -q get "firewall.$sec.network" 2>/dev/null | tr ' ' '\n' | grep -qx f50; then
			# 注意：值里不要再套一层引号，否则 UCI 会存成带引号的字面量 'f50'，
			# 既进不了区域匹配、又会随每次 hotplug 累积脏条目。
			uci -q add_list "firewall.$sec.network=f50"
			uci -q commit firewall
			log "f50 已并入 wan 防火墙区"
		fi
		# 兜底开启 masquerade：没有 SNAT 的话，LAN 侧（192.168.6.x）访问
		# F50 内网（192.168.0.1）时源地址还是 192.168.6.x，F50 没有回程
		# 路由，表现就是「后台打不开」。
		if [ "$(uci -q get "firewall.$sec.masq")" != "1" ]; then
			uci -q set "firewall.$sec.masq='1'"
			uci -q commit firewall
			log "已强制开启 wan 区 masquerade"
		fi
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

	/etc/init.d/firewall reload >/dev/null 2>&1
	/etc/init.d/dnsmasq reload >/dev/null 2>&1
	log "已启动 f50 并重载防火墙/DNS"
}

main "$@"
