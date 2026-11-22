"""Validate core authenticity/architecture and install it with actual OpenClash Make rules."""
import concurrent.futures
import functools
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
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
    def test_preserve_core_patch_is_repeatable_and_rejects_changed_strip_rules(self):
        recipe = "include $(TOPDIR)/rules.mk\ninclude $(INCLUDE_DIR)/package.mk\n"
        adapted = core.preserve_package_core(recipe)
        self.assertEqual(core.preserve_package_core(adapted), adapted)
        self.assertIn("RSTRIP:=:", adapted)
        for bad in ("", recipe * 2, recipe + "RSTRIP=other\n", recipe + "override RSTRIP := other\n"):
            with self.subTest(recipe=bad), self.assertRaises(ValueError):
                core.preserve_package_core(bad)

    def test_api_authentication_is_scoped_and_not_forwarded_on_redirect(self):
        api = "https://api.github.com/repos/MetaCubeX/mihomo/releases/latest"
        urls = [api, "https://github.com/MetaCubeX/mihomo/releases/download/v1.19.34/core.gz",
                "https://raw.githubusercontent.com/MetaCubeX/mihomo/v1.19.34/LICENSE",
                "https://api.github.com.example.org/", "http://api.github.com/"]
        for env in ({"GH_TOKEN": "test-token"}, {"GITHUB_TOKEN": "test-token"}, {}):
            for url in urls:
                with self.subTest(env=list(env), url=url), patch.dict(os.environ, env, clear=True):
                    with patch.object(core.urllib.request, "urlopen", return_value=io.BytesIO(b"ok")) as open_url:
                        self.assertEqual(core.fetch(url, 10), b"ok")
                    request = open_url.call_args.args[0]
                    expected = "Bearer test-token" if env and url == api else None
                    self.assertEqual(request.get_header("Authorization"), expected)
                    redirected = urllib.request.HTTPRedirectHandler().redirect_request(
                        request, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/core.gz")
                    self.assertIsNone(redirected.get_header("Authorization"))

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
            (package / "Makefile").write_text("\ninclude $(INCLUDE_DIR)/package.mk\n")
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
        latest = core.resolve(json.loads((REPO / "Profiles/openclash-core.lock.json").read_bytes()), "latest")
        print("Authenticated official latest Mihomo API resolved: " + latest["tag"])
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory)
            source = json.loads((REPO / "Profiles/sources.lock.json").read_bytes())["AP3000M"]
            raw = "https://raw.githubusercontent.com/immortalwrt/immortalwrt/" + source["commit"] + "/"
            rules = core.fetch(raw + "rules.mk", 100 * 1024).decode()
            strip_rules = rules.split("# strip an entire directory\n", 1)[1].split("\nNINJA =", 1)[0]
            sstrip = core.fetch("https://sources.openwrt.org/ELFkickers-3.2.tar.gz", 2 * 1024 * 1024)
            self.assertEqual(hashlib.sha256(sstrip).hexdigest(),
                             "9b81e6c53e0c94fc198d9882eb737156f36d565152dc32118897c77b06a2687c")
            tools = top / "tools"
            with tarfile.open(fileobj=io.BytesIO(sstrip)) as archive:
                archive.extractall(tools, filter="data")
            tools = next(tools.iterdir())
            result = subprocess.run(["make", "-C", str(tools), "bin/sstrip"], text=True, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            (top / "scripts").mkdir()
            rstrip = top / "scripts/rstrip.sh"
            rstrip.write_bytes(core.fetch(raw + "scripts/rstrip.sh", 100 * 1024))
            rstrip.chmod(0o755)
            (tools / "bin/patchelf").symlink_to(shutil.which("patchelf"))
            strip_setup = f'''\nSCRIPT_DIR:={top}/scripts
STAGING_DIR_HOST:={tools}
CONFIG_USE_SSTRIP:=y
CONFIG_SSTRIP_DISCARD_TRAILING_ZEROES:=y
qstrip=$(subst ",,$(1))
''' + strip_rules
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
            cached_fetch = functools.lru_cache(maxsize=8)(core.fetch)
            cases = [(rev, device) for rev in dict.fromkeys([locked, REVIEWED_LATEST]) for device in ("AP3000M", "TR3000-512")]
            for revision, device in cases:
                with self.subTest(plugin=revision, device=device):
                    tree = top / (revision + "-" + device)
                    package = tree / "package/luci-app-openclash"
                    for name in source_files:
                        path = package / name
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(downloaded[(revision, name)])
                    config = package / "root/etc/config/openclash"
                    original_config = config.read_bytes()
                    self.assertIn(b"option enable '0'", original_config)
                    with patch.object(core, "fetch", cached_fetch):
                        record = core.install(repo, tree, "locked", device)
                    original_core = (package / "root/etc/openclash/core/clash_meta").read_bytes()
                    # Reproduce the missed package finalization stage with real
                    # upstream rules/sstrip, rather than a fake mutating command.
                    before_strip = tree / "unadapted-core"
                    before_strip.write_bytes(original_core)
                    (tree / "strip.mk").write_text(strip_setup + '\nstrip:\n\t$(RSTRIP) $(TOPDIR)/unadapted-core\n')
                    result = subprocess.run(["make", "-f", "strip.mk", f"TOPDIR={tree}", "strip"],
                                            cwd=tree, text=True, capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertNotEqual(before_strip.read_bytes(), original_core,
                                        "unadapted package strip must reproduce the binary mismatch")
                    print("Official Mihomo package strip mismatch reproduced: " + device + " " + revision)
                    original_license = (package / "root/usr/share/openclash/mihomo-LICENSE").read_bytes()
                    qemu = shutil.which("qemu-aarch64")
                    self.assertIsNotNone(qemu, "qemu-aarch64 required for actual ARM64 smoke test")
                    result = subprocess.run([qemu, str(package / "root/etc/openclash/core/clash_meta"), "-v"],
                                            text=True, capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn(record["tag"].removeprefix("v"), result.stdout)
                    self.assertIn("Mihomo", result.stdout)
                    print(device + " official ARM64 core executed: " + result.stdout.strip())
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
''' + strip_setup)
                    (package / "probe.mk").write_text('''include Makefile
define Probe
.PHONY: prepare install packaged
prepare:
\tmkdir -p $(PKG_BUILD_DIR)
\t$(Build/Prepare)
install:
\ttouch $(PKG_BUILD_DIR)/openclash.zh-cn.lmo
\t$(call Package/luci-app-openclash/install,$(TOPDIR)/rootfs)
packaged:
\t$(RSTRIP) $(TOPDIR)/rootfs
endef
$(eval $(Probe))
''')
                    result = subprocess.run(["make", "--no-print-directory", "-f", "probe.mk", f"TOPDIR={tree}", "prepare", "install", "packaged"],
                                            cwd=package, text=True, capture_output=True, timeout=45)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    result = subprocess.run(["make", "--no-print-directory", "-f", "probe.mk", f"TOPDIR={tree}", "packaged"],
                                            cwd=package, text=True, capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    root = tree / "rootfs"
                    installed_core = root / "etc/openclash/core/clash_meta"
                    self.assertEqual(installed_core.read_bytes(), original_core)
                    self.assertTrue(installed_core.stat().st_mode & 0o111)
                    self.assertEqual((root / "usr/share/openclash/mihomo-LICENSE").read_bytes(), original_license)
                    self.assertEqual((root / "etc/config/openclash").read_bytes(), original_config)
                    profile = json.loads((REPO / "Profiles" / (device + ".json")).read_bytes())
                    entries = [item for item in profile["required_files"] if item.get("build_source", "").startswith("package/luci-app-openclash/")]
                    self.assertEqual(len(entries), 7)
                    for item in entries:
                        path = root / item["path"].lstrip("/")
                        self.assertEqual(path.read_bytes(), (tree / item["build_source"]).read_bytes())
                        if item.get("executable"):
                            self.assertTrue(path.stat().st_mode & 0o111)
                    self.assertEqual((root / "lib/upgrade/keep.d/luci-app-openclash").read_text(), "/etc/openclash/\n")
                    self.assertIn("/etc/openclash/", (package / "root/etc/uci-defaults/luci-openclash").read_text())
                    print("Actual upstream OpenClash prepare/install verified: " + device + " " + revision)


if __name__ == "__main__":
    unittest.main()
