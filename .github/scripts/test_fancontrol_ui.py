"""Exercise the locked fan view with the LuCI APIs present on the router."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
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

    def package_fixture(self, root):
        for name, data in ((fix.VIEW, self.original), (fix.MENU, self.menu)):
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
        for bad in ("menu", "partial_menu", "alias"):
            with self.subTest(bad=bad), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.package_fixture(root)
                if bad == "menu":
                    (root / fix.MENU).write_bytes(self.menu + b"\n")
                elif bad == "partial_menu":
                    (root / fix.MENU).write_bytes(self.menu.replace(
                        b'airpi-fancontrol/fancontrol"', b'airpi-fancontrol/fancontrol-stale"'))
                else:
                    (root / fix.VIEW).with_name("fancontrol-10b9af7ae581.js").write_bytes(b"old view")
                before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
                with self.assertRaises(ValueError):
                    fix.patch_package(root)
                self.assertEqual(before, {p: p.read_bytes() for p in root.rglob("*") if p.is_file()})

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
