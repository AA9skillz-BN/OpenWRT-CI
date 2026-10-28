"""Exercise real locked upstream stamp rules without compiling a toolchain."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.request

import toolchain_cache as cache

REPO = Path(__file__).resolve().parents[2]
CONFIG = '''CONFIG_TARGET_mediatek=y
CONFIG_TARGET_mediatek_filogic=y
CONFIG_TARGET_BOARD="mediatek"
CONFIG_TARGET_SUBTARGET="filogic"
CONFIG_ARCH="aarch64"
CONFIG_CPU_TYPE="cortex-a53"
CONFIG_TARGET_OPTIMIZATION="-Os -pipe -mcpu=cortex-a53"
CONFIG_GCC_VERSION="14.4.0"
CONFIG_LIBC="musl"
CONFIG_USE_MUSL=y
CONFIG_CCACHE=y
'''


@unittest.skipUnless(os.name != "nt" and shutil.which("make"), "real Linux Make rules required")
class UpstreamCacheTests(unittest.TestCase):
    def test_both_locked_sources_reuse_original_stamps_without_build_dir(self):
        locks = json.loads((REPO / "Profiles/sources.lock.json").read_text())
        for name in ("AP3000M", "TR3000-512"):
            with self.subTest(source=name), tempfile.TemporaryDirectory() as directory, \
                 patch.object(cache, "host_environment", return_value={"machine": "test-host"}):
                tree = Path(directory).resolve()
                item = locks[name]
                repository = item["url"].removesuffix(".git").removeprefix("https://github.com/")
                url = f"https://codeload.github.com/{repository}/tar.gz/{item['commit']}"
                data = urllib.request.urlopen(url, timeout=90).read()
                # Read ordinary source files only; never extract archive links.
                with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                    for member in archive:
                        relative = Path(*Path(member.name).parts[1:])
                        if not relative.parts or ".." in relative.parts or not member.isfile():
                            continue
                        path = tree / relative
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(archive.extractfile(member).read())
                        path.chmod(member.mode & 0o777)
                subprocess.run(["git", "init", "-q", str(tree)], check=True)
                subprocess.run(["git", "-C", str(tree), "add", "."], check=True)
                (tree / ".config").write_text(CONFIG)
                values = cache.probe(tree)
                key, inputs = cache.fingerprint(tree, values=values)
                reports = tree / "reports"
                cache.write(reports / cache.INPUTS, {"key": key, "inputs": inputs})
                bootstrap = Path(values["STAGING_DIR_HOST"]) / "bin/prerequisite-fixture"
                bootstrap.parent.mkdir(parents=True, exist_ok=True)
                bootstrap.write_text("pristine prerequisite")
                cache.prepare(tree, reports)
                for root in cache.roots(tree, inputs):
                    (root / "bin").mkdir(parents=True, exist_ok=True)
                    (root / "stamp").mkdir(exist_ok=True)
                    (root / "bin/example").write_text("installed artifact fixture")
                completed = [Path(values["tools/stamp-compile"]), Path(values["toolchain/stamp-compile"]),
                             Path(values["TOOLCHAIN_DIR"]) / "stamp/.gcc_final_installed"]
                previous = time.time() - 3600
                for path in completed:
                    path.write_text("")
                    os.utime(path, (previous, previous))
                # Prove that an ordinary fresh checkout fails the real check.
                timestamp = tree / "scripts/timestamp.pl"
                fresh = subprocess.run([str(timestamp), "-n", str(completed[0]), "tools"], cwd=tree)
                self.assertEqual(fresh.returncode, 1)
                cache.seal(tree, reports)
                times = [p.stat().st_mtime_ns for p in completed]
                cache.restore(tree, reports, True)
                self.assertEqual(times, [p.stat().st_mtime_ns for p in completed])
                self.assertFalse((tree / "build_dir").exists())
                (tree / "tmp").mkdir(exist_ok=True)
                (tree / "tmp/.build").touch()
                script = "\n".join([
                    "include $(TOPDIR)/rules.mk", "include $(INCLUDE_DIR)/kernel.mk",
                    "include $(INCLUDE_DIR)/depends.mk", "include $(INCLUDE_DIR)/subdir.mk",
                    "include $(TOPDIR)/tools/Makefile", "include $(TOPDIR)/toolchain/Makefile",
                    "$(toolchain/stamp-compile): $(tools/stamp-compile)",
                    ".PHONY: ci-warm-cache",
                    "ci-warm-cache: $(tools/stamp-compile) $(toolchain/stamp-compile)", "\t@:", "",
                ])
                subprocess.run(["make", "--no-print-directory", "-s", "-f", "-", f"TOPDIR={tree}",
                                "ci-warm-cache"], cwd=tree, input=script, text=True, check=True)
                # Any descent into tools/toolchain would try to prepare/build
                # missing artifacts, and either fail or create build_dir.
                self.assertFalse((tree / "build_dir").exists())
                # Device/package selection changes must remain shareable.
                (tree / ".config").write_text(CONFIG + "CONFIG_TARGET_DEVICE_other=y\nCONFIG_PACKAGE_luci=y\n")
                self.assertEqual(key, cache.fingerprint(tree)[0])
                # Reset package additions, then reject actual corrupted staging
                # and recover the prerequisite state captured before restore.
                (tree / ".config").write_text(CONFIG)
                (Path(values["STAGING_DIR_HOST"]) / "bin/example").write_text("corrupt")
                cache.restore(tree, reports, True)
                self.assertEqual(bootstrap.read_text(), "pristine prerequisite")
                self.assertFalse(completed[2].exists())
                self.assertFalse((tree / cache.MANIFEST).exists())
                self.assertEqual(json.loads((reports / cache.STATE).read_text())["fallback"], "cold")


if __name__ == "__main__":
    unittest.main()
