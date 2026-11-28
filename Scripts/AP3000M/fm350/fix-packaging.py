"""Finish fm350d with the same stripping pass used by OpenWrt packaging."""
import hashlib
from pathlib import Path
import sys

UPSTREAM_SHA256 = "9f7e44c09bbd1455469bfc568347931de958b33f4bfac67c768397bd4dce6405"
BEFORE = '''\t@if [ -n "$(TARGET_STRIP)" ] && [ -x "$(TARGET_STRIP)" ]; then \\
\t\t$(TARGET_STRIP) $(RUST_OUT) || true; \\
\telse \\
\t\techo "==> 跳过 strip（TARGET_STRIP 不可用）"; \\
\tfi
'''
AFTER = '''\t# Keep RUST_OUT identical to the binary after the package's RSTRIP pass.
\t$(RSTRIP) $(RUST_OUT)
'''
CARGO_BUILD = "$(CARGO) build --$(RUST_PROFILE) --target $(RUST_TARGET)"
LOCKED_BUILD = CARGO_BUILD + " --locked"


def patch_makefile(data):
    text = data.decode("utf-8")
    original = text.replace(AFTER, BEFORE).replace(LOCKED_BUILD, CARGO_BUILD)
    if hashlib.sha256(original.encode("utf-8")).hexdigest() != UPSTREAM_SHA256:
        raise ValueError("FM350 recipe differs from the reviewed locked source")
    if original.count(BEFORE) != 1:
        raise ValueError("FM350 strip anchor changed")
    if original.count(CARGO_BUILD) != 1:
        raise ValueError("FM350 Cargo build anchor changed")
    return original.replace(BEFORE, AFTER).replace(CARGO_BUILD, LOCKED_BUILD).encode("utf-8")


if __name__ == "__main__":
    path = Path(sys.argv[1]) / "Makefile"
    path.write_bytes(patch_makefile(path.read_bytes()))
    print("AP3000M FM350: finalize compiled binary with OpenWrt RSTRIP")
