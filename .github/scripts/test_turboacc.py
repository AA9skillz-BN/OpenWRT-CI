"""Run the locked Arthur package, real ucode migration and locked LuCI installer."""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import urllib.request

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("tr_turboacc", Path(__file__).with_name("tr3000-defaults.py"))
adapt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapt)
UCODE = os.environ.get("UCODE_TEST_BIN") or shutil.which("ucode")
BASH = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else None)
LEGACY = {"sw_flow": "1", "hw_flow": "1", "sfe_flow": "0", "hw_wed": "1", "bbr_cca": "1", "fullcone_nat": "1", "fullcone6": "0"}


class TurboAccTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lock = json.loads((REPO / "Profiles/packages.lock.json").read_bytes())["turboacc"]
        assert lock["url"] == "https://github.com/Arthur97172/luci-app-turboacc.git"
        assert lock["directory"] == "luci-app-turboacc" and "subdirectory" not in lock
        with urllib.request.urlopen("https://codeload.github.com/Arthur97172/luci-app-turboacc/tar.gz/" + lock["commit"], timeout=60) as response:
            cls.archive = response.read()
        # Apply the downstream patches to the actual source used by the locked
        # TR recipe, rather than testing an invented capability string.
        url = "https://raw.githubusercontent.com/openwrt/firewall4/bac1d656e09b48e542f860844fe91cbba82abae2/root/usr/share/ucode/fw4.uc"
        with urllib.request.urlopen(url, timeout=60) as response:
            cls.fw4 = response.read().decode("utf8")
        source = json.loads((REPO / "Profiles/sources.lock.json").read_bytes())["TR3000-512"]
        raw = source["url"].removesuffix(".git").replace("https://github.com/", "https://raw.githubusercontent.com/") + "/" + source["commit"] + "/package/network/config/firewall4/patches/"
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            file = tree / "root/usr/share/ucode/fw4.uc"
            file.parent.mkdir(parents=True)
            file.write_text(cls.fw4, encoding="utf8", newline="\n")
            for name in ("001-add-bridge-flowtable-support.patch", "001-sonic-fullcone.patch", "002-default-enable-fullcone.patch", "003-match-nat-leakage-address-family.patch"):
                with urllib.request.urlopen(raw + name, timeout=60) as response:
                    patch_data = response.read()
                result = subprocess.run(["git", "apply", "--allow-empty", "--include=root/usr/share/ucode/fw4.uc", "-"], cwd=tree, input=patch_data, capture_output=True)
                if result.returncode:
                    raise AssertionError(result.stderr.decode())
            cls.fw4 = file.read_text(encoding="utf8")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.top = Path(self.temp.name)
        self.package = self.top / "package/luci-app-turboacc"
        with tarfile.open(fileobj=io.BytesIO(self.archive)) as archive:
            for member in archive.getmembers():
                relative = Path(*Path(member.name).parts[1:])
                if not member.isfile():
                    continue
                self.assertNotIn("..", relative.parts)
                file = self.package / relative
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(archive.extractfile(member).read())
                file.chmod(member.mode)

    def test_adaptation_is_repeatable_and_source_drift_cannot_partially_write(self):
        original = {name: (self.package / name).read_bytes() for name in adapt.HASHES}
        adapt.configure(self.package)
        first = {name: (self.package / name).read_bytes() for name in adapt.HASHES}
        adapt.configure(self.package)
        self.assertEqual(first, {name: (self.package / name).read_bytes() for name in adapt.HASHES})
        for name in adapt.HASHES:
            self.assertNotEqual(original[name], first[name])
            file = self.package / name
            file.write_bytes(first[name] + b"\n")
            before = {n: (self.package / n).read_bytes() for n in adapt.HASHES}
            with self.assertRaisesRegex(ValueError, "drift"):
                adapt.configure(self.package)
            self.assertEqual(before, {n: (self.package / n).read_bytes() for n in adapt.HASHES})
            file.write_bytes(first[name])
        self.assertFalse(list((self.package / ".tr3000-reviewed-originals").rglob("Makefile")))

    def test_profile_gates_all_new_runtime_assets_and_forbids_retired_ones(self):
        profile = json.loads((REPO / "Profiles/TR3000-512.json").read_bytes())
        required = {item["path"]: item for item in profile["required_files"]}
        for folder, installed in (("root", "/"), ("htdocs", "/www/")):
            for file in (self.package / folder).rglob("*"):
                if file.is_file():
                    path = installed + file.relative_to(self.package / folder).as_posix()
                    self.assertEqual(required[path]["build_source"], "package/luci-app-turboacc/" + file.relative_to(self.package).as_posix())
        config = (REPO / "Config/TR3000-512.txt").read_text() + (REPO / "Config/BASE.txt").read_text()
        self.assertNotIn("luci-app-turboacc_INCLUDE_", config)
        for name in ("ucode", "ucode-mod-fs", "ucode-mod-uci", "rpcd-mod-ucode", "luci-base", "luci-i18n-turboacc-zh-cn"):
            self.assertIn(name, profile["required_packages"])
            self.assertIn("CONFIG_PACKAGE_" + name + "=y", config)
        for path in profile["forbidden_files"]:
            self.assertNotIn(path, required)
            self.assertFalse((REPO / "Files/TR3000-512" / path.lstrip("/")).exists())

    @unittest.skipIf(os.name == "nt", "upstream executable mode and POSIX shell tests run on Linux")
    def test_upstream_frontend_tests_still_pass_after_adaptation(self):
        adapt.configure(self.package)
        result = subprocess.run(["node", "tests/run.js"], cwd=self.package, text=True, capture_output=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print("Arthur TurboACC upstream frontend/package tests passed")

    @unittest.skipUnless(BASH, "shell required")
    def test_exact_legacy_cleanup_is_repeatable_and_preserves_unrelated_files(self):
        source = (REPO / "Scripts/TR3000-512/turboacc/cleanup-legacy.sh").read_text()
        paths = re.findall(r"/(?:etc|usr)/[^\s]+", "\n".join(line for line in source.splitlines() if line.startswith("rm -f ")))
        root = self.top / "rootfs"
        for name in [*paths, "/etc/config/turboacc", "/etc/config/firewall", "/etc/init.d/turboacc", "/usr/lib/lua/luci/controller/unrelated.lua"]:
            file = root / name.lstrip("/")
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("preserve me\n")
        # Only redirect absolute file targets; execute the maintained cleanup itself.
        prefix = root.as_posix()
        if os.name == "nt":
            prefix = "/" + prefix[0].lower() + prefix[2:]
        source = re.sub(r"(?<!\S)/(etc|usr)/", lambda m: "'" + prefix + "/" + m[1] + "/", source)
        source = re.sub(r"('[^'\s]+)(?=\s|$)", r"\1'", source)
        script = self.top / "cleanup.sh"
        script.write_text(source, newline="\n")
        for _ in range(2):
            result = subprocess.run([BASH, str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        for path in paths:
            self.assertFalse((root / path.lstrip("/")).exists())
        for path in ("etc/config/turboacc", "etc/config/firewall", "etc/init.d/turboacc", "usr/lib/lua/luci/controller/unrelated.lua"):
            self.assertEqual((root / path).read_text(), "preserve me\n")

    def run_core(self, settings, actions="defaults(); apply();", original=False, modules=True, stale_hw="1", sonic=False):
        if not UCODE:
            self.skipTest("real ucode runtime installed by Linux CI")
        core = (self.package / adapt.CORE).read_text()
        if not original:
            adapt.configure(self.package)
            core = (self.package / adapt.CORE).read_text()
        files = {"/proc/sys/kernel/osrelease": "6.18.55", "/usr/share/ucode/fw4.uc": self.fw4,
                 "/usr/sbin/nft": "executable provided by required nftables-json package",
                 "/proc/device-tree/compatible": "cudy,tr3000-v1\u0000mediatek,mt7981\u0000",
                 "/proc/sys/net/ipv4/tcp_available_congestion_control": "cubic reno",
                 "/proc/sys/net/ipv4/tcp_congestion_control": "cubic",
                 "/sys/module/mt7915e/parameters/wed_enable": "Y", "/var/run/fw4.state": "1"}
        if modules:
            files["/lib/modules/6.18.55/nft_fullcone.ko"] = "module"
        store = {"turboacc": {"config": settings},
                 "firewall": {"@defaults[0]": {"flow_offloading": "1", "flow_offloading_hw": stale_hw},
                              "wan": {".type": "zone", "name": "wan", "masq": "1", "network": ["wan"], "custom": "keep"}},
                 "ucitrack": {"old": {".type": "turboacc", "init": "turboacc"},
                              "custom": {".type": "turboacc", "init": "turboacc", "exec": "/keep"},
                              "network": {".type": "network", "init": "network"}}}
        harness = '''
let store = STORE, files = FILES, commands = [], writes = [], commits = [];
const fs = {
 readfile: function(p) { return files[p]; },
 access: function(p) { return files[p] != null ? true : null; },
 stat: function(p) { return null; },
 writefile: function(p,v) { files[p] = '' + v; push(writes,p); return length(''+v); },
 popen: function(p) { return null; }
};
const cursor = {
 get: function(c,s,k) { if (store[c] == null || store[c][s] == null) return null; return k == null ? store[c][s] : store[c][s][k]; },
 set: function(c,s,k,v) { store[c] ??= {}; store[c][s] ??= {}; store[c][s][k]=v; return true; },
 delete: function(c,s,k) { if (k == null) delete store[c][s]; else delete store[c][s][k]; return true; },
 foreach: function(c,t,f) { for (let s in store[c]) if (store[c][s]['.type']==t) f({ ...store[c][s], '.name':s }); },
 commit: function(c) { push(commits,c); return true; },
 load: function(c) { return true; },
 add: function(c,t) { store[c] ??= {}; store[c].new={}; return 'new'; },
 rename: function(c,s,n) { store[c][n]=store[c][s]; delete store[c][s]; }
};
const uci = { cursor: function() { return cursor; } };
function simulate_system(p) {
 push(commands,p);
 if (p == 'modprobe tcp_bbr 2>/dev/null') files['/proc/sys/net/ipv4/tcp_available_congestion_control']='cubic reno bbr';
 if (index(p, 'turboacc_probe') >= 0) return SONIC && index(p, 'masquerade fullcone') >= 0 ? 0 : 1;
 return 0;
}
'''.replace("STORE", json.dumps(store)).replace("FILES", json.dumps(files)).replace("SONIC", "true" if sonic else "false")
        core = core.replace("import * as fs from 'fs';", "").replace("import * as uci from 'uci';", "")
        core = core.replace("export { detect, apply, stop, defaults, status };", "").replace("system(", "simulate_system(")
        script = self.top / "runtime.uc"
        script.write_text(harness + core + "\n" + actions + "\nprintf('%J', {store,files,commands,writes,commits});\n", encoding="utf8", newline="\n")
        result = subprocess.run([UCODE, str(script)], text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_real_ucode_reproduces_old_config_reset_and_preserves_disabled_values(self):
        old = dict.fromkeys(LEGACY, "0")
        broken = self.run_core(old, original=True)
        self.assertEqual(broken["store"]["turboacc"]["config"]["fastpath"], "flow_offloading")
        actual = self.run_core(old)
        cfg = actual["store"]["turboacc"]["config"]
        self.assertEqual((cfg["fastpath"], cfg["fastpath_fo_hw"], cfg["tcpcca"], cfg["fullcone"]), ("disabled", "0", "cubic", "0"))
        self.assertFalse(set(LEGACY) - {"fullcone6"} & set(cfg))
        self.assertNotIn("old", actual["store"]["ucitrack"])
        self.assertEqual(actual["store"]["ucitrack"]["custom"]["exec"], "/keep")
        self.assertEqual(actual["store"]["firewall"]["wan"]["custom"], "keep")
        self.assertFalse(any("modprobe tcp_bbr" in cmd for cmd in actual["commands"]))

    def test_real_ucode_migrates_enabled_values_and_loads_bbr_before_capability_probe(self):
        actual = self.run_core(LEGACY)
        cfg = actual["store"]["turboacc"]["config"]
        self.assertEqual((cfg["fastpath"], cfg["fastpath_fo_hw"], cfg["tcpcca"], cfg["fullcone"]), ("flow_offloading", "1", "bbr", "1"))
        self.assertIn("modprobe tcp_bbr 2>/dev/null", actual["commands"])
        self.assertEqual(actual["files"]["/proc/sys/net/ipv4/tcp_congestion_control"], "bbr")
        self.assertEqual(actual["files"]["/sys/module/mt7915e/parameters/wed_enable"], "Y")

    def test_real_ucode_modern_choices_win_and_repeated_defaults_do_not_reenable(self):
        settings = dict(LEGACY, fastpath="disabled", fastpath_fo_hw="0", tcpcca="reno", fullcone="0", initialized="1")
        actual = self.run_core(settings, "defaults(); apply(); defaults(); apply();")
        cfg = actual["store"]["turboacc"]["config"]
        self.assertEqual((cfg["fastpath"], cfg["fastpath_fo_hw"], cfg["tcpcca"], cfg["fullcone"]), ("disabled", "0", "reno", "0"))
        self.assertFalse(set(LEGACY) - {"fullcone6"} & set(cfg))
        self.assertEqual(actual["commits"].count("turboacc"), 1)

    def test_real_ucode_fresh_tr_defaults_survive_upstream_defaults_and_repeated_apply(self):
        adapt.configure(self.package)
        settings = dict(re.findall(r"option ([a-z0-9_]+) '([^']*)'", (self.package / adapt.CONFIG).read_text()))
        actual = self.run_core(settings, "defaults(); apply(); defaults(); apply();", modules=False, sonic=True)
        cfg = actual["store"]["turboacc"]["config"]
        self.assertEqual((cfg["fastpath"], cfg["fastpath_fo_hw"], cfg["tcpcca"], cfg["fullcone"]), ("flow_offloading", "1", "bbr", "2"))
        self.assertEqual(actual["files"]["/sys/module/mt7915e/parameters/wed_enable"], "Y")
        self.assertEqual(actual["store"]["firewall"]["@defaults[0]"]["fullcone"], "1")

    def test_real_ucode_migrated_hardware_off_and_missing_fullcone_backend_stay_off(self):
        actual = self.run_core(dict(LEGACY, hw_flow="0", hw_wed="0"), modules=False)
        self.assertEqual(actual["store"]["firewall"]["@defaults[0]"]["flow_offloading_hw"], "0")
        self.assertEqual(actual["store"]["firewall"]["@defaults[0]"]["fullcone"], "0")
        self.assertEqual(actual["files"]["/sys/module/mt7915e/parameters/wed_enable"], "N")

    def test_real_ucode_sonic_mode_two_maps_to_the_actual_boolean_firewall_option(self):
        actual = self.run_core(LEGACY, modules=False, sonic=True)
        self.assertEqual(actual["store"]["turboacc"]["config"]["fullcone"], "2")
        self.assertEqual(actual["store"]["firewall"]["@defaults[0]"]["fullcone"], "1")

    @unittest.skipIf(os.name == "nt", "actual locked LuCI Make install runs on Linux")
    def test_real_locked_luci_prepare_and_install_carry_new_assets_without_old_paths(self):
        adapt.configure(self.package)
        lock = json.loads((REPO / "Profiles/sources.lock.json").read_bytes())["TR3000-512"]["feeds"]["luci"]
        raw = lock["url"].removesuffix(".git").replace("https://github.com/", "https://raw.githubusercontent.com/") + "/" + lock["commit"] + "/luci.mk"
        with urllib.request.urlopen(raw, timeout=60) as response:
            luci = response.read()
        (self.top / "feeds/luci").mkdir(parents=True)
        (self.top / "feeds/luci/luci.mk").write_bytes(luci)
        (self.top / "include").mkdir()
        (self.top / "include/package.mk").touch()
        (self.top / "rules.mk").write_text('''
INCLUDE_DIR:=$(TOPDIR)/include
BUILD_DIR:=$(TOPDIR)/build
CP:=cp -fpR
INSTALL_DIR:=install -d
PKG_PO_VERSION:=test
PKG_SRC_VERSION:=test
PKG_GITBRANCH:=test
''')
        (self.package / "probe.mk").write_text('''
include Makefile
define BuildPackage
endef
.PHONY: prepare install
define Probe
prepare:
\t$(Build/Prepare)
install:
\t$(call Package/luci-app-turboacc/install,$(TOPDIR)/rootfs)
endef
$(eval $(Probe))
''')
        result = subprocess.run(["make", "--no-print-directory", "-f", "probe.mk", f"TOPDIR={self.top}", "prepare", "install"], cwd=self.package, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        root = self.top / "rootfs"
        profile = json.loads((REPO / "Profiles/TR3000-512.json").read_bytes())
        for item in profile["required_files"]:
            prefix = "package/luci-app-turboacc/"
            if item.get("build_source", "").startswith(prefix):
                self.assertEqual((root / item["path"].lstrip("/")).read_bytes(), (self.package / item["build_source"].removeprefix(prefix)).read_bytes())
        for path in profile["forbidden_files"]:
            self.assertFalse((root / path.lstrip("/")).exists())
        print("Arthur TurboACC actual locked LuCI install: exact new runtime assets, no retired paths")


if __name__ == "__main__":
    unittest.main()
