# Retire exact old TurboACC paths and the AP-only competing defaults.
# Do not call the former stop(): it rewrites firewall/DNS state.
rm -f /etc/uci-defaults/luci-turboacc /etc/uci-defaults/99-ap3000m-flowoffload
rm -f /usr/lib/lua/luci/controller/turboacc.lua
rm -f /usr/lib/lua/luci/model/cbi/turboacc.lua
rm -f /usr/lib/lua/luci/view/turboacc/turboacc_status.htm
# Preserve the rest of the AP tuning file and any other user sysctls.
if [ -f /etc/sysctl.d/99-ap3000m-tune.conf ]; then
	sed -i '/^net\.ipv4\.tcp_congestion_control=bbr$/d' /etc/sysctl.d/99-ap3000m-tune.conf
fi
rm -f /tmp/luci-indexcache /tmp/luci-indexcache.*
rm -rf /tmp/luci-modulecache
