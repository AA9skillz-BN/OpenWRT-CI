#!/usr/bin/env bash
# 按固定 commit 拉取 nftables 版 TurboACC（由 Scripts/TR3000-512/private.sh 调用）
#
# 注意：WRT-CORE 把 ./wrt 做成指向 /mnt/build_wrt 的软链，源码根的 ".." 是 /mnt
# （普通用户不可写），临时目录必须放 $GITHUB_WORKSPACE。
set -euo pipefail
commit=530092c532839efb96e9f328d34dbf3adff4b557

tmp="${GITHUB_WORKSPACE:-/tmp}/.build-src"
mkdir -p "$tmp"
rm -rf "$tmp/turboacc-source"

git init -q "$tmp/turboacc-source"
git -C "$tmp/turboacc-source" remote add origin https://github.com/chenmozhijin/turboacc.git
git -C "$tmp/turboacc-source" fetch --depth 1 origin "$commit"
git -C "$tmp/turboacc-source" checkout --detach FETCH_HEAD
test "$(git -C "$tmp/turboacc-source" rev-parse HEAD)" = "$commit"

# 用这份审阅过的包替换 feed 里的同名条目的符号链接
if [ -e package/feeds/luci/luci-app-turboacc ] || [ -L package/feeds/luci/luci-app-turboacc ]; then
	rm -f package/feeds/luci/luci-app-turboacc
fi
rm -rf package/luci-app-turboacc
cp -a "$tmp/turboacc-source/luci-app-turboacc" package/
git -C "$tmp/turboacc-source" rev-parse HEAD > turboacc-commit.txt
