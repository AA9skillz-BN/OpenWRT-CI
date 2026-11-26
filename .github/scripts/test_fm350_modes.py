"""Check the real AP build hook selects the requested FM350 adaptations."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")


@unittest.skipUnless(BASH, "Bash is required for the device hook")
class Fm350ModeTests(unittest.TestCase):
    def test_mode_selects_both_patch_scripts_and_rejects_unknown(self):
        source = (REPO / "Scripts/AP3000M/private.sh").read_text(encoding="utf-8")
        block = source.split("# Apply the independently reviewed patch for the selected plugin mode.\n", 1)[1].split("\n# 6)", 1)[0]
        script = 'set -eu\npython3() { printf "%s\\n" "$1"; }\n' + block
        for mode, suffix, success in (("locked", "", True), ("latest", "-latest", True), ("other", "", False)):
            with self.subTest(mode=mode):
                result = subprocess.run([BASH, "-c", script], text=True, capture_output=True,
                                        env=dict(os.environ, GITHUB_WORKSPACE="/repo", WRT_PLUGIN_MODE=mode))
                self.assertEqual(result.returncode == 0, success, result.stderr)
                if success:
                    self.assertEqual(result.stdout.splitlines(), [
                        f"/repo/Scripts/AP3000M/fancontrol/fix-confirm{suffix}.py",
                        f"/repo/Scripts/AP3000M/fm350/fix-pdp-dns{suffix}.py",
                        f"/repo/Scripts/AP3000M/fm350/fix-packaging{suffix}.py",
                    ])
                else:
                    self.assertIn("Unsupported AP3000M plugin mode", result.stderr)


if __name__ == "__main__":
    unittest.main()
