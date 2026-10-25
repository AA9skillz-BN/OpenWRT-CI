#!/bin/bash
# SPDX-License-Identifier: MIT
# AirPi AP3000M 专用扩展脚本
#
# 由 Scripts/Packages.sh 末尾的官方扩展钩子自动 source：
#   if [ -f "$GITHUB_WORKSPACE/Scripts/PRIVATE.sh" ]; then source "$GITHUB_WORKSPACE/Scripts/PRIVATE.sh"; fi
#
# 为什么需要它：
#   luci-app-fm350 的 Makefile 不走 OpenWrt 的 rust-package.mk，而是直接调用编译机上的
#   `cargo build --release --target aarch64-unknown-linux-musl` 生成静态后端 fm350d。
#   GitHub 托管的 ubuntu runner 自带 Rust 稳定版，但默认只装了宿主 target，
#   必须补 aarch64-unknown-linux-musl 的 std，否则 fm350 编译必然失败。
#   （luci-app-airpi-fancontrol 走 feeds/packages 的 rust-package.mk，不需要这里干预。）
#
# 非 AP3000M 构建：直接返回，不改变原有流程的任何行为。

set +e

_ap3000m_log() { echo "==> [AP3000M] $*"; }

# ---- TR3000 分支：走独立钩子（分区补丁 / 固定版本插件 / 首启 / F50 即插即用）----
if [[ "${WRT_CONFIG,,}" == *"tr3000"* ]]; then
	_tr_private="$GITHUB_WORKSPACE/Scripts/TR3000-512/private.sh"
	if [ -f "$_tr_private" ]; then
		# shellcheck disable=SC1090
		source "$_tr_private"
		return $? 2>/dev/null || exit $?
	fi
	echo "==> [TR3000] 缺少 $_tr_private，跳过专属扩展" >&2
	return 0 2>/dev/null || exit 0
fi

# 仅 AP3000M 配置生效（WRT_CONFIG 为空时也跳过）
if [[ "${WRT_CONFIG,,}" != *"ap3000m"* ]]; then
	return 0 2>/dev/null || exit 0
fi

RUST_TARGET="aarch64-unknown-linux-musl"

# 1) rustup 不在 PATH 但已安装的情况（$HOME/.cargo/bin 未导出）
if ! command -v rustup >/dev/null 2>&1 && [ -x "$HOME/.cargo/bin/rustup" ]; then
	export PATH="$HOME/.cargo/bin:$PATH"
fi

# 2) 极端情况：runner 完全没有 Rust，装一个最小化工具链
if ! command -v rustup >/dev/null 2>&1; then
	_ap3000m_log "未检测到 rustup，安装最小化 Rust 工具链"
	curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs |
		sh -s -- -y --profile minimal --default-toolchain stable >/dev/null 2>&1
	export PATH="$HOME/.cargo/bin:$PATH"
fi

# 3) 把 cargo 目录写进后续步骤的 PATH（GITHUB_PATH 跨步骤生效）
if [ -n "${GITHUB_PATH:-}" ] && [ -d "$HOME/.cargo/bin" ]; then
	echo "$HOME/.cargo/bin" >>"$GITHUB_PATH"
fi

# 4) 补齐 aarch64 musl 目标
if command -v rustup >/dev/null 2>&1; then
	_ap3000m_log "rustup target add $RUST_TARGET"
	rustup target add "$RUST_TARGET" ||
		_ap3000m_log "rustup target add 失败，若编译机 cargo 来自 feeds/packages 的 rust 主机包可忽略"
fi

# 5) 打印工具链信息 + 预检两个插件是否已由 Packages.sh 克隆到位
command -v cargo >/dev/null 2>&1 && _ap3000m_log "cargo: $(cargo --version 2>/dev/null)"
command -v rustc >/dev/null 2>&1 && _ap3000m_log "rustc: $(rustc --version 2>/dev/null)"
command -v rustup >/dev/null 2>&1 && _ap3000m_log "已装 target: $(rustup target list --installed 2>/dev/null | tr '\n' ' ')"

for _dir in package/luci-app-fm350 package/luci-app-airpi3000m-fancontrol; do
	if [ -d "$_dir" ]; then
		_ap3000m_log "插件已就绪: $_dir"
	else
		_ap3000m_log "插件缺失: $_dir（请检查 Scripts/Packages.sh 的 UPDATE_PACKAGE 列表）"
	fi
done

# 6) 投放 AP3000M 专属的首次开机配置（FM350 的 AT 口 / APN / CID 自动纠正）
#    真机实测：插件默认 at_port=/dev/ttyUSB1 在本机是哑口（真 AT 口是 USB 接口 :1.6
#    = /dev/ttyUSB3），且默认 cid=1 会拨到运营商的 ims 承载，表现为「能读到模组却上不了网」。
_ap3000m_uci="$GITHUB_WORKSPACE/Scripts/AP3000M/fm350-ap3000m"
if [ -f "$_ap3000m_uci" ]; then
	_ap3000m_dst="package/base-files/files/etc/uci-defaults/99-ap3000m-fm350"
	mkdir -p "$(dirname "$_ap3000m_dst")"
	cp -f "$_ap3000m_uci" "$_ap3000m_dst"
	chmod 0755 "$_ap3000m_dst"
	_ap3000m_log "已投放开机自动配置: $_ap3000m_dst"
else
	_ap3000m_log "缺少 $_ap3000m_uci，FM350 将沿用插件默认值（AT 口可能不对）"
fi

# 7) 一键 OTA：CLI 脚本 + 构建标签 + 升级结果回写
#    构建标签就是将来的 Release tag：WRT-CORE 在「Initialization Values」里已经算好
#    WRT_CONFIG/WRT_INFO/WRT_BRANCH/WRT_DATE 并写进 $GITHUB_ENV，本步能直接读到。
_ap3000m_ota_dir="$GITHUB_WORKSPACE/Scripts/AP3000M/ota"
_ap3000m_bf="package/base-files/files"
if [ -d "$_ap3000m_ota_dir" ]; then
	mkdir -p "$_ap3000m_bf/usr/sbin" "$_ap3000m_bf/etc/config"

	cp -f "$_ap3000m_ota_dir/ap3000m-ota" "$_ap3000m_bf/usr/sbin/ap3000m-ota"
	chmod 0755 "$_ap3000m_bf/usr/sbin/ap3000m-ota"

	cp -f "$_ap3000m_ota_dir/ap3000m_ota.config" "$_ap3000m_bf/etc/config/ap3000m_ota"
	chmod 0644 "$_ap3000m_bf/etc/config/ap3000m_ota"

	_ap3000m_tag="$WRT_CONFIG-$WRT_INFO-$WRT_BRANCH-$WRT_DATE"
	printf '%s\n' "$_ap3000m_tag" >"$_ap3000m_bf/etc/ap3000m-build.tag"
	chmod 0644 "$_ap3000m_bf/etc/ap3000m-build.tag"

	# 升级状态要跨越 sysupgrade 保留（只保留这两个文件，build.tag 必须用新镜像里的）
	if ! grep -q '^/etc/ap3000m-pending$' "$_ap3000m_bf/etc/sysupgrade.conf" 2>/dev/null; then
		printf '/etc/ap3000m-pending\n/etc/ap3000m-result\n' >>"$_ap3000m_bf/etc/sysupgrade.conf"
	fi

	# 开机回写升级结果：自带 init.d 脚本 + 构建期建 rc.d 软链（不碰 rc.local）
	cp -f "$_ap3000m_ota_dir/ap3000m-ota-boot" "$_ap3000m_bf/etc/init.d/ap3000m-ota-boot"
	chmod 0755 "$_ap3000m_bf/etc/init.d/ap3000m-ota-boot"
	mkdir -p "$_ap3000m_bf/etc/rc.d"
	ln -sf ../init.d/ap3000m-ota-boot "$_ap3000m_bf/etc/rc.d/S99ap3000m-ota-boot"

	_ap3000m_log "已投放 OTA：ap3000m-ota（构建标签 $_ap3000m_tag）"
else
	_ap3000m_log "缺少 $_ap3000m_ota_dir，跳过 OTA 集成"
fi

_ap3000m_log "扩展准备完成"
return 0 2>/dev/null || exit 0
