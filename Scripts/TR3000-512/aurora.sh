#!/usr/bin/env bash
set -euo pipefail
for pkg in luci-theme-aurora luci-app-aurora-config; do
  case "$pkg" in
    luci-theme-aurora) commit=84c27b66952e6fc4be60128067a6b88bfd34afb5 ;;
    luci-app-aurora-config) commit=a5299ce0144859f47683ec3bc40805bf616f534f ;;
  esac
  # Remove only an existing feed symlink to avoid duplicate definitions.
  if [ -L "package/feeds/luci/$pkg" ]; then
    rm "package/feeds/luci/$pkg"
  fi
  test ! -e "package/$pkg"
  git init "package/$pkg"
  git -C "package/$pkg" remote add origin "https://github.com/eamonxg/$pkg.git"
  git -C "package/$pkg" fetch --depth 1 origin "$commit"
  git -C "package/$pkg" checkout --detach FETCH_HEAD
  test "$(git -C "package/$pkg" rev-parse HEAD)" = "$commit"
  git -C "package/$pkg" rev-parse HEAD >> aurora-commits.txt
done
