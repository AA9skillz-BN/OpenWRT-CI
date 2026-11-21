"""AP-specific migration, old-file cleanup and real locked LuCI installation."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest
import urllib.request

import test_turboacc as package_tests

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ap_turboacc", Path(__file__).with_name("ap3000m-turboacc.py"))
adapt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapt)


class ApTurboAccTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lock = json.loads((REPO / "Profiles/packages.lock.json").read_bytes())["turboacc"]
        with urllib.request.urlopen("https://codeload.github.com/Arthur97172/luci-app-turboacc/tar.gz/" + lock["commit"], timeout=60) as response:
            cls.archive = response.read()
        source = json.loads((REPO / "Profiles/sources.lock.json").read_bytes())["AP3000M"]
        raw = source["url"].removesuffix(".git").replace("https://github.com/", "https://raw.githubusercontent.com/") + "/" + source["commit"] + "/package/network/config/firewall4/"
        with urllib.request.urlopen(raw + "Makefile", timeout=60) as response:
            recipe = response.read().decode("utf8")
        revision = re.search(r"^PKG_SOURCE_VERSION:=(\w+)$", recipe, re.M)[1]
        with urllib.request.urlopen("https://raw.githubusercontent.com/openwrt/firewall4/" + revision + "/root/usr/share/ucode/fw4.uc", timeout=60) as response:
            original = response.read()
        with urllib.request.urlopen(raw + "patches/001-firewall4-add-support-for-fullcone-nat.patch", timeout=60) as response:
            patch = response.read()
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            file = tree / "root/usr/share/ucode/fw4.uc"
            file.parent.mkdir(parents=True)
            file.write_bytes(original)
            result = subprocess.run(["git", "apply", "--include=root/usr/share/ucode/fw4.uc", "-"], cwd=tree, input=patch, capture_output=True)
            if result.returncode:
                raise AssertionError(result.stderr.decode())
            cls.fw4 = file.read_text(encoding="utf8")

    setUp = package_tests.TurboAccTests.setUp

    def runtime(self, settings, actions="defaults(); apply();", firewall=None, wed="Y"):
        adapt.configure(self.package)
        setup = "files['/proc/device-tree/compatible'] = 'airpi,ap3000m\\u0000mediatek,mt7981\\u0000';\n"
        setup += "files['/sys/module/mt7915e/parameters/wed_enable'] = " + json.dumps(wed) + ";\n"
        setup += "store.firewall['@defaults[0]'] = " + json.dumps(firewall or {"flow_offloading": "1", "flow_offloading_hw": "1", "fullcone": "1", "fullcone6": "0"}) + ";\n"
        return package_tests.TurboAccTests.run_core(self, settings, setup + actions, original=True)

    def template(self):
        adapt.configure(self.package)
        return dict(re.findall(r"option ([a-z0-9_]+) '([^']*)'", (self.package / adapt.CONFIG).read_text(encoding="utf8")))

    def test_adaptation_is_repeatable_and_rejects_source_drift_before_writing(self):
        adapt.configure(self.package)
        first = {name: (self.package / name).read_bytes() for name in adapt.HASHES}
        adapt.configure(self.package)
        self.assertEqual(first, {name: (self.package / name).read_bytes() for name in adapt.HASHES})
        file = self.package / adapt.CORE
        file.write_bytes(first[adapt.CORE] + b"\n")
        before = {name: (self.package / name).read_bytes() for name in adapt.HASHES}
        with self.assertRaisesRegex(ValueError, "drift"):
            adapt.configure(self.package)
        self.assertEqual(before, {name: (self.package / name).read_bytes() for name in adapt.HASHES})
        self.assertFalse(list((self.package / ".ap3000m-reviewed-originals").rglob("Makefile")))

    def test_profile_requires_plugin_dependencies_and_exact_new_assets(self):
        profile = json.loads((REPO / "Profiles/AP3000M.json").read_bytes())
        self.assertIn("turboacc", profile["packages"])
        required = {item["path"]: item for item in profile["required_files"]}
        for folder, installed in (("root", "/"), ("htdocs", "/www/")):
            for file in (self.package / folder).rglob("*"):
                if file.is_file():
                    path = installed + file.relative_to(self.package / folder).as_posix()
                    self.assertEqual(required[path]["build_source"], "package/luci-app-turboacc/" + file.relative_to(self.package).as_posix())
        config = (REPO / "Config/AP3000M.txt").read_text(encoding="utf8")
        for name in ("luci-app-turboacc", "luci-i18n-turboacc-zh-cn", "firewall4", "nftables-json", "ucode", "ucode-mod-fs", "ucode-mod-uci", "rpcd-mod-ucode", "ucitrack"):
            self.assertIn(name, profile["required_packages"])
            self.assertIn("CONFIG_PACKAGE_" + name + "=y", config)
        hook = (REPO / "Scripts/AP3000M/private.sh").read_text(encoding="utf8")
        self.assertIn("ap3000m-turboacc.py", hook)
        self.assertNotIn("99-ap3000m-flowoffload", hook)
        self.assertFalse((REPO / "Scripts/AP3000M/net-tune/99-ap3000m-flowoffload").exists())
        tune = (REPO / "Scripts/AP3000M/net-tune/99-ap3000m-tune.conf").read_text(encoding="utf8")
        self.assertNotRegex(tune, r"(?m)^\s*net\.ipv4\.tcp_congestion_control\s*=")
        self.assertIn("net.core.rmem_max=8388608", tune)

    def test_real_ucode_imports_existing_firewall_and_actual_wed_once(self):
        actual = self.runtime(self.template(), "defaults(); apply(); defaults(); apply();")
        cfg = actual["store"]["turboacc"]["config"]
        self.assertEqual((cfg["fastpath"], cfg["fastpath_fo_hw"], cfg["fullcone"], cfg["tcpcca"]), ("flow_offloading", "1", "1", "bbr"))
        self.assertEqual(cfg["fastpath_mh_eth_hnat_wed"], "1")
        self.assertNotIn("ap3000m_import_firewall", cfg)
        self.assertEqual(actual["files"]["/proc/sys/net/ipv4/tcp_congestion_control"], "bbr")
        self.assertEqual(actual["commits"].count("turboacc"), 1)
        self.assertEqual(actual["store"]["firewall"]["wan"]["custom"], "keep")
        self.assertNotIn("old", actual["store"]["ucitrack"])
        self.assertEqual(actual["store"]["ucitrack"]["custom"]["exec"], "/keep")

    def test_real_ucode_import_keeps_existing_offload_disabled_and_no_wed(self):
        actual = self.runtime(self.template(), "defaults(); apply(); defaults(); apply();",
                              {"flow_offloading": "0", "flow_offloading_hw": "0", "fullcone": "0", "tcpcca": "reno"}, wed="N")
        cfg = actual["store"]["turboacc"]["config"]
        self.assertEqual((cfg["fastpath"], cfg["fastpath_fo_hw"], cfg["fullcone"], cfg["fastpath_mh_eth_hnat_wed"]), ("disabled", "0", "0", "0"))
        self.assertEqual(actual["store"]["firewall"]["@defaults[0]"]["flow_offloading"], "0")
        self.assertEqual(cfg["tcpcca"], "reno")
        self.assertFalse(any("modprobe tcp_bbr" in cmd for cmd in actual["commands"]))

    def test_real_ucode_retained_modern_choices_override_stale_template_marker(self):
        settings = dict(self.template(), initialized="1", fastpath="disabled", fastpath_fo_hw="0", fullcone="0", tcpcca="reno")
        actual = self.runtime(settings, "defaults(); apply(); defaults(); apply();")
        cfg = actual["store"]["turboacc"]["config"]
        self.assertEqual((cfg["fastpath"], cfg["fastpath_fo_hw"], cfg["fullcone"], cfg["tcpcca"]), ("disabled", "0", "0", "reno"))
        self.assertNotIn("ap3000m_import_firewall", cfg)
        self.assertFalse(any("modprobe tcp_bbr" in cmd for cmd in actual["commands"]))

    def test_real_ucode_legacy_off_switches_survive_migration(self):
        settings = dict.fromkeys(package_tests.LEGACY, "0")
        actual = self.runtime(settings)
        cfg = actual["store"]["turboacc"]["config"]
        self.assertEqual((cfg["fastpath"], cfg["fastpath_fo_hw"], cfg["fullcone"], cfg["tcpcca"]), ("disabled", "0", "0", "cubic"))
        self.assertEqual(actual["files"]["/sys/module/mt7915e/parameters/wed_enable"], "N")
        self.assertFalse(set(package_tests.LEGACY) - {"fullcone6"} & set(cfg))

    @unittest.skipUnless(package_tests.BASH, "shell required")
    def test_cleanup_removes_only_retired_paths_and_legacy_bbr_override(self):
        root = self.top / "rootfs"
        retired = ["/etc/uci-defaults/luci-turboacc", "/etc/uci-defaults/99-ap3000m-flowoffload",
                   "/usr/lib/lua/luci/controller/turboacc.lua", "/usr/lib/lua/luci/model/cbi/turboacc.lua",
                   "/usr/lib/lua/luci/view/turboacc/turboacc_status.htm", "/tmp/luci-indexcache.hash", "/tmp/luci-modulecache/old"]
        preserved = ["/etc/config/firewall", "/etc/config/turboacc", "/etc/init.d/turboacc", "/usr/lib/lua/luci/controller/other.lua"]
        for path in retired + preserved:
            file = root / path.lstrip("/")
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("keep\n", encoding="utf8")
        tune = root / "etc/sysctl.d/99-ap3000m-tune.conf"
        tune.parent.mkdir(parents=True)
        tune.write_text("net.ipv4.tcp_congestion_control=bbr\nnet.ipv4.tcp_congestion_control=cubic\nnet.core.rmem_max=8388608\n", encoding="utf8")
        prefix = root.as_posix()
        if os.name == "nt":
            prefix = "/" + prefix[0].lower() + prefix[2:]
        source = (REPO / "Scripts/AP3000M/turboacc/cleanup-legacy.sh").read_text(encoding="utf8")
        source = re.sub(r"(?<![\w/])/(etc|usr|tmp)/", lambda m: shlex.quote(prefix) + "/" + m[1] + "/", source)
        script = self.top / "cleanup.sh"
        script.write_text(source, encoding="utf8", newline="\n")
        for _ in range(2):
            result = subprocess.run([package_tests.BASH, str(script)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        for path in retired:
            self.assertFalse((root / path.lstrip("/")).exists())
        for path in preserved:
            self.assertEqual((root / path.lstrip("/")).read_text(encoding="utf8"), "keep\n")
        self.assertEqual(tune.read_text(encoding="utf8"), "net.ipv4.tcp_congestion_control=cubic\nnet.core.rmem_max=8388608\n")

    @unittest.skipIf(os.name == "nt", "actual locked LuCI Make install runs on Linux")
    def test_actual_ap_locked_luci_installs_every_required_new_runtime_file(self):
        adapt.configure(self.package)
        lock = json.loads((REPO / "Profiles/sources.lock.json").read_bytes())["AP3000M"]["feeds"]["luci"]
        raw = lock["url"].removesuffix(".git").replace("https://github.com/", "https://raw.githubusercontent.com/") + "/" + lock["commit"] + "/luci.mk"
        with urllib.request.urlopen(raw, timeout=60) as response:
            luci = response.read()
        (self.top / "feeds/luci").mkdir(parents=True)
        (self.top / "feeds/luci/luci.mk").write_bytes(luci)
        (self.top / "include").mkdir()
        (self.top / "include/package.mk").touch()
        (self.top / "rules.mk").write_text("INCLUDE_DIR:=$(TOPDIR)/include\nBUILD_DIR:=$(TOPDIR)/build\nCP:=cp -fpR\nINSTALL_DIR:=install -d\nPKG_PO_VERSION:=test\nPKG_SRC_VERSION:=test\nPKG_GITBRANCH:=test\n", encoding="utf8")
        (self.package / "probe.mk").write_text("include Makefile\ndefine BuildPackage\nendef\n.PHONY: prepare install\ndefine Probe\nprepare:\n\t$(Build/Prepare)\ninstall:\n\t$(call Package/luci-app-turboacc/install,$(TOPDIR)/rootfs)\nendef\n$(eval $(Probe))\n", encoding="utf8")
        result = subprocess.run(["make", "--no-print-directory", "-f", "probe.mk", f"TOPDIR={self.top}", "prepare", "install"], cwd=self.package, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        profile = json.loads((REPO / "Profiles/AP3000M.json").read_bytes())
        root = self.top / "rootfs"
        for item in profile["required_files"]:
            prefix = "package/luci-app-turboacc/"
            if item.get("build_source", "").startswith(prefix):
                self.assertEqual((root / item["path"].lstrip("/")).read_bytes(), (self.package / item["build_source"].removeprefix(prefix)).read_bytes())
        for path in profile["forbidden_files"]:
            self.assertFalse((root / path.lstrip("/")).exists())
        print("AP Arthur TurboACC: actual locked LuCI prepare/install verified")


if __name__ == "__main__":
    unittest.main()
