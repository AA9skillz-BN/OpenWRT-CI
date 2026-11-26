"""Exercise the anchor-based latest fan hook (v7.0.5+) and behavioral UI gate."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import urllib.request

REPO = Path(__file__).resolve().parents[2]
LATEST_COMMIT = "27e9c67f85fde1850db6e265d1b4273786eb075a"

spec = importlib.util.spec_from_file_location(
    "fan_ui_fix_latest", REPO / "Scripts/AP3000M/fancontrol/fix-confirm-latest.py")
fix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fix)

locked_spec = importlib.util.spec_from_file_location(
    "fan_ui_fix_locked", REPO / "Scripts/AP3000M/fancontrol/fix-confirm.py")
locked_fix = importlib.util.module_from_spec(locked_spec)
locked_spec.loader.exec_module(locked_fix)


class FanUiLatestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = ("https://codeload.github.com/LianXia233/luci-app-airpi3000m-fancontrol/tar.gz/" +
               LATEST_COMMIT)
        with urllib.request.urlopen(url, timeout=60) as response:
            cls.archive = response.read()
        files = {}
        with tarfile.open(fileobj=io.BytesIO(cls.archive)) as archive:
            for member in archive.getmembers():
                if member.isfile():
                    rel = "/".join(Path(member.name).parts[1:])
                    files[rel] = archive.extractfile(member).read()
        cls.original = files[fix.VIEW]
        cls.menu = files[fix.MENU]
        cls.recipe = files[fix.RECIPE]
        cls.config = files[fix.CONFIG]

    def package_fixture(self, root):
        for name, data in ((fix.VIEW, self.original), (fix.MENU, self.menu),
                           (fix.RECIPE, self.recipe), (fix.CONFIG, self.config)):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def test_menu_selects_distinct_latest_url_and_tolerates_benign_version_bumps(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.package_fixture(root)
            # Simulate a benign upstream PKG_VERSION bump and comment update.
            bumped_recipe = self.recipe.replace(b"PKG_VERSION:=7.0.5", b"PKG_VERSION:=7.0.6\n# harmless comment")
            (root / fix.RECIPE).write_bytes(bumped_recipe)
            subprocess.run([sys.executable, str(spec.origin), str(root)],
                           capture_output=True, check=True, timeout=30)
            menu = json.loads((root / fix.MENU).read_bytes())
            view_name = menu["admin/status/airpi-fancontrol"]["action"]["path"]
            self.assertTrue(view_name.startswith("airpi-fancontrol/fancontrol-"))
            self.assertNotEqual(view_name, "airpi-fancontrol/fancontrol-10b9af7ae581")
            alias = (root / fix.VIEW).parent.parent / (view_name + ".js")
            self.assertEqual(alias.read_bytes(), fix.patch_view(self.original))
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(fix.patch_package(root), view_name)
            self.assertEqual(before, {p: p.read_bytes() for p in root.rglob("*") if p.is_file()})
            profile = json.loads((REPO / "Profiles/AP3000M.json").read_bytes())
            required = {p["path"]: p for p in profile["required_files"]}
            menu_gate = required["/usr/share/luci/menu.d/luci-app-airpi-fancontrol.json"]
            self.assertEqual(
                menu_gate["luci_view_build_root"] + "/" + view_name + ".js",
                "package/luci-app-airpi3000m-fancontrol/" + alias.relative_to(root).as_posix(),
            )
            cfg_text = (root / fix.CONFIG).read_text(encoding="utf-8")
            for needle in ("option temp_high '75'", "option temp_mid '68'", "option temp_low '48'",
                           "option hysteresis '4'", "option min_hold '60'", "option log_level '1'"):
                self.assertIn(needle, cfg_text)

    def test_locked_hook_rejects_latest_and_latest_hook_rejects_broken_semantics(self):
        with self.assertRaisesRegex(ValueError, "reviewed locked source"):
            locked_fix.patch_view(self.original)
        patched = fix.patch_view(self.original)
        self.assertEqual(fix.patch_view(patched), patched)
        # Breaking the exit-code check must fail the contract / behavioral gate.
        with self.assertRaises(ValueError):
            fix.patch_view(patched.replace(b"res.code !== 0", b"false"))
        # Breaking command() arguments must be caught by the built-in Node UI check.
        if shutil.which("node"):
            with self.assertRaisesRegex(ValueError, "behavioral UI check"):
                fix.patch_view(patched.replace(b"var CTL = '/usr/bin/airpi-fanctl.sh';",
                                               b"var CTL = '/usr/bin/wrong-ctl.sh';"))

    def test_structural_anchor_drift_fails_before_any_files_are_written(self):
        digest = hashlib.sha256(fix.patch_view(self.original)).hexdigest()[:12]
        for bad in ("menu_json", "partial_menu", "alias", "recipe_anchor", "patched_recipe", "config_anchor"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.package_fixture(root)
                if bad == "menu_json":
                    (root / fix.MENU).write_bytes(b"{}")
                elif bad == "partial_menu":
                    (root / fix.MENU).write_bytes(self.menu.replace(
                        b'airpi-fancontrol/fancontrol"', b'airpi-fancontrol/fancontrol-stale"'))
                elif bad == "alias":
                    (root / fix.VIEW).with_name(f"fancontrol-{digest}.js").write_bytes(b"old view")
                elif bad == "recipe_anchor":
                    (root / fix.RECIPE).write_bytes(self.recipe.replace(b"fancontrol.js", b"other.js"))
                elif bad == "config_anchor":
                    (root / fix.CONFIG).write_bytes(self.config.replace(b"option temp_mid", b"# removed"))
                else:
                    fix.patch_package(root)
                    recipe = root / fix.RECIPE
                    recipe.write_bytes(recipe.read_bytes().replace(b"$(INSTALL_DATA)", b"false"))
                before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
                with self.assertRaises(ValueError):
                    fix.patch_package(root)
                self.assertEqual(before, {p: p.read_bytes() for p in root.rglob("*") if p.is_file()})

    def test_unpatched_latest_accepts_malformed_rc_reply_and_patched_rejects_it(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required for fan UI regression")
        runner = str(Path(__file__).with_name("fancontrol-ui-test.js"))
        with tempfile.TemporaryDirectory() as temp:
            unpatched = Path(temp) / "unpatched.js"
            unpatched.write_bytes(self.original)
            bad = subprocess.run([node, runner, str(unpatched)],
                                 capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertNotEqual(bad.returncode, 0, "unpatched 7.0.5 treats {rc:0} without code as success")
            repaired = Path(temp) / "repaired.js"
            repaired.write_bytes(fix.patch_view(self.original))
            good = subprocess.run([node, runner, str(repaired)],
                                  capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
            self.assertIn("fan UI confirmation/command regressions passed", good.stdout)

    def test_obsolete_ui_api_repairs_are_not_reintroduced_in_latest_mode(self):
        repaired = fix.patch_view(self.original)
        # Upstream owns confirmation and save behavior. Regressing to the old
        # API must fail validation rather than silently injecting our old fix.
        for candidate in (repaired.replace(b"confirmAction(", b"ui.confirm("),
                          repaired.replace(b"res.code !== 0", b"res.rc !== 0")):
            with self.subTest(candidate=candidate[:30]), self.assertRaises(ValueError):
                fix.patch_view(candidate)
        self.assertNotIn("LEGACY_HELPER", Path(spec.origin).read_text(encoding="utf-8"))

    @unittest.skipIf(os.name == "nt", "actual package install regression runs on Linux")
    def test_actual_latest_install_packages_the_view_selected_by_menu(self):
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory)
            root = top / "package/luci-app-airpi3000m-fancontrol"
            with tarfile.open(fileobj=io.BytesIO(self.archive)) as archive:
                for member in archive.getmembers():
                    if not member.isfile():
                        continue
                    relative = Path(*Path(member.name).parts[1:])
                    self.assertNotIn("..", relative.parts)
                    file = root / relative
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_bytes(archive.extractfile(member).read())
            view_name = fix.patch_package(root)
            (top / "include").mkdir()
            (top / "include/package.mk").touch()
            (top / "rules.mk").write_text('''
INCLUDE_DIR:=$(TOPDIR)/include
PKG_INSTALL_DIR:=$(TOPDIR)/installed
INSTALL_DIR:=install -d
INSTALL_BIN:=install -m0755
INSTALL_DATA:=install -m0644
INSTALL_CONF:=install -m0600
AIRPI_PREBUILT:=1
AIRPI_PREBUILT_BIN:=/bin/true
''')
            package = (root / fix.RECIPE).parent
            (package / "probe.mk").write_text('''
include Makefile
.PHONY: compile install
compile:
\t$(Build/Compile)
install:
\t$(call Package/$(PKG_NAME)/install,$(TOPDIR)/rootfs)
''')
            command = ["make", "--no-print-directory", "-f", "probe.mk", f"TOPDIR={top}", "compile", "install"]
            after = subprocess.run(command, cwd=package, text=True, capture_output=True, timeout=30)
            self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
            installed = top / "rootfs"
            alias = installed / ("www/luci-static/resources/view/" + view_name + ".js")
            menu = installed / "usr/share/luci/menu.d/luci-app-airpi-fancontrol.json"
            selected = json.loads(menu.read_bytes())["admin/status/airpi-fancontrol"]["action"]["path"]
            self.assertEqual(selected, view_name)
            self.assertEqual(alias.read_bytes(), fix.patch_view(self.original))
            self.assertEqual(menu.read_bytes(), (root / fix.MENU).read_bytes())
            self.assertEqual(alias.stat().st_mode & 0o777, 0o644)
            self.assertEqual(alias.with_name("fancontrol.js").read_bytes(), alias.read_bytes())


if __name__ == "__main__":
    unittest.main()
