#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Cudy TR3000 512MB 构建钩子（与 AP3000M 同构，各自独立文件）
#
# 调用链：
#   WRT-CORE.yml「Custom Packages」→ Scripts/Packages.sh 末尾 source Scripts/PRIVATE.sh
#   → PRIVATE.sh 扫描 Scripts/*/private.sh → 本脚本（cwd = ./wrt 源码根）
#
# 本脚本负责四件事，全部失败即中止构建（分区/镜像不对的固件宁可不发）：
#   1. 512MB 硬改机的 UBI 分区补丁（不改则 sysupgrade 平台检查拒绝刷入）
#   2. 按固定 commit 拉取 Aurora / OpenClash / TurboACC（构建可复现）
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

# ------------------------------------------------------------ 1b. 内核启用 BBR
# 上游 target/linux/generic/config-6.18 里 BBR 是 `# CONFIG_TCP_CONG_BBR is not set`，
# 且 config/Config-kernel.in 没有暴露该选项 —— 所以 Config 里写 CONFIG_TCP_CONG_BBR=y
# 会被 make defconfig 静默丢弃（实测固件里只有 reno/cubic，日志里也没有任何 BBR 输出）。
# 这里直接改内核配置片段。
_tr3000_kcfg="target/linux/generic/config-6.18"
if [ -f "$_tr3000_kcfg" ]; then
	if grep -q '^# CONFIG_TCP_CONG_BBR is not set' "$_tr3000_kcfg"; then
		sed -i 's/^# CONFIG_TCP_CONG_BBR is not set$/CONFIG_TCP_CONG_BBR=y/' "$_tr3000_kcfg"
		log "已启用内核 BBR（$_tr3000_kcfg）"
	elif grep -q '^CONFIG_TCP_CONG_BBR=y' "$_tr3000_kcfg"; then
		log "内核 BBR 已处于启用状态"
	else
		log "警告：$_tr3000_kcfg 里没有 BBR 条目，跳过"
	fi
else
	log "警告：找不到 $_tr3000_kcfg，BBR 无法启用"
fi

# ---------------------------------------------------------- 2. 固定版本插件
# Packages.sh 已按分支最新克隆过 aurora / aurora-config / openclash，
# 这里先清掉，再按本仓库审阅过的 commit 重新拉取，保证每次构建内容一致。
log "清理 Packages.sh 的分支最新版，改拉固定 commit"
rm -rf package/luci-theme-aurora package/luci-app-aurora-config \
	package/luci-app-openclash package/OpenClash

log "克隆 OpenClash（固定 commit）"
bash "$TR_DIR/openclash.sh"
log "克隆 TurboACC（固定 commit）"
bash "$TR_DIR/turboacc.sh"
log "克隆 Aurora 主题与配置（固定 commit）"
bash "$TR_DIR/aurora.sh"

for _d in package/luci-theme-aurora package/luci-app-aurora-config \
	package/luci-app-openclash package/luci-app-turboacc; do
	[ -d "$_d" ] || { log "插件缺失: $_d"; exit 1; }
done
log "四个专属插件全部就位"

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
# WRT-CORE 的 dos2unix 步骤只处理 *.sh/*.txt，所以这里显式赋权。
while IFS= read -r f; do
	chmod 0755 "$f"
done < <(find files/etc/uci-defaults files/etc/hotplug.d files/etc/init.d -type f 2>/dev/null)

# 开机钩子：构建期建 rc.d 软链（不碰 rc.local）
mkdir -p files/etc/rc.d
ln -sf ../init.d/tr3000-usbwan files/etc/rc.d/S99tr3000-usbwan
chmod 0755 files/etc/tr3000-usbwan.sh

# ---------------------------------------------------------------- 4. 构建标签
BUILD_TAG="${WRT_CONFIG:-TR3000-512}-${WRT_INFO:-unknown}-${WRT_BRANCH:-owrt}-${WRT_DATE:-unknown}"
printf '%s\n' "$BUILD_TAG" > files/etc/tr3000-build.tag
chmod 0644 files/etc/tr3000-build.tag
log "构建标签: $BUILD_TAG"

log "扩展准备完成"
