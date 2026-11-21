#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
repo="${GITHUB_WORKSPACE:?missing workspace}"
source="$repo/Scripts/TR3000-512/ota"
base=package/base-files/files
mkdir -p "$base/usr/sbin" "$base/usr/share/tr3000-ota" "$base/lib/functions" \
	"$base/etc/config" "$base/etc/uci-defaults" "$base/etc/init.d" "$base/etc/rc.d"
install -m 0755 "$source/tr3000-ota" "$base/usr/sbin/tr3000-ota"
install -m 0644 "$source/tr3000-system.sh" "$base/lib/functions/tr3000-system.sh"
install -m 0644 "$source/runtime-abi" "$base/usr/share/tr3000-ota/runtime-abi"
install -m 0644 "$source/tr3000_ota.config" "$base/etc/config/tr3000_ota"
install -m 0755 "$source/99-tr3000-ota-cron" "$base/etc/uci-defaults/99-tr3000-ota-cron"
install -m 0755 "$source/tr3000-ota-boot" "$base/etc/init.d/tr3000-ota-boot"
ln -sf ../init.d/tr3000-ota-boot "$base/etc/rc.d/S99tr3000-ota-boot"
# Preserve result records for --keep only. Never preserve the old build tag.
touch "$base/etc/sysupgrade.conf"
for name in /etc/tr3000-pending /etc/tr3000-result; do
	grep -Fxq "$name" "$base/etc/sysupgrade.conf" || printf '%s\n' "$name" >> "$base/etc/sysupgrade.conf"
done
rm -rf package/luci-app-tr3000-ota
cp -a "$repo/Scripts/TR3000-512/luci-app-tr3000-ota" package/luci-app-tr3000-ota
