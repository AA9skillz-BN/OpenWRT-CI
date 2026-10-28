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
CONFIG_TARGET_SUFFIX="musl"
CONFIG_USE_MUSL=y
CONFIG_CCACHE=y
'''


@unittest.skipUnless(os.name != "nt" and shutil.which("make"), "real Linux Make rules required")
class UpstreamCacheTests(unittest.TestCase):
    def world_make(self, tree, goal):
        # Enter via the real outer Makefile so its exports, then the actual
        # target/package/tools/toolchain include order, define the stamps.
        wrapper = tree / "tmp/.ci-cache-world.mk"
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        names = ("HOST_OS", "HOST_ARCH", "GNU_HOST_NAME", "tools-y", "tools-",
                 "tools/stamp-compile", "toolchain/stamp-compile", "toolchain/builddirs")
        wrapper.write_text("\n".join([
            "ifeq ($(__rules_inc),1)", ".PHONY: ci-cache-world-inputs ci-warm-cache",
            "ci-cache-world-inputs:",
            "\t@" + "".join(f"$(info CI_WORLD:{name}=$({name}))" for name in names) + ":",
            "ci-warm-cache: $(tools/stamp-compile) $(toolchain/stamp-compile)", "\t@:",
            "else", ".PHONY: ci-cache-world-inputs ci-warm-cache",
            "ci-cache-world-inputs ci-warm-cache:",
            "\t@+$(SUBMAKE) -r -f $(TOPDIR)/Makefile -f " + str(wrapper) + " $@",
            "endif", "",
        ]), encoding="utf-8")
        return cache.capture("make", "--no-print-directory", "-s", "-f", "Makefile", "-f", str(wrapper),
                             goal, cwd=tree)

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
                world = dict(line.removeprefix("CI_WORLD:").split("=", 1)
                             for line in self.world_make(tree, "ci-cache-world-inputs").splitlines()
                             if line.startswith("CI_WORLD:"))
                self.assertEqual(values["HOST_OS"], "Linux")
                self.assertTrue(values["GNU_HOST_NAME"])
                self.assertEqual(world, {name: values[name] for name in world})
                self.assertEqual(len(world), 8)
                info = Path(values["TOOLCHAIN_DIR"]) / "info.mk"
                # A prerequisite/initial compiler can leave the unsuffixed
                # prefix in generated info.mk, as observed in the full builds.
                initial_info = info.read_text().replace(
                    "TARGET_CROSS=", "TARGET_CROSS=" + values["GNU_TARGET_NAME"] + "-")
                info.write_text(initial_info)
                values = cache.probe(tree)
                key, inputs = cache.fingerprint(tree, values=values)
                reports = tree / "reports"
                cache.write(reports / cache.INPUTS, {"key": key, "inputs": inputs})
                # Exercise the real GCC install mutation that the previous
                # stamp-only fixture missed. info.mk is a generated output.
                installed_prefix = values["REAL_GNU_TARGET_NAME"] + "-"
                self.assertNotEqual(values["TARGET_CROSS"], installed_prefix)
                installed_info = initial_info.replace("TARGET_CROSS=" + values["GNU_TARGET_NAME"] + "-",
                                                      "TARGET_CROSS=" + installed_prefix)
                info.write_text(installed_info)
                self.assertEqual(cache.probe(tree)["TARGET_CROSS"], installed_prefix)
                self.assertEqual(key, cache.fingerprint(tree)[0])
                info.write_text(initial_info)
                bootstrap = Path(values["STAGING_DIR_HOST"]) / "bin/prerequisite-fixture"
                bootstrap.parent.mkdir(parents=True, exist_ok=True)
                bootstrap.write_text("pristine prerequisite")
                cache.prepare(tree, reports)
                info.write_text(installed_info)
                for root in cache.roots(tree, inputs):
                    (root / "bin").mkdir(parents=True, exist_ok=True)
                    (root / "stamp").mkdir(exist_ok=True)
                    (root / "bin/example").write_text("installed artifact fixture")
                # Construct fixtures at independently obtained world-build
                # paths, not at paths produced by the probe under test.
                completed = [Path(world["tools/stamp-compile"]), Path(world["toolchain/stamp-compile"]),
                             Path(values["TOOLCHAIN_DIR"]) / "stamp/.gcc_final_installed"]
                previous = time.time() - 3600
                for path in completed:
                    path.write_text("")
                    os.utime(path, (previous, previous))
                # Prove that an ordinary fresh checkout fails the real check.
                timestamp = tree / "scripts/timestamp.pl"
                fresh = subprocess.run([str(timestamp), "-n", str(completed[0]), "tools"], cwd=tree)
                self.assertEqual(fresh.returncode, 1)
                # The full kernel build adds another optional kernel.mk
                # include. Reproduce its appearance before sealing the cache.
                kernel_config = Path(values["LINUX_DIR"]) / ".config"
                kernel_config.parent.mkdir(parents=True, exist_ok=True)
                kernel_config.write_text("CONFIG_ARM64=y\nCONFIG_NEW_KERNEL_SYMBOL=y\n")
                self.assertIn(str(kernel_config), cache.probe(tree)["MAKEFILE_LIST"].split())
                self.assertEqual(key, cache.fingerprint(tree)[0])
                cache.seal(tree, reports)
                times = [p.stat().st_mtime_ns for p in completed]
                # This build output is not included in the cache archive.
                shutil.rmtree(tree / "build_dir")
                cache.restore(tree, reports, True)
                self.assertEqual(times, [p.stat().st_mtime_ns for p in completed])
                self.assertFalse((tree / "build_dir").exists())
                (tree / "tmp").mkdir(exist_ok=True)
                (tree / "tmp/.build").touch()
                self.world_make(tree, "ci-warm-cache")
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
