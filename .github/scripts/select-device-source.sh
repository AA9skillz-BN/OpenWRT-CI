#!/bin/bash
set -euo pipefail

# Run immediately after clone, before feeds, patches, configuration or cache keys.
[ "${WRT_PROFILE:-}" = 'C8-688' ] || exit 0
mode=$(sed -e 's/#.*//' -e '/^[[:space:]]*$/d' -e 's/[[:space:]]//g' \
    "$GITHUB_WORKSPACE/Scripts/C8-688/source-pin.txt" | head -n1)
case "$mode" in
    ''|auto|master|main|HEAD) exit 0 ;;
esac
[[ "$mode" =~ ^[a-fA-F0-9]{40}$ ]] || { echo "Invalid C8-688 source pin: $mode" >&2; exit 1; }
git fetch --depth=1 origin "$mode"
git checkout --detach FETCH_HEAD
test "$(git rev-parse HEAD)" = "${mode,,}"
