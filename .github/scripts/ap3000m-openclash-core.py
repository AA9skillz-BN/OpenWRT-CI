#!/usr/bin/env python3
"""Install a verified official static ARM64 Mihomo core into the OpenClash package."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import struct
import urllib.request

REPOSITORY = "MetaCubeX/mihomo"
MAX_ARCHIVE = 40 * 1024 * 1024
MAX_BINARY = 100 * 1024 * 1024


def fetch(url, limit):
    request = urllib.request.Request(url, headers={"User-Agent": "OpenWRT-CI-AP3000M", "Accept": "application/vnd.github+json"})
    # Authenticate only the GitHub API, never release storage or raw content.
    # Unredirected headers are deliberately omitted by urllib on redirects.
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        request.add_unredirected_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read(limit + 1)
    if not data or len(data) > limit:
        raise ValueError("OpenClash core download is empty or exceeds its size limit")
    return data


def validate_snapshot(snapshot):
    if snapshot.get("repository") != REPOSITORY or not re.fullmatch(r"v\d+\.\d+\.\d+", snapshot.get("tag", "")):
        raise ValueError("OpenClash core must use a stable official Mihomo release")
    if snapshot.get("asset") != "mihomo-linux-arm64-" + snapshot["tag"] + ".gz":
        raise ValueError("OpenClash core asset is not the intended Linux ARM64 binary")
    if not re.fullmatch(r"[a-f0-9]{64}", snapshot.get("sha256", "")):
        raise ValueError("OpenClash core requires an exact SHA-256")
    size = snapshot.get("size")
    if type(size) is not int or not 0 < size <= MAX_ARCHIVE:
        raise ValueError("OpenClash core archive size is invalid")
    return dict(snapshot)


def resolve(lock, mode):
    if mode == "locked":
        return validate_snapshot(lock)
    if mode != "latest":
        raise ValueError("OpenClash core plugin mode must be locked or latest")
    # Resolve once. The download URL and digest below describe this exact asset,
    # even when another release is published while the firmware is building.
    release = json.loads(fetch("https://api.github.com/repos/" + REPOSITORY + "/releases/latest", 2 * 1024 * 1024))
    if release.get("draft") is not False or release.get("prerelease") is not False:
        raise ValueError("OpenClash core release must be public and stable")
    tag = release.get("tag_name", "")
    name = "mihomo-linux-arm64-" + tag + ".gz"
    assets = [item for item in release.get("assets", []) if item.get("name") == name]
    if len(assets) != 1:
        raise ValueError("OpenClash core release must contain one exact ARM64 gzip asset")
    asset = assets[0]
    digest = asset.get("digest") or ""
    if not digest.startswith("sha256:"):
        raise ValueError("OpenClash core release asset has no SHA-256 digest")
    return validate_snapshot({"repository": REPOSITORY, "tag": tag, "asset": name,
                              "size": asset.get("size"), "sha256": digest.removeprefix("sha256:")})


def unpack(archive, snapshot):
    if len(archive) != snapshot["size"] or hashlib.sha256(archive).hexdigest() != snapshot["sha256"]:
        raise ValueError("OpenClash core archive size or SHA-256 differs from the resolved release")
    with gzip.GzipFile(fileobj=io.BytesIO(archive)) as stream:
        binary = stream.read(MAX_BINARY + 1)
    if not 64 <= len(binary) <= MAX_BINARY or binary[:7] != b"\x7fELF\x02\x01\x01":
        raise ValueError("OpenClash core is not a bounded little-endian ELF64 binary")
    kind, machine, version = struct.unpack_from("<HHI", binary, 16)
    if kind not in (2, 3) or machine != 183 or version != 1:
        raise ValueError("OpenClash core is not a Linux ARM64 executable")
    offset = struct.unpack_from("<Q", binary, 32)[0]
    entry_size, count = struct.unpack_from("<HH", binary, 54)
    if count == 0 or entry_size != 56 or offset < 64 or offset + count * entry_size > len(binary):
        raise ValueError("OpenClash core has an invalid ELF program table")
    for index in range(count):
        header = struct.unpack_from("<IIQQQQQQ", binary, offset + index * entry_size)
        if header[0] == 3:
            raise ValueError("OpenClash core requires a dynamic loader; use the static official build")
        if header[2] + header[5] > len(binary):
            raise ValueError("OpenClash core has an invalid ELF segment")
    return binary


def install(repo, tree, mode, device="AP3000M"):
    if device not in ("AP3000M", "TR3000-512"):
        raise ValueError("Unsupported OpenClash core device")
    metadata_name = "ap3000m-core.json" if device == "AP3000M" else "tr3000-core.json"
    package = tree / "package/luci-app-openclash"
    if not (package / "Makefile").is_file() or not (package / "root/etc/config/openclash").is_file():
        raise ValueError("OpenClash package must be installed before adding its core")
    snapshot = resolve(json.loads((repo / "Profiles/openclash-core.lock.json").read_bytes()), mode)
    base = "https://github.com/" + REPOSITORY + "/releases/download/" + snapshot["tag"] + "/"
    binary = unpack(fetch(base + snapshot["asset"], MAX_ARCHIVE), snapshot)
    license_url = "https://raw.githubusercontent.com/" + REPOSITORY + "/" + snapshot["tag"] + "/LICENSE"
    license_text = fetch(license_url, 128 * 1024)
    record = {"schema": 1, "mode": mode, **snapshot,
              "binary_sha256": hashlib.sha256(binary).hexdigest(), "binary_size": len(binary),
              "elf_machine": 183, "static": True,
              "source": "https://github.com/" + REPOSITORY + "/tree/" + snapshot["tag"],
              "source_archive": "https://github.com/" + REPOSITORY + "/archive/refs/tags/" + snapshot["tag"] + ".tar.gz",
              "license_url": license_url, "license_sha256": hashlib.sha256(license_text).hexdigest()}
    core = package / "root/etc/openclash/core/clash_meta"
    core.parent.mkdir(parents=True, exist_ok=True)
    core.write_bytes(binary)
    core.chmod(0o755)
    share = package / "root/usr/share/openclash"
    share.mkdir(parents=True, exist_ok=True)
    (share / "mihomo-LICENSE").write_bytes(license_text)
    metadata = json.dumps(record, indent=2) + "\n"
    (share / metadata_name).write_text(metadata, encoding="utf-8", newline="\n")
    # The upstream first-boot script uses the same path and rule. Put it in the
    # image too, so data backup works before or after first-boot processing.
    keep = package / "root/lib/upgrade/keep.d/luci-app-openclash"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text("/etc/openclash/\n", encoding="utf-8", newline="\n")
    reports = repo / "build-reports"
    reports.mkdir(exist_ok=True)
    (reports / "openclash-core.json").write_text(metadata, encoding="utf-8", newline="\n")
    print(f"{device} OpenClash core: {mode} {snapshot['tag']} ARM64 static SHA256={record['binary_sha256']}", flush=True)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(os.environ.get("GITHUB_WORKSPACE", ".")))
    parser.add_argument("--tree", type=Path, default=Path.cwd())
    parser.add_argument("--mode", choices=("locked", "latest"), default=os.environ.get("WRT_PLUGIN_MODE", "locked"))
    parser.add_argument("--device", choices=("AP3000M", "TR3000-512"), default="AP3000M")
    args = parser.parse_args()
    install(args.repo.resolve(), args.tree.resolve(), args.mode, args.device)


if __name__ == "__main__":
    main()
