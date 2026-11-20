#!/bin/sh
# SPDX-License-Identifier: MIT
# Sourced by the pinned manager after its original functions. Existing USB
# users keep their manager. Ethernet users keep their own interface and policy.
. /usr/share/c8-cellular/slots.sh

# Both Ethernet and USB users retain the dial mode selected in the WEBUI.
apply_profile() {
    local pdp protocol apn username password auth auth_code dial_mode
    pdp="$(config_value pdp_type ipv4v6)"
    case "$pdp" in ip) protocol=IP;; ipv6) protocol=IPV6;; *) protocol=IPV4V6;; esac
    dial_mode="$(config_value dial_mode 1)"
    case "$dial_mode" in 1|2) ;; *) return 1;; esac
    apn="$(config_value apn '')"; username="$(config_value username '')"; password="$(config_value password '')"
    auth="$(config_value auth none)"
    case "$auth" in pap) auth_code=1;; chap) auth_code=2;; auto) auth_code=3;; *) auth_code=0;; esac
    apply_profile_transaction "1,$dial_mode,\"$protocol\",\"$apn\",\"$username\",\"$password\",$auth_code"
}
c8_ethernet_interface() {
    local section device
    for section in $(uci -q show network | sed -n "s/^network\.\([A-Za-z0-9_]*\)=interface$/\1/p"); do
        device="$(uci -q get "network.$section.device" || true)"
        [ "$device" = eth1 ] || continue
        printf '%s\n' "$section"; return 0
    done
    return 1
}
C8_ETH_INTERFACE="$(c8_ethernet_interface || true)"
# Existing installations can have both the default eth1 WAN and a deliberately
# configured USB uplink. Preserve that explicit MT5700M selection, even disabled.
case "$(uci -q get network.MT5700M.device || true)" in
    ''|eth1) ;;
    *) C8_ETH_INTERFACE='';;
esac
if [ -n "$C8_ETH_INTERFACE" ]; then
    INTERFACE="$C8_ETH_INTERFACE"
    INTERFACE6=''
    for c8_section in $(uci -q show network | sed -n "s/^network\.\([A-Za-z0-9_]*\)=interface$/\1/p"); do
        [ "$(uci -q get "network.$c8_section.proto" || true)" = dhcpv6 ] || continue
        case "$(uci -q get "network.$c8_section.device" || true)" in
            "@$INTERFACE"|eth1) INTERFACE6="$c8_section"; break;;
        esac
    done
    mt5700m_netdev() { [ -d /sys/class/net/eth1 ] && printf 'eth1\n'; }
    sync_manager() { :; } # Never overwrite WAN, IPv6, DNS, metric or firewall.
    monitor_sync_if_needed() { :; }
    bring_up() {
        [ "$(config_value enabled 1)" = 1 ] || return 0
        [ "$(uci -q get "network.$INTERFACE.auto" || echo 1)" != 0 ] || return 0
        ifup "$INTERFACE" >/dev/null 2>&1 || return 1
        if [ -n "$INTERFACE6" ] && [ "$(uci -q get "network.$INTERFACE6.auto" || echo 1)" != 0 ]; then
            ifup "$INTERFACE6" >/dev/null 2>&1
        fi
    }
    bring_down() { :; } # Stopping the monitor is not a request to stop WAN.
    recover_ndis_link() { :; } # Ethernet carrier is not USB NDIS carrier.
    connect_manager() {
        apply_profile || return 1
        bring_up
    }
    disconnect_manager() {
        apply_profile_transaction 0 || return 1
        # Explicit disconnect stops router traffic. Service stop remains a no-op.
        ifdown "$INTERFACE" >/dev/null 2>&1 || return 1
        [ -z "$INTERFACE6" ] || ifdown "$INTERFACE6" >/dev/null 2>&1
    }
    redial_manager() {
        # Profile transaction cancels/reapplies auto-dial with verified rollback;
        # do not toggle the USB NDIS session on an Ethernet data path.
        apply_profile || return 1
        ifdown "$INTERFACE" >/dev/null 2>&1 || return 1
        bring_up
    }
    config_value() {
        if [ "$1" = enabled ] && [ "$(uci -q get "network.$INTERFACE.auto" || echo 1)" = 0 ]; then
            printf '0\n'
        else
            uci -q get "mt5700m.connection.$1" 2>/dev/null || printf '%s\n' "$2"
        fi
    }
    # The original health checker retains its bound HTTPS probes, three-failure
    # threshold, 180-second cooldown and maximum three repairs per hour.
    # Honor both operator switches before allowing it to request a repair.
    c8_health_enabled() {
        [ "$(config_value enabled 1)" = 1 ] &&
        [ "$(uci -q get "network.$INTERFACE.auto" || echo 1)" != 0 ]
    }
    # Automatic Ethernet recovery refreshes DHCP only. A generic USB NDIS
    # toggle must not alter an independently configured Ethernet data session.
    health_repair() {
        c8_health_enabled || { health_state disabled; return 0; }
        health_claim_repair "$1" || return 0
        health_put failures 0
        health_state renewing-lease
        ifup "$INTERFACE" >/dev/null 2>&1 || health_state needs-attention
    }
fi
