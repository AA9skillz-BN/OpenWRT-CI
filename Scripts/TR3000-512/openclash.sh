#!/usr/bin/env bash
set -euo pipefail
commit=74ff5be54933881289ac383d457115dcdd93b135

git init ../openclash-source
git -C ../openclash-source remote add origin https://github.com/vernesong/OpenClash.git
git -C ../openclash-source fetch --depth 1 origin "$commit"
git -C ../openclash-source checkout --detach FETCH_HEAD
test "$(git -C ../openclash-source rev-parse HEAD)" = "$commit"
cp -a ../openclash-source/luci-app-openclash package/
git -C ../openclash-source rev-parse HEAD > openclash-commit.txt
