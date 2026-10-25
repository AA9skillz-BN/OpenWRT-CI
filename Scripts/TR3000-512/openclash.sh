#!/usr/bin/env bash
# 按固定 commit 拉取 OpenClash（由 Scripts/TR3000-512/private.sh 调用，cwd = 源码根）
#
# 注意：WRT-CORE 把 ./wrt 做成指向 /mnt/build_wrt 的软链，所以源码根的 ".."
# 是 /mnt（普通用户不可写）。临时目录必须用 $GITHUB_WORKSPACE。
set -euo pipefail
commit=74ff5be54933881289ac383d457115dcdd93b135

tmp="${GITHUB_WORKSPACE:-/tmp}/.build-src"
mkdir -p "$tmp"
rm -rf "$tmp/openclash-source"

git init -q "$tmp/openclash-source"
git -C "$tmp/openclash-source" remote add origin https://github.com/vernesong/OpenClash.git
git -C "$tmp/openclash-source" fetch --depth 1 origin "$commit"
git -C "$tmp/openclash-source" checkout --detach FETCH_HEAD
test "$(git -C "$tmp/openclash-source" rev-parse HEAD)" = "$commit"

rm -rf package/luci-app-openclash
cp -a "$tmp/openclash-source/luci-app-openclash" package/
git -C "$tmp/openclash-source" rev-parse HEAD > openclash-commit.txt
