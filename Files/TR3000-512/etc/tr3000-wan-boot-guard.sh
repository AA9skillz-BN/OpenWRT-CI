#!/bin/sh
# SPDX-License-Identifier: MIT
# One bounded recovery for the observed eth0 carrier-up / zero-RX boot stall.
# Never rewrite network configuration or periodically reset a healthy uplink.
NET=/sys/class/net/eth0
BOOT_UPTIME=/proc/uptime
BOARD=/tmp/sysinfo/board_name
STATE=/tmp/tr3000-wan-boot-guard

log() { logger -t tr3000-wan-boot-guard "$*"; }
uptime_seconds() { cut -d. -f1 "$BOOT_UPTIME"; }
number() { case "$1" in ''|*[!0-9]*) return 1;; esac; }

eligible() {
	[ "$(cat "$BOARD" 2>/dev/null)" = cudy,tr3000-v1 ] &&
	[ "$(uci -q get network.wan.proto)" = dhcp ] &&
	[ "$(uci -q get network.wan.device)" = eth0 ] &&
	[ "$(uci -q get network.wan.disabled)" != 1 ] &&
	[ "$(uci -q get network.wan.auto)" != 0 ] &&
	[ "$(uci -q get network.wan.boot_rx_recovery)" != 0 ] &&
	[ ! -e "$NET/master" ] || return 1
}

stalled() {
	eligible || return 1
	[ "$(cat "$NET/carrier" 2>/dev/null)" = 1 ] || return 1
	[ "$(cat "$NET/statistics/rx_packets" 2>/dev/null)" = 0 ] || return 1
	tx=$(cat "$NET/statistics/tx_packets" 2>/dev/null)
	number "$tx" && [ "$tx" -gt 0 ] || return 1
	# Require netifd's exact device and DHCP-pending state, not a failed ping.
	status=$(ubus call network.interface.wan status 2>/dev/null) || return 1
	[ "$(printf '%s' "$status" | jsonfilter -e '@.device')" = eth0 ] &&
	[ "$(printf '%s' "$status" | jsonfilter -e '@.proto')" = dhcp ] &&
	[ "$(printf '%s' "$status" | jsonfilter -e '@.pending')" = true ] &&
	[ "$(printf '%s' "$status" | jsonfilter -e '@.up')" = false ] || return 1
	addresses=$(ip -4 -o address show dev eth0) || return 1
	[ -z "$addresses" ]
}

recover() (
	# Claimed before link-down and retained until reboot, including failed attempts.
	mkdir "$STATE/attempted" 2>/dev/null || exit 0
	log 'boot DHCP pending, carrier up and RX=0; resetting eth0 once'
	trap 'ip link set dev eth0 up' EXIT
	trap 'exit 1' HUP INT TERM
	ip link set dev eth0 down || exit 1
	sleep 2
	ip link set dev eth0 up || exit 1
	trap - EXIT HUP INT TERM
)

main() {
	eligible || return 0
	now=$(uptime_seconds); number "$now" || return 1
	[ "$now" -le 180 ] || return 0
	# One worker per boot, also when manually started twice. No respawn.
	mkdir "$STATE" 2>/dev/null || return 0
	while [ "$now" -le 180 ]; do
		eligible || return 0
		rx=$(cat "$NET/statistics/rx_packets" 2>/dev/null)
		number "$rx" || return 1
		[ "$rx" = 0 ] || { log 'RX active; no boot recovery needed'; return 0; }
		if [ "$now" -ge 60 ] && stalled; then
			before=$tx
			sleep 10
			# Confirm continued DHCP transmission with no received frames.
			now=$(uptime_seconds); number "$now" || return 1
			if [ "$now" -le 180 ] && stalled && [ "$tx" -gt "$before" ]; then
				recover || return 1
				sleep 10
				log "recovery finished: RX=$(cat "$NET/statistics/rx_packets"), WAN up=$(ubus call network.interface.wan status | jsonfilter -e '@.up')"
				return 0
			fi
		else
			sleep 5
		fi
		now=$(uptime_seconds); number "$now" || return 1
	done
	log 'boot observation ended; no matching zero-RX stall'
}

main "$@"
