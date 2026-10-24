#!/usr/bin/env bash
set -euo pipefail
git clone https://github.com/chenmozhijin/turboacc.git ../turboacc-source
git -C ../turboacc-source checkout --detach 530092c532839efb96e9f328d34dbf3adff4b557
# Use this reviewed package instead of a duplicate feed entry.
if [ -e package/feeds/luci/luci-app-turboacc ] || [ -L package/feeds/luci/luci-app-turboacc ]; then
  rm -f package/feeds/luci/luci-app-turboacc
fi
test ! -e package/luci-app-turboacc
cp -a ../turboacc-source/luci-app-turboacc package/
git -C ../turboacc-source rev-parse HEAD > turboacc-commit.txt
