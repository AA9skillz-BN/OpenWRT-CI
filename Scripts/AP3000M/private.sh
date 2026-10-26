#!/bin/bash
# SPDX-License-Identifier: MIT
# AirPi AP3000M 专属扩展（2026-10-25 从 Scripts/PRIVATE.sh 拆出）
#
# 由 Scripts/PRIVATE.sh 的目录扫描自动 source：
#   for _ext in "$GITHUB_WORKSPACE"/Scripts/*/private.sh; do source "$_ext"; done
# 每个 private.sh 自己判断 WRT_CONFIG，不属于自己的直接 return，互不干扰。
#
# 为什么需要它：
#   luci-app-fm350 的 Makefile 不走 OpenWrt 的 rust-package.mk，而是直接调用编译机上的
#   `cargo build --release --target aarch64-unknown-linux-musl` 生成静态后端 fm350d。
#   GitHub 托管的 ubuntu runner 自带 Rust 稳定版，但默认只装了宿主 target，
#   必须补 aarch64-unknown-linux-musl 的 std，否则 fm350 编译必然失败。
#   （luci-app-airpi-fancontrol 走 feeds/packages 的 rust-package.mk，不需要这里干预。）

set +e

# 仅 AP3000M 配置生效（WRT_CONFIG 为空时也跳过）
if [[ "${WRT_CONFIG,,}" != *"ap3000m"* ]]; then
	return 0 2>/dev/null || exit 0
fi

_ap3000m_log() { echo "==> [AP3000M] $*"; }

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

	# 每日自动检查（写 /tmp 缓存给 LuCI 页面秒开），auto_check=0 时脚本自己跳过
	cp -f "$_ap3000m_ota_dir/99-ap3000m-ota-cron" "$_ap3000m_bf/etc/uci-defaults/99-ap3000m-ota-cron"
	chmod 0755 "$_ap3000m_bf/etc/uci-defaults/99-ap3000m-ota-cron"

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

# 8) 开机 RNDIS 卡死自愈
#    真机两次复现：刷机/重启后 eth2 有地址但 rx_packets=0、tx_errors 飞涨，唯一解法是
#    软重启模组（AT+CFUN=1,1）+ 重新定位 AT 口。这里做成开机 120s 后自动执行。
_ap3000m_guard_dir="$GITHUB_WORKSPACE/Scripts/AP3000M/rdnis-guard"
if [ -d "$_ap3000m_guard_dir" ]; then
	cp -f "$_ap3000m_guard_dir/ap3000m-rdnis-guard" "$_ap3000m_bf/usr/sbin/ap3000m-rdnis-guard"
	chmod 0755 "$_ap3000m_bf/usr/sbin/ap3000m-rdnis-guard"

	cp -f "$_ap3000m_guard_dir/ap3000m-rdnis-guard-boot" "$_ap3000m_bf/etc/init.d/ap3000m-rdnis-guard"
	chmod 0755 "$_ap3000m_bf/etc/init.d/ap3000m-rdnis-guard"

	mkdir -p "$_ap3000m_bf/etc/rc.d"
	ln -sf ../init.d/ap3000m-rdnis-guard "$_ap3000m_bf/etc/rc.d/S98ap3000m-rdnis-guard"

	# 运行期巡检：RNDIS 卡死也会发生在运行中（不只开机），每 5 分钟检查一次
	cp -f "$_ap3000m_guard_dir/99-ap3000m-rdnis-cron" "$_ap3000m_bf/etc/uci-defaults/99-ap3000m-rdnis-cron"
	chmod 0755 "$_ap3000m_bf/etc/uci-defaults/99-ap3000m-rdnis-cron"

	_ap3000m_log "已投放 RNDIS 自愈（开机自检 + 每 5 分钟运行期巡检，两级恢复）"
else
	_ap3000m_log "缺少 $_ap3000m_guard_dir，跳过 RNDIS 自愈"
fi

# 9) LuCI OTA 页面（luci-app-ap3000m-ota：JS 视图 + rpcd ucode 后端）
_ap3000m_luci="$GITHUB_WORKSPACE/Scripts/AP3000M/luci-app-ap3000m-ota"
if [ -d "$_ap3000m_luci" ]; then
	rm -rf package/luci-app-ap3000m-ota
	cp -rf "$_ap3000m_luci" package/luci-app-ap3000m-ota
	_ap3000m_log "已投放 LuCI OTA 页面（package/luci-app-ap3000m-ota）"
else
	_ap3000m_log "缺少 $_ap3000m_luci，跳过 LuCI OTA 页面"
fi

# 10) RNDIS 数据面调优（RPS/XPS + txqueuelen + MTU 实测 + BBR sysctl + flow offload）
_ap3000m_tune="$GITHUB_WORKSPACE/Scripts/AP3000M/net-tune"
if [ -d "$_ap3000m_tune" ]; then
	mkdir -p "$_ap3000m_bf/usr/sbin" "$_ap3000m_bf/etc/hotplug.d/net" \
		"$_ap3000m_bf/etc/sysctl.d" "$_ap3000m_bf/etc/uci-defaults"

	cp -f "$_ap3000m_tune/ap3000m-net-tune" "$_ap3000m_bf/usr/sbin/ap3000m-net-tune"
	chmod 0755 "$_ap3000m_bf/usr/sbin/ap3000m-net-tune"

	cp -f "$_ap3000m_tune/20-ap3000m-net-tune" "$_ap3000m_bf/etc/hotplug.d/net/20-ap3000m-net-tune"
	chmod 0755 "$_ap3000m_bf/etc/hotplug.d/net/20-ap3000m-net-tune"

	cp -f "$_ap3000m_tune/99-ap3000m-tune.conf" "$_ap3000m_bf/etc/sysctl.d/99-ap3000m-tune.conf"
	cp -f "$_ap3000m_tune/99-ap3000m-flowoffload" "$_ap3000m_bf/etc/uci-defaults/99-ap3000m-flowoffload"
	chmod 0755 "$_ap3000m_bf/etc/uci-defaults/99-ap3000m-flowoffload"

	_ap3000m_log "已投放 RNDIS 调优（hotplug + sysctl + flow offload）"
else
	_ap3000m_log "缺少 $_ap3000m_tune，跳过 RNDIS 调优"
fi

# 11) fullcone 补丁容错（上游漂移专用）
#     immortalwrt 自带的 package/network/config/firewall4/patches/001-...fullcone-nat.patch
#     最后修改于 2024-08，遇到上游 firewall4 大版本更新就会打不上而让整条构建失败
#     （2026-10-26 实测：firewall4 升到 bac1d656 后 hunk #8 失败，构建整体红）。
#     这里只在"已知会失败的那个版本"上把这一个补丁移开，让其余部分照常编译；
#     上游修好后（版本再变或补丁更新）本逻辑自动不生效。
_ap3000m_fw4_mk="package/network/config/firewall4/Makefile"
_ap3000m_fw4_patch="package/network/config/firewall4/patches/001-firewall4-add-support-for-fullcone-nat.patch"
if [ -f "$_ap3000m_fw4_mk" ] && [ -f "$_ap3000m_fw4_patch" ]; then
	_ap3000m_fw4_ver=$(sed -n 's/^PKG_SOURCE_VERSION:=//p' "$_ap3000m_fw4_mk" | head -n1)
	case "$_ap3000m_fw4_ver" in
		bac1d656e09b48e542f860844fe91cbba82abae2)
			mv -f "$_ap3000m_fw4_patch" "$_ap3000m_fw4_patch.disabled-ap3000m"
			_ap3000m_log "⚠ firewall4=$_ap3000m_fw4_ver 与 fullcone 补丁不兼容，已临时跳过该补丁（本版无 fullcone NAT，普通 NAT 不受影响）"
			;;
		*)
			_ap3000m_log "fullcone 补丁检查通过（firewall4=$_ap3000m_fw4_ver，保持原样）"
			;;
	esac
else
	_ap3000m_log "未找到 firewall4 Makefile 或 fullcone 补丁，跳过检查"
fi

_ap3000m_log "扩展准备完成"

# 按 Scripts/README.md 规则 3：收尾复位 shell 选项，不依赖分发器
# （本脚本只开了 set +e，没开 -u/pipefail；显式复位更稳妥）
set +eu
set +o pipefail
return 0 2>/dev/null || exit 0
