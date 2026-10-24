#!/usr/bin/env bash
set -euo pipefail
for pkg in luci-theme-aurora luci-app-aurora-config; do
  # Remove only an existing feed symlink to avoid duplicate definitions.
  if [ -L "package/feeds/luci/$pkg" ]; then
    rm "package/feeds/luci/$pkg"
  fi
  test ! -e "package/$pkg"
  git clone --depth 1 --single-branch --branch master \
    "https://github.com/eamonxg/$pkg.git" "package/$pkg"
  git -C "package/$pkg" rev-parse HEAD >> aurora-commits.txt
done
