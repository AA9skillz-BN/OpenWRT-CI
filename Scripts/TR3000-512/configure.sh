#!/usr/bin/env bash
set -euo pipefail
cp "$GITHUB_WORKSPACE/Config/TR3000-512.txt" .config
make defconfig
grep -qx 'CONFIG_TARGET_mediatek_filogic_DEVICE_cudy_tr3000-v1=y' .config
grep -qx 'CONFIG_PACKAGE_luci-app-openclash=y' .config
grep -qx 'CONFIG_PACKAGE_kmod-usb-net-rndis=y' .config
grep -qx 'CONFIG_PACKAGE_kmod-usb-net-cdc-ncm=y' .config
grep -qx 'CONFIG_PACKAGE_kmod-mt7915e=y' .config
grep -qx 'CONFIG_PACKAGE_kmod-nft-offload=y' .config
grep -qx 'CONFIG_PACKAGE_luci-app-turboacc=y' .config
grep -qx 'CONFIG_PACKAGE_luci-theme-aurora=y' .config
grep -qx 'CONFIG_PACKAGE_luci-app-aurora-config=y' .config
grep -qx 'CONFIG_PACKAGE_luci-i18n-base-zh-cn=y' .config
grep -qx 'CONFIG_PACKAGE_luci-i18n-aurora-config-zh-cn=y' .config
cp .config build.config
mkdir -p files/etc/uci-defaults



cp -a "$GITHUB_WORKSPACE/Files/TR3000-512/." files/
chmod +x files/etc/uci-defaults/99-tr3000-*
