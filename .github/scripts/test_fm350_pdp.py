"""Replay PDP responses through the locked Rust plugin and a Linux PTY."""
import hashlib
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
spec = importlib.util.spec_from_file_location(
    "fm350_pdp_fix", REPO / "Scripts/AP3000M/fm350/fix-pdp-dns.py")
fix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fix)


class Fm350PdpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lock = json.loads((REPO / "Profiles/packages.lock.json").read_text())["fm350"]
        if lock["url"] != "https://github.com/LianXia233/luci-app-fm350.git":
            raise AssertionError("review the FM350 patch for a changed package source")
        url = ("https://codeload.github.com/LianXia233/luci-app-fm350/tar.gz/" + lock["commit"])
        with urllib.request.urlopen(url, timeout=60) as response:
            cls.archive = response.read()
        with tarfile.open(fileobj=io.BytesIO(cls.archive)) as archive:
            member = next(m for m in archive.getmembers() if m.name.endswith("/rust/" + fix.SOURCE.removeprefix("rust/")))
            cls.original = archive.extractfile(member).read()
        if hashlib.sha256(cls.original).hexdigest() != fix.UPSTREAM_SHA256:
            raise AssertionError("locked PDP source hash mismatch")

    def test_patch_is_repeatable_and_rejects_source_or_patch_drift(self):
        patched = fix.patch_pdp(self.original)
        self.assertEqual(fix.patch_pdp(patched), patched)
        for data in (self.original + b"\n", patched + b"\n",
                     patched.replace(b"contr_dns.push(a6)", b"contr_ipv6 = a6")):
            with self.assertRaisesRegex(ValueError, "reviewed locked source"):
                fix.patch_pdp(data)

    @unittest.skipIf(os.name == "nt", "the real serial-port regression requires Linux PTYs")
    def test_real_pdp_and_dial_fail_before_patch_and_pass_after_patch(self):
        cargo = shutil.which("cargo")
        self.assertIsNotNone(cargo, "Cargo is required on the Linux checks runner")
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)
            with tarfile.open(fileobj=io.BytesIO(self.archive)) as archive:
                for member in archive.getmembers():
                    parts = Path(member.name).parts
                    if len(parts) < 3 or parts[1] != "rust" or not member.isfile():
                        continue
                    relative = Path(*parts[2:])
                    self.assertNotIn("..", relative.parts)
                    dest = tree / relative
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(archive.extractfile(member).read())
            tests = tree / "tests"
            tests.mkdir(exist_ok=True)
            shutil.copyfile(Path(__file__).with_name("fm350-pdp-regression.rs"),
                            tests / "repository_pdp_regression.rs")
            command = [cargo, "test", "--locked", "--test", "repository_pdp_regression", "--", "--test-threads=1"]
            before = subprocess.run(command, cwd=tree, capture_output=True, text=True, timeout=300)
            self.assertNotEqual(before.returncode, 0, before.stdout + before.stderr)
            self.assertIn("dns_only_with_stale_cgpaddr_is_inactive ... FAILED", before.stdout)
            self.assertIn("dial_activates_dns_only_session_instead_of_reusing_stale_address ... FAILED", before.stdout)
            (tree / "src/modem/pdp.rs").write_bytes(fix.patch_pdp(self.original))
            after = subprocess.run(command, cwd=tree, capture_output=True, text=True, timeout=180)
            self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
            self.assertIn("7 passed; 0 failed", after.stdout)
            print("FM350 original source reproduces the PDP/dial failure; patched real PTY tests: " + after.stdout)


if __name__ == "__main__":
    unittest.main()
