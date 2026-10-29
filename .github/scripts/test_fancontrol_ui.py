"""Exercise the locked fan view with the LuCI APIs present on the router."""
import hashlib
import importlib.util
from pathlib import Path
import shutil
import subprocess
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
