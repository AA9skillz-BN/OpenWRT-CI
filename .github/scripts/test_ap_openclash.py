"""Validate core authenticity/architecture and install it with actual OpenClash Make rules."""
import concurrent.futures
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ap_openclash_core", Path(__file__).with_name("ap3000m-openclash-core.py"))
core = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(core)
REVIEWED_LATEST = "e553e82e01c697aefbe1555261a503f425e00ccd"


def fixture_binary(machine=183, loader=False):
    binary = bytearray(128)
    binary[:7] = b"\x7fELF\x02\x01\x01"
    struct.pack_into("<HHI", binary, 16, 2, machine, 1)
    struct.pack_into("<Q", binary, 32, 64)
    struct.pack_into("<HH", binary, 54, 56, 1)
    struct.pack_into("<IIQQQQQQ", binary, 64, 3 if loader else 1, 5, 0, 0, 0, len(binary), len(binary), 4096)
    return bytes(binary)


def snapshot(binary):
    archive = gzip.compress(binary)
    record = {"repository": "MetaCubeX/mihomo", "tag": "v1.19.34", "asset": "mihomo-linux-arm64-v1.19.34.gz",
              "size": len(archive), "sha256": hashlib.sha256(archive).hexdigest()}
    return archive, record


class OpenClashCoreTests(unittest.TestCase):
    def test_locked_avoids_moving_api_and_latest_resolves_one_exact_arm64_asset(self):
        _, locked = snapshot(fixture_binary())
        with patch.object(core, "fetch", side_effect=AssertionError("locked must not use latest API")):
            self.assertEqual(core.resolve(locked, "locked"), locked)
        latest = dict(locked, tag="v1.20.0", asset="mihomo-linux-arm64-v1.20.0.gz")
        release = {"draft": False, "prerelease": False, "tag_name": latest["tag"],
                   "assets": [{"name": latest["asset"], "size": latest["size"], "digest": "sha256:" + latest["sha256"]}]}
        with patch.object(core, "fetch", return_value=json.dumps(release).encode()) as fetch:
            self.assertEqual(core.resolve(locked, "latest"), latest)
            self.assertEqual(fetch.call_count, 1)
        for change in ({"prerelease": True}, {"draft": True}, {"assets": []},
                       {"assets": [{"name": latest["asset"], "size": 10, "digest": None}]},
                       {"assets": release["assets"] * 2}):
            with self.subTest(change=change), patch.object(core, "fetch", return_value=json.dumps(dict(release, **change)).encode()):
                with self.assertRaises(ValueError):
                    core.resolve(locked, "latest")

    def test_bad_checksum_wrong_architecture_dynamic_loader_and_oversize_are_rejected(self):
        good, record = snapshot(fixture_binary())
        self.assertEqual(core.unpack(good, record), fixture_binary())
        bad_table = bytearray(fixture_binary())
        struct.pack_into("<Q", bad_table, 32, 5000)
        for archive, item in [(good + b"x", record), (good, dict(record, sha256="a" * 64)),
                              snapshot(fixture_binary(machine=62)), snapshot(fixture_binary(loader=True)),
                              snapshot(bytes(bad_table)), snapshot(b"not an executable")]:
            with self.subTest(item=item), self.assertRaises(ValueError):
                core.unpack(archive, item)
        with patch.object(core, "MAX_BINARY", 127), self.assertRaises(ValueError):
            core.unpack(good, record)

    def test_failed_download_never_overwrites_existing_package_core(self):
        archive, record = snapshot(fixture_binary())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "tree/package/luci-app-openclash"
            (package / "root/etc/config").mkdir(parents=True)
            (package / "Makefile").touch()
            (package / "root/etc/config/openclash").touch()
            existing = package / "root/etc/openclash/core/clash_meta"
            existing.parent.mkdir(parents=True)
            existing.write_bytes(b"existing core")
            (root / "Profiles").mkdir()
            lock = root / "Profiles/openclash-core.lock.json"
            lock.write_text(json.dumps(record))
            with patch.object(core, "fetch", return_value=archive + b"corrupt"), self.assertRaises(ValueError):
                core.install(root, root / "tree", "locked")
            self.assertEqual(existing.read_bytes(), b"existing core")
            self.assertEqual(json.loads(lock.read_bytes()), record)


@unittest.skipIf(os.name == "nt", "real ARM64 execution and upstream Make installation run on Linux")
class ActualOpenClashTests(unittest.TestCase):
    def test_official_locked_core_runs_and_both_plugin_snapshots_install_exact_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory)
            repo = top / "repo"
            (repo / "Profiles").mkdir(parents=True)
            shutil.copy2(REPO / "Profiles/openclash-core.lock.json", repo / "Profiles/openclash-core.lock.json")
            locked = json.loads((REPO / "Profiles/packages.lock.json").read_bytes())["openclash"]["commit"]
            source_files = ["Makefile", "root/etc/config/openclash", "root/etc/init.d/openclash",
                            "root/etc/uci-defaults/luci-openclash", "luasrc/controller/openclash.lua",
                            "luasrc/openclash.lua", "luasrc/view/openclash/config_edit.htm"]
            def get(item):
                revision, name = item
                url = "https://raw.githubusercontent.com/vernesong/OpenClash/" + revision + "/luci-app-openclash/" + name
                with urllib.request.urlopen(url, timeout=60) as response:
                    return item, response.read()
            requests = [(revision, name) for revision in dict.fromkeys([locked, REVIEWED_LATEST]) for name in source_files]
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                downloaded = dict(pool.map(get, requests))
            record = None
            for revision in dict.fromkeys([locked, REVIEWED_LATEST]):
                with self.subTest(plugin=revision):
                    tree = top / revision
                    package = tree / "package/luci-app-openclash"
                    for name in source_files:
                        path = package / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(downloaded[(revision, name)])
                    config = package / "root/etc/config/openclash"
                    original_config = config.read_bytes()
                    self.assertIn(b"option enable '0'", original_config)
                    if record is None:
                        record = core.install(repo, tree, "locked")
                        original_core = (package / "root/etc/openclash/core/clash_meta").read_bytes()
                        original_license = (package / "root/usr/share/openclash/mihomo-LICENSE").read_bytes()
                        qemu = shutil.which("qemu-aarch64")
                        self.assertIsNotNone(qemu, "qemu-aarch64 required for actual ARM64 smoke test")
                        result = subprocess.run([qemu, str(package / "root/etc/openclash/core/clash_meta"), "-v"],
                                                text=True, capture_output=True, timeout=30)
                        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                        self.assertIn(record["tag"].removeprefix("v"), result.stdout)
                        self.assertIn("Mihomo", result.stdout)
                        print("Official ARM64 core executed: " + result.stdout.strip())
                    else:
                        # Reuse the same verified core so this Make test does not download twice.
                        first = top / locked / "package/luci-app-openclash/root"
                        for relative in ("etc/openclash/core/clash_meta", "usr/share/openclash/mihomo-LICENSE",
                                         "usr/share/openclash/ap3000m-core.json", "lib/upgrade/keep.d/luci-app-openclash"):
                            path = package / "root" / relative
                            path.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(first / relative, path)
                    self.assertEqual(config.read_bytes(), original_config)
                    (tree / "include").mkdir()
                    (tree / "include/package.mk").touch()
                    (tree / "rules.mk").write_text('''
INCLUDE_DIR:=$(TOPDIR)/include
BUILD_DIR:=$(TOPDIR)/build
CURDIR:=$(CURDIR)
CP:=cp -fpR
INSTALL_DIR:=install -d
INSTALL_DATA:=install -m0644
INSTALL_BIN:=install -m0755
''')
                    (package / "probe.mk").write_text('''include Makefile
define Probe
.PHONY: prepare install
prepare:
\t$(Build/Prepare)
install:
\ttouch $(PKG_BUILD_DIR)/openclash.zh-cn.lmo
\t$(call Package/luci-app-openclash/install,$(TOPDIR)/rootfs)
endef
$(eval $(Probe))
''')
                    result = subprocess.run(["make", "--no-print-directory", "-f", "probe.mk", f"TOPDIR={tree}", "prepare", "install"],
                                            cwd=package, text=True, capture_output=True, timeout=45)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    root = tree / "rootfs"
                    installed_core = root / "etc/openclash/core/clash_meta"
                    self.assertEqual(installed_core.read_bytes(), original_core)
                    self.assertTrue(installed_core.stat().st_mode & 0o111)
                    self.assertEqual((root / "usr/share/openclash/mihomo-LICENSE").read_bytes(), original_license)
                    self.assertEqual((root / "etc/config/openclash").read_bytes(), original_config)
                    profile = json.loads((REPO / "Profiles/AP3000M.json").read_bytes())
                    entries = [item for item in profile["required_files"] if item.get("build_source", "").startswith("package/luci-app-openclash/")]
                    self.assertEqual(len(entries), 7)
                    for item in entries:
                        path = root / item["path"].lstrip("/")
                        self.assertEqual(path.read_bytes(), (tree / item["build_source"]).read_bytes())
                        if item.get("executable"):
                            self.assertTrue(path.stat().st_mode & 0o111)
                    self.assertEqual((root / "lib/upgrade/keep.d/luci-app-openclash").read_text(), "/etc/openclash/\n")
                    self.assertIn("/etc/openclash/", (package / "root/etc/uci-defaults/luci-openclash").read_text())
                    print("Actual upstream OpenClash prepare/install verified: " + revision)


if __name__ == "__main__":
    unittest.main()
