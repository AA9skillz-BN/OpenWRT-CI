"""Check the real AP build hook selects the requested FM350 adaptations."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else None)


@unittest.skipUnless(BASH, "Bash is required for the device hook")
class Fm350ModeTests(unittest.TestCase):
    def test_mode_selects_both_patch_scripts_and_rejects_unknown(self):
        source = (REPO / "Scripts/AP3000M/private.sh").read_text(encoding="utf-8")
        block = source.split("# Apply the independently reviewed patch for the selected plugin mode.\n", 1)[1].split("\n# 6)", 1)[0]
        script = 'set -eu\npython3() { printf "%s\\n" "$1"; }\n' + block
        for mode, suffix, success in (("locked", "", True), ("latest", "-latest", True),
                                      ("auto", "-latest", True), ("other", "", False)):
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

    @unittest.skipUnless(shutil.which("jq"), "jq required for per-plugin auto resolution")
    def test_auto_mode_honors_per_plugin_fallback_independently(self):
        source = (REPO / "Scripts/AP3000M/private.sh").read_text(encoding="utf-8")
        block = source.split("# Apply the independently reviewed patch for the selected plugin mode.\n", 1)[1].split("\n# 6)", 1)[0]
        script = 'set -eu\npython3() { printf "%s\\n" "$1"; }\n' + block
        with tempfile.TemporaryDirectory() as temp:
            reports = Path(temp)
            (reports / "plugin-resolution.json").write_text(json.dumps({
                "mode": "auto",
                "plugins": {
                    "fancontrol": {"effective_mode": "locked"},
                    "fm350": {"effective_mode": "latest"},
                }
            }), encoding="utf-8")
            result = subprocess.run(
                [BASH, "-c", script], text=True, capture_output=True,
                env=dict(os.environ, GITHUB_WORKSPACE="/repo", WRT_PLUGIN_MODE="auto",
                         WRT_BUILD_REPORTS=Path(reports).as_posix()),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), [
                "/repo/Scripts/AP3000M/fancontrol/fix-confirm.py",
                "/repo/Scripts/AP3000M/fm350/fix-pdp-dns-latest.py",
                "/repo/Scripts/AP3000M/fm350/fix-packaging-latest.py",
            ])


if __name__ == "__main__":
    unittest.main()
