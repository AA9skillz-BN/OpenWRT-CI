"""Exercise the locked fan view with the LuCI APIs present on the router."""
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
spec = importlib.util.spec_from_file_location(
    "fan_ui_fix", REPO / "Scripts/AP3000M/fancontrol/fix-confirm.py")
fix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fix)


class FanUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = ("https://raw.githubusercontent.com/LianXia233/luci-app-airpi3000m-fancontrol/"
               "f931c4681f7c401bd4702351fd4a3ba821105340/" + fix.VIEW)
        with urllib.request.urlopen(url, timeout=60) as response:
            cls.original = response.read()
        if hashlib.sha256(cls.original).hexdigest() != fix.UPSTREAM_SHA256:
            raise AssertionError("locked fan source hash mismatch")
        url = ("https://raw.githubusercontent.com/LianXia233/luci-app-airpi3000m-fancontrol/"
               "f931c4681f7c401bd4702351fd4a3ba821105340/" + fix.MENU)
        with urllib.request.urlopen(url, timeout=60) as response:
            cls.menu = response.read()
        if hashlib.sha256(cls.menu).hexdigest() != fix.MENU_SHA256:
            raise AssertionError("locked fan menu hash mismatch")
        url = ("https://raw.githubusercontent.com/LianXia233/luci-app-airpi3000m-fancontrol/"
               "f931c4681f7c401bd4702351fd4a3ba821105340/" + fix.RECIPE)
        with urllib.request.urlopen(url, timeout=60) as response:
            cls.recipe = response.read()
        if hashlib.sha256(cls.recipe).hexdigest() != fix.RECIPE_SHA256:
            raise AssertionError("locked fan recipe hash mismatch")

    def package_fixture(self, root):
        for name, data in ((fix.VIEW, self.original), (fix.MENU, self.menu), (fix.RECIPE, self.recipe)):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def test_menu_selects_new_url_and_image_gates_cover_exact_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.package_fixture(root)
            # Exercise the same executable hook used by private.sh.
            subprocess.run([sys.executable, str(spec.origin), str(root)],
                           capture_output=True, check=True, timeout=30)
            menu = json.loads((root / fix.MENU).read_bytes())
            view_name = menu["admin/status/airpi-fancontrol"]["action"]["path"]
            self.assertEqual(view_name, "airpi-fancontrol/fancontrol-10b9af7ae581")
            alias = (root / fix.VIEW).parent.parent / (view_name + ".js")
            self.assertEqual(alias.read_bytes(), fix.patch_view(self.original))
            before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
            self.assertEqual(fix.patch_package(root), view_name)
            self.assertEqual(before, {p: p.read_bytes() for p in root.rglob("*") if p.is_file()})
            profile = json.loads((REPO / "Profiles/AP3000M.json").read_bytes())
            required = {p["path"]: p for p in profile["required_files"]}
            paths = {
                "/www/luci-static/resources/view/" + view_name + ".js": alias,
                "/usr/share/luci/menu.d/luci-app-airpi-fancontrol.json": root / fix.MENU,
            }
            for installed, source in paths.items():
                expected = "package/luci-app-airpi3000m-fancontrol/" + source.relative_to(root).as_posix()
                self.assertEqual(required[installed]["build_source"], expected)

    def test_menu_or_alias_drift_fails_before_any_files_are_written(self):
        for bad in ("menu", "partial_menu", "alias", "recipe", "patched_recipe"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.package_fixture(root)
                if bad == "menu":
                    (root / fix.MENU).write_bytes(self.menu + b"\n")
                elif bad == "partial_menu":
                    (root / fix.MENU).write_bytes(self.menu.replace(
                        b'airpi-fancontrol/fancontrol"', b'airpi-fancontrol/fancontrol-stale"'))
                elif bad == "alias":
                    (root / fix.VIEW).with_name("fancontrol-10b9af7ae581.js").write_bytes(b"old view")
                elif bad == "recipe":
                    (root / fix.RECIPE).write_bytes(self.recipe + b"\n")
                else:
                    fix.patch_package(root)
                    recipe = root / fix.RECIPE
                    recipe.write_bytes(recipe.read_bytes().replace(b"$(INSTALL_DATA)", b"false"))
                before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
                with self.assertRaises(ValueError):
                    fix.patch_package(root)
                self.assertEqual(before, {p: p.read_bytes() for p in root.rglob("*") if p.is_file()})

    @unittest.skipIf(os.name == "nt", "actual package install regression runs on Linux")
    def test_actual_locked_install_packages_the_view_selected_by_menu(self):
        lock = json.loads((REPO / "Profiles/packages.lock.json").read_bytes())["fancontrol"]
        self.assertEqual(lock["url"], "https://github.com/LianXia233/luci-app-airpi3000m-fancontrol.git")
        with urllib.request.urlopen("https://codeload.github.com/LianXia233/luci-app-airpi3000m-fancontrol/tar.gz/" +
                                    lock["commit"], timeout=60) as response:
            source = response.read()
        self.check_actual_install(source, fix.RECIPE_SHA256)

    @unittest.skipIf(os.name == "nt", "actual package install regression runs on Linux")
    def test_actual_reviewed_latest_install_packages_the_view_selected_by_menu(self):
        # Exact plugin snapshot from the failed latest-mode build. Fetching a
        # moving branch here would hide later upstream drift from the review.
        with urllib.request.urlopen("https://codeload.github.com/LianXia233/luci-app-airpi3000m-fancontrol/"
                                    "tar.gz/7f12c388a1185d0af95603108f4fa18792211335", timeout=60) as response:
            source = response.read()
        self.check_actual_install(source, "2b81809c45d7e825e69a83064ca3a6a3f1fb0efbb0e7a09ffdb7c57042a49ffc")

    def check_actual_install(self, source, expected_recipe_hash):
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory)
            root = top / "package/luci-app-airpi3000m-fancontrol"
            with tarfile.open(fileobj=io.BytesIO(source)) as archive:
                for member in archive.getmembers():
                    if not member.isfile():
                        continue
                    relative = Path(*Path(member.name).parts[1:])
                    self.assertNotIn("..", relative.parts)
                    file = root / relative
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_bytes(archive.extractfile(member).read())
            recipe = (root / fix.RECIPE).read_bytes()
            self.assertEqual(hashlib.sha256(recipe).hexdigest(), expected_recipe_hash)
            # Reproduce the earlier hook: it generated view/menu files but left
            # the real upstream install macro selecting only fancontrol.js.
            view_name = fix.patch_package(root)
            (root / fix.RECIPE).write_bytes(recipe)
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
            # Exercise the upstream prebuilt branch to isolate frontend install
            # behavior; this test does not claim to compile the fan controller.
            command = ["make", "--no-print-directory", "-f", "probe.mk", f"TOPDIR={top}", "compile", "install"]
            before = subprocess.run(command, cwd=package, text=True, capture_output=True, timeout=30)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            installed = top / "rootfs"
            alias = installed / ("www/luci-static/resources/view/" + view_name + ".js")
            self.assertFalse(alias.exists(), "original install must reproduce the missing view")
            fix.patch_package(root)
            after = subprocess.run(command, cwd=package, text=True, capture_output=True, timeout=30)
            self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
            menu = installed / "usr/share/luci/menu.d/luci-app-airpi-fancontrol.json"
            selected = json.loads(menu.read_bytes())["admin/status/airpi-fancontrol"]["action"]["path"]
            self.assertEqual(selected, view_name)
            self.assertEqual(alias.read_bytes(), fix.patch_view(self.original))
            self.assertEqual(menu.read_bytes(), (root / fix.MENU).read_bytes())
            self.assertEqual(alias.stat().st_mode & 0o777, 0o644)
            legacy = alias.with_name("fancontrol.js")
            self.assertEqual(legacy.read_bytes(), alias.read_bytes())
            for name in ("etc/config/airpi-fan", "usr/share/rpcd/acl.d/luci-app-airpi-fancontrol.json"):
                self.assertEqual((installed / name).read_bytes(), (package / "files" / name).read_bytes())
            print("AP fan real package install: missing versioned view reproduced; repaired menu selects exact installed JS")

    def test_patch_is_exact_repeatable_and_rejects_source_drift(self):
        patched = fix.patch_view(self.original)
        self.assertEqual(fix.patch_view(patched), patched)
        for data in (self.original + b"\n", patched + b"\n",
                     patched.replace(b"res.code !== 0", b"false")):
            with self.assertRaisesRegex(ValueError, "reviewed locked source"):
                fix.patch_view(data)

    def test_confirm_cancel_and_real_exec_response_shape(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is required for fan UI regression")
        with tempfile.TemporaryDirectory() as temp:
            view = Path(temp) / "fancontrol.js"
            view.write_bytes(fix.patch_view(self.original))
            result = subprocess.run(
                [node, str(Path(__file__).with_name("fancontrol-ui-test.js")), str(view)],
                capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("fan UI confirmation/command regressions passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
