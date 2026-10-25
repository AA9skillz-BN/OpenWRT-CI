#!/usr/bin/env bash
set -euo pipefail
cp "$GITHUB_WORKSPACE/Config/TR3000-512.txt" .config
make defconfig
cp .config build.config
python3 "$GITHUB_WORKSPACE/Scripts/TR3000-512/verify-build.py" --config .config
mkdir -p files/etc/uci-defaults
cp -a "$GITHUB_WORKSPACE/Files/TR3000-512/." files/
chmod +x files/etc/uci-defaults/99-tr3000-*
