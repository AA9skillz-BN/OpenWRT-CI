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
            self.assertIn("4 passed; 0 failed", result.stdout)
            print(result.stdout)


if __name__ == "__main__":
    unittest.main()
