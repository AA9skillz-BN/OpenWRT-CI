"""Check the real LuCI package which installs the ucitrack service."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import urllib.request

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("turboacc_provider", Path(__file__).with_name("tr3000-defaults.py"))
adapt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapt)
# Exact LuCI snapshot shared by the two failed latest-mode builds.
FAILED_LUCI = "8724b54de3a760294aedeb10a29ffba7e44cca2f"
SERVICE = "modules/luci-base/root/etc/init.d/ucitrack"


class UcitrackProviderTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "actual upstream LuCI Make installation runs on Linux")
    def test_locked_and_failed_latest_luci_base_install_the_required_service(self):
        sources = json.loads((REPO / "Profiles/sources.lock.json").read_bytes())
        plugin = json.loads((REPO / "Profiles/packages.lock.json").read_bytes())["turboacc"]
        originals = {}
        for name in adapt.HASHES:
            with urllib.request.urlopen("https://raw.githubusercontent.com/Arthur97172/luci-app-turboacc/" + plugin["commit"] + "/" + name, timeout=60) as response:
                originals[name] = response.read()
        # The actual adapted recipe must require the owning package;
        # checking service presence alone missed this dependency error.
        adapted = adapt.patched(originals)["Makefile"].decode()
        self.assertIn("+luci-base", adapted)
        self.assertNotIn("+ucitrack", adapted)
        revisions = {FAILED_LUCI, *(sources[device]["feeds"]["luci"]["commit"] for device in ("TR3000-512", "AP3000M"))}
        for revision in sorted(revisions):
            with self.subTest(luci=revision), tempfile.TemporaryDirectory() as directory:
                top = Path(directory)
                feed = top / "feeds/luci"
                for name in ("luci.mk", "LICENSE", "NOTICE", "modules/luci-base/Makefile", SERVICE):
                    with urllib.request.urlopen("https://raw.githubusercontent.com/immortalwrt/luci/" + revision + "/" + name, timeout=60) as response:
                        data = response.read()
                    file = feed / name
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_bytes(data)
                service = feed / SERVICE
                service.chmod(0o755)
                self.assertIn("START=80", service.read_text())
                self.assertIn("/usr/share/ucitrack/*.json", service.read_text())
                (top / "include").mkdir()
                for name in ("package.mk", "host-build.mk"):
                    (top / "include" / name).touch()
                (top / "rules.mk").write_text("""
INCLUDE_DIR:=$(TOPDIR)/include
BUILD_DIR:=$(TOPDIR)/build
BUILD_DIR_HOST:=$(TOPDIR)/host-build
CP:=cp -fpR
INSTALL_DIR:=install -d
DUMP:=1
""")
                package = feed / "modules/luci-base"
                (package / "probe.mk").write_text("""
include Makefile
define Probe
.PHONY: metadata prepare install
metadata:
\t@echo provider=$(PKG_NAME)
prepare:
\t$(Build/Prepare)
install:
\t$(call Package/luci-base/install,$(TOPDIR)/rootfs)
endef
$(eval $(Probe))
""")
                result = subprocess.run(["make", "--no-print-directory", "-f", "probe.mk", f"TOPDIR={top}", "metadata", "prepare", "install"],
                                        cwd=package, text=True, capture_output=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("provider=luci-base", result.stdout)
                installed = top / "rootfs/etc/init.d/ucitrack"
                self.assertEqual(installed.read_bytes(), service.read_bytes())
                self.assertTrue(installed.stat().st_mode & 0o111)
                for device in ("TR3000-512", "AP3000M"):
                    profile = json.loads((REPO / f"Profiles/{device}.json").read_bytes())
                    self.assertIn("luci-base", profile["required_packages"])
                    self.assertNotIn("ucitrack", profile["required_packages"])
                    required = {item["path"]: item for item in profile["required_files"]}
                    self.assertEqual(required["/etc/init.d/ucitrack"]["build_source"], "feeds/luci/" + SERVICE)
                    self.assertTrue(required["/etc/init.d/ucitrack"]["executable"])
                    self.assertTrue(required["/etc/rc.d/S80ucitrack"]["executable"])
                    self.assertNotIn("CONFIG_PACKAGE_ucitrack=", (REPO / f"Config/{device}.txt").read_text())
        print("Locked and failed-latest LuCI: luci-base installs the exact executable ucitrack service")


if __name__ == "__main__":
    unittest.main()
