#!/usr/bin/env bash
set -euo pipefail
commit=530092c532839efb96e9f328d34dbf3adff4b557
git init ../turboacc-source
git -C ../turboacc-source remote add origin https://github.com/chenmozhijin/turboacc.git
git -C ../turboacc-source fetch --depth 1 origin "$commit"
git -C ../turboacc-source checkout --detach FETCH_HEAD
test "$(git -C ../turboacc-source rev-parse HEAD)" = "$commit"
# Use this reviewed package instead of a duplicate feed entry.
if [ -e package/feeds/luci/luci-app-turboacc ] || [ -L package/feeds/luci/luci-app-turboacc ]; then
  rm -f package/feeds/luci/luci-app-turboacc
fi
test ! -e package/luci-app-turboacc
cp -a ../turboacc-source/luci-app-turboacc package/
git -C ../turboacc-source rev-parse HEAD > turboacc-commit.txt
