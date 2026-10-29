"""Build and execute the locked Rust fan plugin with the C8 adaptation."""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import urllib.request

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("c8_fan_adapt", REPO / "Scripts/C8-688/fancontrol/adapt.py")
adapt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapt)


class C8FanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lock = json.loads((REPO / "Profiles/packages.lock.json").read_text(encoding="utf-8"))["c8-fancontrol"]
        if lock["url"] != "https://github.com/LianXia233/luci-app-h5000m-fancontrol.git":
            raise AssertionError("review the C8 port when changing the fan source")
        with urllib.request.urlopen("https://codeload.github.com/LianXia233/luci-app-h5000m-fancontrol/tar.gz/" + lock["commit"], timeout=60) as response:
            cls.archive = response.read()

    def tree(self, root):
        with tarfile.open(fileobj=io.BytesIO(self.archive)) as archive:
            for member in archive.getmembers():
                relative = Path(*Path(member.name).parts[1:])
                if not member.isfile():
                    continue
                self.assertNotIn("..", relative.parts)
                file = root / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(archive.extractfile(member).read())

    def test_hash_guard_repeatability_and_frontend_render(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            self.tree(tree)
            adapt.adapt(tree)
            first = {n: (tree / n).read_bytes() for n in adapt.HASHES}
            adapt.adapt(tree)
            self.assertEqual(first, {n: (tree / n).read_bytes() for n in adapt.HASHES})
            node = shutil.which("node")
            self.assertIsNotNone(node, "node required for real fan UI rendering")
            result = subprocess.run([node, "tests/frontend-smoke.js"], cwd=tree, text=True, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            file = tree / "src/src/safety.rs"
            file.write_bytes(file.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "drift"):
                adapt.adapt(tree)

    @unittest.skipIf(os.name == "nt", "actual OpenWrt package discovery runs on Linux")
    def test_locked_openwrt_scan_does_not_discover_review_backups_as_packages(self):
        source = json.loads((REPO / "Profiles/sources.lock.json").read_text())["C8-688"]
        raw = "https://raw.githubusercontent.com/immortalwrt/immortalwrt/" + source["commit"] + "/"
        files = {}
        for name in ("include/scan.mk", "include/scan.awk"):
            with urllib.request.urlopen(raw + name, timeout=60) as response:
                files[name] = response.read()
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory)
            tree = top / "package/luci-app-h5000m-fancontrol"
            self.tree(tree)
            adapt.adapt(tree)
            for name, data in files.items():
                path = top / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            (top / "include/verbose.mk").touch()
            (top / "rules.mk").write_text('STAGING_DIR_HOST:=$(TOPDIR)/host\n')
            (top / "tmp/info").mkdir(parents=True)
            def scan(cookie):
                name = f"tmp/info/.files-packageinfo-{cookie}"
                result = subprocess.run(["make", "--no-print-directory", "-f", "include/scan.mk",
                                         f"TOPDIR={top}", f"SCAN_COOKIE={cookie}", str(top / name)],
                                        cwd=top, text=True, capture_output=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return (top / name).read_text().splitlines()
            self.assertEqual(scan("fixed"), ["luci-app-h5000m-fancontrol"])
            # Independently reproduce the full-build failure with a legacy
            # literal Makefile backup. The fixed format must avoid this entry.
            legacy = tree / ".c8-reviewed-originals/Makefile"
            legacy.write_bytes((tree / ".c8-reviewed-originals/Makefile.original").read_bytes())
            self.assertIn("luci-app-h5000m-fancontrol/.c8-reviewed-originals", scan("legacy"))

    @unittest.skipIf(os.name == "nt", "Linux Rust/sysfs regression runs in CI")
    def test_real_controller_obeys_c8_pwm_and_safety(self):
        cargo = shutil.which("cargo")
        self.assertIsNotNone(cargo, "cargo required on Linux CI")
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            self.tree(tree)
            adapt.adapt(tree)
            shutil.copyfile(Path(__file__).with_name("c8-fan-regression.rs"), tree / "src/tests/repository_c8.rs")
            result = subprocess.run([cargo, "test", "--locked", "--offline", "--test", "repository_c8"],
                                    cwd=tree / "src", text=True, capture_output=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("5 passed; 0 failed", result.stdout)
            print(result.stdout)

    @unittest.skipIf(os.name == "nt", "actual LuCI Make packaging runs on Linux")
    def test_locked_luci_install_cross_compiles_and_preserves_reviewed_frontend(self):
        # Use the real pinned package and LuCI prepare/install rules. In
        # particular, their src/* copy does not carry src/.cargo into the build.
        lock = json.loads((REPO / "Profiles/sources.lock.json").read_text())["C8-688"]["feeds"]["luci"]
        with urllib.request.urlopen(
                "https://raw.githubusercontent.com/immortalwrt/luci/" + lock["commit"] + "/luci.mk",
                timeout=60) as response:
            luci_make = response.read()
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory)
            tree = top / "package/luci-app-h5000m-fancontrol"
            self.tree(tree)
            adapt.adapt(tree)
            (top / "feeds/luci").mkdir(parents=True)
            (top / "feeds/luci/luci.mk").write_bytes(luci_make)
            (top / "include").mkdir()
            # Replace OpenWrt framework I/O only, keeping the package and
            # upstream LuCI macros responsible for preparation/installation.
            (top / "rules.mk").write_text('''
INCLUDE_DIR:=$(TOPDIR)/include
BUILD_DIR:=$(TOPDIR)/build
PKG_INSTALL_DIR:=$(TOPDIR)/installed
CP:=cp -fpR
FIND:=find
XARGS:=xargs
INSTALL_DIR:=install -d
ARCH:=aarch64
CONFIG_LUCI_JSMIN:=y
DUMP:=1
define Build/Install/Default
\t$(MAKE) -C $(PKG_BUILD_DIR)/src ARCH=$(ARCH) DESTDIR=$(PKG_INSTALL_DIR) install
endef
''')
            (top / "include/package.mk").touch()
            (top / "bin").mkdir()
            minifier = top / "bin/jsmin"
            minifier.write_text("#!/bin/sh\ncat >/dev/null\nprintf 'MINIFIED\\n'\n")
            minifier.chmod(0o755)
            (tree / "probe.mk").write_text('''
include Makefile
define Probe
.PHONY: prepare install
prepare:
\t$(Build/Prepare)
install:
\t$(call Package/$(PKG_NAME)/install,$(TOPDIR)/rootfs)
endef
$(eval $(Probe))
''')
            command = ["make", "--no-print-directory", "-f", "probe.mk", f"TOPDIR={top}"]
            env = dict(os.environ, PATH=str(top / "bin") + os.pathsep + os.environ["PATH"])
            prepared = subprocess.run(command + ["prepare"], cwd=tree, env=env,
                                      text=True, capture_output=True, timeout=60)
            self.assertEqual(prepared.returncode, 0, prepared.stdout + prepared.stderr)
            self.assertTrue((top / "build/luci-app-h5000m-fancontrol/src/Cargo.toml").is_file())
            result = subprocess.run(command + ["install"], cwd=tree, env=env,
                                    text=True, capture_output=True, timeout=180)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            binary = top / "rootfs/usr/sbin/h5000m-fancontrol"
            elf = binary.read_bytes()[:20]
            self.assertEqual(elf[:6], b"\x7fELF\x02\x01")
            self.assertEqual(int.from_bytes(elf[18:20], "little"), 183)
            self.assertTrue(binary.stat().st_mode & 0o111)
            frontend = "htdocs/luci-static/resources/view/h5000m/fancontrol.js"
            self.assertEqual((top / "rootfs/www" / Path(frontend).relative_to("htdocs")).read_bytes(),
                             (tree / frontend).read_bytes())


if __name__ == "__main__":
    unittest.main()
