#!/usr/bin/env bash
set -euo pipefail
git clone --depth 1 --single-branch --branch dev \
  https://github.com/vernesong/OpenClash.git ../openclash-source
cp -a ../openclash-source/luci-app-openclash package/
git -C ../openclash-source rev-parse HEAD > openclash-commit.txt
