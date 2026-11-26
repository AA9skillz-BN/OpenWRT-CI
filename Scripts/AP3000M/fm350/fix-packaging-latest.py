"""Finish fm350d with OpenWrt RSTRIP via structural strip-anchor verification."""
from pathlib import Path
import sys

BEFORE = '''\t@if [ -n "$(TARGET_STRIP)" ] && [ -x "$(TARGET_STRIP)" ]; then \\
\t\t$(TARGET_STRIP) $(RUST_OUT) || true; \\
\telse \\
\t\techo "==> 跳过 strip（TARGET_STRIP 不可用）"; \\
\tfi
'''
AFTER = '''\t# Keep RUST_OUT identical to the binary after the package's RSTRIP pass.
\t$(RSTRIP) $(RUST_OUT)
'''
CARGO_BUILD = "\t\t$(CARGO) build --$(RUST_PROFILE) --target $(RUST_TARGET)\n"
LOCKED_BUILD = CARGO_BUILD.rstrip("\n") + " --locked\n"


def patch_makefile(data):
    text = data.decode("utf-8")
    if "PKG_NAME:=luci-app-fm350" not in text or "RUST_OUT" not in text:
        raise ValueError("FM350 recipe identity or RUST_OUT definition missing")
    if text.count(CARGO_BUILD) + text.count(LOCKED_BUILD) != 1:
        raise ValueError("FM350 Cargo build anchor changed")
    text = text.replace(CARGO_BUILD, LOCKED_BUILD, 1)
    if AFTER in text:
        if text.count(AFTER) != 1 or BEFORE in text:
            raise ValueError("FM350 strip anchor duplicated or inconsistent")
        return text.encode("utf-8")
    if text.count(BEFORE) != 1:
        raise ValueError("FM350 strip anchor changed; review latest Makefile")
    return text.replace(BEFORE, AFTER, 1).encode("utf-8")


if __name__ == "__main__":
    path = Path(sys.argv[1]) / "Makefile"
    path.write_bytes(patch_makefile(path.read_bytes()))
    print("AP3000M FM350: finalize compiled binary with OpenWrt RSTRIP")
