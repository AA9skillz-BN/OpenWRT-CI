# Retire the former TR BBR helper and exact old-package runtime paths.
# Do not call old stop(): it writes firewall/DNS state while stopping.
rm -f /etc/rc.d/S99tr3000-bbr /etc/rc.d/K10tr3000-bbr /etc/init.d/tr3000-bbr
rm -f /etc/uci-defaults/luci-turboacc
rm -f /usr/lib/lua/luci/controller/turboacc.lua
rm -f /usr/lib/lua/luci/model/cbi/turboacc.lua
rm -f /usr/lib/lua/luci/view/turboacc/turboacc_status.htm
