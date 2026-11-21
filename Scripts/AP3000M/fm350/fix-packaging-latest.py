"""Finish fm350d with the same stripping pass used by OpenWrt packaging."""
import hashlib
from pathlib import Path
import sys

UPSTREAM_SHA256 = "e9d61f3000df33a5f712fcd669f6db2f82951e2f3eb36ba5d10a776f36bba84d"
REVIEWED_SHA256 = {UPSTREAM_SHA256}
BEFORE = '''\t@if [ -n "$(TARGET_STRIP)" ] && [ -x "$(TARGET_STRIP)" ]; then \\
\t\t$(TARGET_STRIP) $(RUST_OUT) || true; \\
\telse \\
\t\techo "==> 跳过 strip（TARGET_STRIP 不可用）"; \\
\tfi
'''
AFTER = '''\t# Keep RUST_OUT identical to the binary after the package's RSTRIP pass.
\t$(RSTRIP) $(RUST_OUT)
'''


def patch_makefile(data):
    text = data.decode("utf-8")
    original = text.replace(AFTER, BEFORE)
    if hashlib.sha256(original.encode("utf-8")).hexdigest() not in REVIEWED_SHA256:
        raise ValueError("FM350 recipe differs from the reviewed locked source")
    if original.count(BEFORE) != 1:
        raise ValueError("FM350 strip anchor changed")
    return original.replace(BEFORE, AFTER).encode("utf-8")


if __name__ == "__main__":
    path = Path(sys.argv[1]) / "Makefile"
    path.write_bytes(patch_makefile(path.read_bytes()))
    print("AP3000M FM350: finalize compiled binary with OpenWrt RSTRIP")
