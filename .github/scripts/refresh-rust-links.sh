#!/bin/bash
set -euo pipefail

# Feed Rust installs under the target's host prefix, not staging_dir/host.
# Ask OpenWrt instead of reconstructing its architecture-specific directory.
RUST_BIN=$(make --no-print-directory -s -f - TOPDIR="$PWD" ci-rust-bin <<'MAKE'
include $(TOPDIR)/rules.mk
.PHONY: ci-rust-bin
ci-rust-bin:
	@printf '%s\n' '$(STAGING_DIR)/host/bin'
MAKE
)
mkdir -p "$RUST_BIN"
for tool in cargo rustc rustdoc; do
    test -x "$CI_RUST_ROOT/bin/$tool"
    ln -sfn "$CI_RUST_ROOT/bin/$tool" "$RUST_BIN/$tool"
done
"$RUST_BIN/rustc" --version
"$RUST_BIN/cargo" --version

export CCACHE_DIR="$PWD/.ccache"
ccache --max-size=1G
ccache --zero-stats
