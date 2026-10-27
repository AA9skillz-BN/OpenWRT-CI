#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Cudy TR3000 512MB 构建钩子（与 AP3000M 同构，各自独立文件）
#
# 调用链：
#   DEVICE-CORE.yml → bash 本脚本（cwd = ./wrt 源码根；独立进程）
#
# 本脚本负责四件事，全部失败即中止构建（分区/镜像不对的固件宁可不发）：
#   1. 512MB 硬改机的 UBI 分区补丁（不改则 sysupgrade 平台检查拒绝刷入）
#   2. 插件由 Profiles/packages.lock.json 统一管理
#   3. 投放首启配置与 F50 即插即用脚本到源码 files/
#   4. 写入构建标签（即 Release tag），便于设备端识别当前版本

# 仅 TR3000 配置生效；return 放在 set -e 之前，避免误伤其它项目的 source
if [[ "${WRT_CONFIG,,}" != *"tr3000"* ]]; then
	return 0 2>/dev/null || exit 0
fi

set -euo pipefail

WRT_REPO_ROOT="${GITHUB_WORKSPACE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
TR_DIR="$WRT_REPO_ROOT/Scripts/TR3000-512"
log() { echo "==> [TR3000] $*"; }

# ------------------------------------------------------------ 0. 清理坏 feed
# feeds/video 的 qt5base/Config.in 存在 Kconfig 递归依赖错误：
#   feeds/video/frameworks/qt5/qt5base/Config.in:18: error: recursive dependency detected!
# 这会让 make defconfig 直接失败，.config 里丢掉 DEVICE_cudy_tr3000-v1，
# 于是 make world 空跑两小时一个镜像都不出，最后卡在 profiles.json 生成。
# 本项目不需要 video（GENERAL.txt 已 CONFIG_FEED_video=n），整包移除。
if [ -d feeds/video ]; then
	rm -rf feeds/video
	log "已移除 feeds/video（qt5 递归依赖会破坏 defconfig）"
fi
rm -rf package/feeds/video

# ---------------------------------------------------------------- 1. 分区布局
log "打 512MB 分区补丁（UBI 0x5c0000 + 0x1da40000，IMAGE_SIZE 485632k）"
bash "$TR_DIR/patch-layout.sh"
if grep -q '0x1da40000' target/linux/mediatek/dts/mt7981b-cudy-tr3000-v1.dts; then
	log "分区 patch 校验通过"
else
	log "分区 patch 校验失败：DTS 里没有 0x1da40000"
	exit 1
fi

# BBR is selected as kmod-tcp-bbr in the device config.
# Locked plugins are installed once by device-build.py before this hook.
python3 "$WRT_REPO_ROOT/.github/scripts/tr3000-defaults.py"

# ------------------------------------------------- 3. 首启配置 + F50 即插即用
log "投放 Files/TR3000-512 到源码 files/"
mkdir -p files
cp -a "$WRT_REPO_ROOT/Files/TR3000-512/." files/

# 万一某个脚本被 Windows 侧 checkout 成 CRLF，路由器上的 /bin/sh 会直接
# 报 "bad interpreter: /bin/sh^M"，这里统一清一遍（.gitattributes 是第一道防线）。
while IFS= read -r f; do
	sed -i 's/\r$//' "$f"
done < <(find files/etc -type f 2>/dev/null)

# uci-defaults / hotplug.d / init.d 里的脚本没有 .sh 后缀，
# 这些文件不一定有 .sh 后缀，所以这里显式赋权。
while IFS= read -r f; do
	chmod 0755 "$f"
done < <(find files/etc/uci-defaults files/etc/hotplug.d files/etc/init.d -type f 2>/dev/null)

# 开机钩子：构建期建 rc.d 软链（不碰 rc.local）
mkdir -p files/etc/rc.d
ln -sf ../init.d/tr3000-usbwan files/etc/rc.d/S99tr3000-usbwan
chmod 0755 files/etc/tr3000-usbwan.sh

# BBR：必须排在 S90turboacc 之后（turboacc 只认 tcp_bbr.ko，会把内置 BBR 强设回 cubic）
ln -sf ../init.d/tr3000-bbr files/etc/rc.d/S99tr3000-bbr
chmod 0755 files/etc/init.d/tr3000-bbr

# ---------------------------------------------------------------- 4. 构建标签
BUILD_TAG="${WRT_RELEASE_TAG:?missing release tag}"
printf '%s\n' "$BUILD_TAG" > files/etc/tr3000-build.tag
chmod 0644 files/etc/tr3000-build.tag
log "构建标签: $BUILD_TAG"

log "扩展准备完成"
