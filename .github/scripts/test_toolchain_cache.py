"""Cache invalidation, corruption gates and original-stamp reuse regressions."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import toolchain_cache as cache
import importlib.util

ROLL_SPEC = importlib.util.spec_from_file_location("roll_shared_cache", Path(__file__).with_name("roll-device-cache.py"))
roll = importlib.util.module_from_spec(ROLL_SPEC)
ROLL_SPEC.loader.exec_module(roll)


class ToolchainCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tree = Path(self.temp.name).resolve()
        self.reports = self.tree / "reports"
        self.put("tools/Makefile", "$(CONFIG_PACKAGE_kmod-b43) $(CONFIG_DWARVES)")
        self.put("toolchain/Makefile", "$(CONFIG_GDB) $(CONFIG_NASM) $(CONFIG_CPU_TYPE)")
        self.put("include/subdir.mk", "$(CONFIG_GCC_VERSION) $(CONFIG_PKG_CC_STACKPROTECTOR_STRONG)")
        self.put("scripts/timestamp.pl", "#!/usr/bin/perl\nexit 0;\n")
        (self.tree / "scripts/timestamp.pl").chmod(0o755)
        self.put("rules.mk", "$(CONFIG_TARGET_OPTIMIZATION)")
        self.put("target/linux/generic/kernel-6.18", "LINUX_VERSION-6.18 = .1")
        self.put("target/linux/mediatek/Makefile", "KERNEL_PATCHVER:=6.18")
        self.put("target/linux/mediatek/filogic/target.mk", "CPU_TYPE:=cortex-a53")
        self.put(".config", 'CONFIG_CPU_TYPE="cortex-a53"\nCONFIG_PACKAGE_luci=y\nCONFIG_TARGET_DEVICE_test=y\n')
        subprocess.run(["git", "init", "-q", str(self.tree)], check=True)
        subprocess.run(["git", "-C", str(self.tree), "add", "."], check=True)
        self.values = {key: "" for key in cache.VARIABLES}
        self.values.update({"BOARD": "mediatek", "SUBTARGET": "filogic",
            "STAGING_DIR_HOST": str(self.tree / "staging_dir/host"),
            "TOOLCHAIN_DIR": str(self.tree / "staging_dir/toolchain-aarch64"),
            "tools/stamp-compile": str(self.tree / "staging_dir/host/stamp/.tools_compile_yn"),
            "toolchain/stamp-compile": str(self.tree / "staging_dir/toolchain-aarch64/stamp/.toolchain_compile"),
            "MAKEFILE_LIST": "rules.mk target/linux/mediatek/Makefile target/linux/mediatek/filogic/target.mk"})
        self.host = {"os": "Ubuntu", "libraries": "1", "compiler": "gcc13"}
        self.probe = patch.object(cache, "probe", side_effect=lambda tree: dict(self.values))
        self.env = patch.object(cache, "host_environment", side_effect=lambda: dict(self.host))
        self.probe.start(); self.addCleanup(self.probe.stop)
        self.env.start(); self.addCleanup(self.env.stop)

    def put(self, name, text):
        path = self.tree / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def key(self):
        return cache.fingerprint(self.tree)[0]

    def setup_installed(self):
        key, inputs = cache.fingerprint(self.tree)
        cache.write(self.reports / cache.INPUTS, {"key": key, "inputs": inputs})
        self.put("staging_dir/host/bin/example", "installed host tool")
        self.put("staging_dir/toolchain-aarch64/bin/example-gcc", "installed compiler")
        for name in ("tools/stamp-compile", "toolchain/stamp-compile"):
            self.put(Path(self.values[name]).relative_to(self.tree), "")
        self.put("staging_dir/toolchain-aarch64/stamp/.gcc_final_installed", "")
        cache.seal(self.tree, self.reports)

    def test_packages_devices_feeds_plugins_and_rust_do_not_invalidate(self):
        first = self.key()
        with (self.tree / ".config").open("a") as stream:
            stream.write('CONFIG_PACKAGE_luci=n\nCONFIG_PACKAGE_other=y\nCONFIG_TARGET_DEVICE_test=n\n'
                         'CONFIG_TARGET_DEVICE_another=y\nCONFIG_GOLANG_EXTERNAL_BOOTSTRAP_ROOT="/new/go"\n')
        self.put("Profiles/packages.lock.json", '{"new": "plugin"}')
        self.put("feeds/packages/lang/rust/Makefile", "new rust")
        self.put(".github/scripts/test_other_device.py", "changed test")
        self.put("files/etc/build.tag", "new build date")
        self.assertEqual(first, self.key())

    def test_actual_tool_selection_and_abi_config_invalidate(self):
        original = (self.tree / ".config").read_text()
        first = self.key()
        for symbol, value in (("CONFIG_PACKAGE_kmod-b43", "y"), ("CONFIG_GDB", "y"),
                              ("CONFIG_NASM", "y"), ("CONFIG_CPU_TYPE", '"cortex-a72"'),
                              ("CONFIG_TARGET_OPTIMIZATION", '"-Os -mcpu=cortex-a53"'),
                              ("CONFIG_PKG_CC_STACKPROTECTOR_STRONG", "y")):
            with self.subTest(symbol=symbol):
                self.put(".config", original + f"{symbol}={value}\n")
                self.assertNotEqual(first, self.key())
        self.put(".config", original)

    def test_rules_headers_patches_new_inputs_and_runner_invalidate(self):
        first = self.key()
        for filename in ("target/linux/generic/kernel-6.18", "target/linux/generic/pending-6.18/001.patch",
                         "tools/foo/patches/fix.patch", "toolchain/Makefile", "scripts/timestamp.pl",
                         "include/subdir.mk", "target/linux/mediatek/filogic/target.mk"):
            with self.subTest(filename=filename):
                path = self.tree / filename
                before = path.read_text() if path.exists() else None
                self.put(filename, "changed source input")
                self.assertNotEqual(first, self.key())
                if before is None:
                    path.unlink()
                else:
                    self.put(filename, before)
        self.host["libraries"] = "2"
        self.assertNotEqual(first, self.key())

    def test_content_not_commit_or_mtime_defines_identity(self):
        first = self.key()
        subprocess.run(["git", "-C", str(self.tree), "-c", "user.name=Test", "-c",
                        "user.email=test@example.invalid", "commit", "-qm", "source"], check=True)
        for path in (self.tree / "tools/Makefile", self.tree / "toolchain/Makefile"):
            os.utime(path, (cache.EPOCH, cache.EPOCH))
        self.assertEqual(first, self.key())

    def test_unknown_dynamic_tool_config_requires_review(self):
        self.put("tools/new/Makefile", "$(CONFIG_PACKAGE_$(name))")
        with self.assertRaisesRegex(ValueError, "dynamic config"):
            self.key()

    def test_changed_inputs_before_seal_or_restore_fail_closed(self):
        self.setup_installed()
        self.put("toolchain/new.mk", "changed after computing the key")
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            cache.seal(self.tree, self.reports)
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            cache.restore(self.tree, self.reports, True)

    def test_corruption_missing_files_and_wrong_manifest_fail_before_touch(self):
        self.setup_installed()
        source = self.tree / "tools/Makefile"
        before = source.stat().st_mtime_ns
        binary = self.tree / "staging_dir/host/bin/example"
        binary.write_text("damaged")
        with self.assertRaisesRegex(ValueError, "incomplete, changed"):
            cache.restore(self.tree, self.reports, True)
        self.assertEqual(source.stat().st_mtime_ns, before)
        binary.unlink()
        with self.assertRaisesRegex(ValueError, "incomplete, changed"):
            cache.restore(self.tree, self.reports, True)
        self.put("staging_dir/host/bin/example", "installed host tool")
        manifest = json.loads((self.tree / cache.MANIFEST).read_text())
        manifest["key"] = "wrong"
        cache.write(self.tree / cache.MANIFEST, manifest)
        with self.assertRaisesRegex(ValueError, "exact key"):
            cache.restore(self.tree, self.reports, True)

    @unittest.skipIf(os.name == "nt", "POSIX source timestamps and links")
    def test_hit_preserves_stamps_and_only_normalizes_tools_sources(self):
        self.setup_installed()
        paths = cache.stamps(self.tree, cache.expected(self.reports)["inputs"])
        before = [p.stat().st_mtime_ns for p in paths]
        other = self.put("package/custom/Makefile", "package source")
        other_time = other.stat().st_mtime_ns
        cache.restore(self.tree, self.reports, True)
        self.assertTrue(json.loads((self.reports / cache.STATE).read_text())["validated"])
        self.assertEqual(before, [p.stat().st_mtime_ns for p in paths])
        self.assertEqual((self.tree / "tools/Makefile").stat().st_mtime, cache.EPOCH)
        self.assertEqual(other.stat().st_mtime_ns, other_time)

    @unittest.skipIf(os.name == "nt", "POSIX links")
    def test_links_to_uncached_build_dir_are_rejected(self):
        self.setup_installed()
        target = self.put("build_dir/host/example", "not cached")
        (self.tree / "staging_dir/host/bin/unsafe").symlink_to(target)
        with self.assertRaisesRegex(ValueError, "uncached build files"):
            cache.seal(self.tree, self.reports)

    @unittest.skipIf(os.name == "nt", "POSIX links")
    def test_changed_runner_link_target_is_rejected(self):
        self.setup_installed()
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "runner-tool"
            binary.write_text("version one")
            (self.tree / "staging_dir/host/bin/runner-tool").symlink_to(binary)
            cache.seal(self.tree, self.reports)
            binary.write_text("version two")
            with self.assertRaisesRegex(ValueError, "incomplete, changed"):
                cache.restore(self.tree, self.reports, True)

    def test_miss_leaves_source_timestamps_alone(self):
        key, inputs = cache.fingerprint(self.tree)
        cache.write(self.reports / cache.INPUTS, {"key": key, "inputs": inputs})
        path = self.tree / "tools/Makefile"
        before = path.stat().st_mtime_ns
        cache.restore(self.tree, self.reports, False)
        self.assertEqual(before, path.stat().st_mtime_ns)

    def test_timing_warning_and_overlapping_activity(self):
        cache.write(self.reports / cache.STATE, {"exact_hit": True, "validated": True})
        self.put("reports/parallel.tsv", "1\tbegin\tcompile\ttools/a\n2\tbegin\tcompile\ttools/b\n"
                 "3\tend\tcompile\ttools/a\n4\tend\tcompile\ttools/b\n")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            cache.report(self.tree, self.reports)
        self.assertIn("::warning::", output.getvalue())
        evidence = json.loads((self.reports / cache.STATE).read_text())
        self.assertEqual(evidence["activity_seconds"], 3)
        self.assertEqual(evidence["compiled"], ["tools/a", "tools/b"])


class SharedRetentionTests(unittest.TestCase):
    def test_shared_cleanup_preserves_recent_newest_current_and_other_targets(self):
        prefix = "wrt-v3-toolchain-mediatek-filogic"
        def entry(number, date):
            return {"id": number, "key": prefix + "-" + str(number)*64, "createdAt": date}
        entries = [entry(1, "2020-01-01T00:00:00Z"), entry(2, "2020-01-02T00:00:00Z"),
                   entry(3, "2099-01-01T00:00:00Z"), entry(4, "2099-01-02T00:00:00Z"),
                   {"id": 5, "key": "wrt-v3-toolchain-other-target-" + "5"*64,
                    "createdAt": "2020-01-01T00:00:00Z"}]
        device = [{"id": 6, "key": "wrt-v2-ap3000m-linux-x64-staging-old", "createdAt": "2020"},
                  {"id": 7, "key": "wrt-v2-c8-688-linux-x64-staging-old", "createdAt": "2020"}]
        env = {"TOOLCHAIN_CACHE_PREFIX": prefix, "DEVICE_STAGING_KEY": entries[0]["key"],
               "DEVICE_CACHE_PREFIX": "wrt-v2-ap3000m-linux-x64", "DEVICE_DL_KEY": "dl-current",
               "DEVICE_CCACHE_KEY": "ccache-current", "GITHUB_REPOSITORY": "owner/repo",
               "GITHUB_REF": "refs/heads/main"}
        with patch.dict(os.environ, env), patch.object(roll.subprocess, "check_output", side_effect=[
                json.dumps(device), json.dumps(entries)]) as listing, patch.object(roll.subprocess, "run") as remove:
            remove.return_value.returncode = 0
            roll.main()
        self.assertTrue(all("refs/heads/main" in call.args[0] for call in listing.call_args_list))
        self.assertEqual([call.args[0][3] for call in remove.call_args_list], ["2", "6"])

    def test_missing_shared_current_never_prunes(self):
        prefix = "wrt-v3-toolchain-mediatek-filogic"
        env = {"TOOLCHAIN_CACHE_PREFIX": prefix, "DEVICE_STAGING_KEY": prefix + "-" + "a"*64,
               "DEVICE_CACHE_PREFIX": "wrt-v2-ap3000m-linux-x64", "DEVICE_DL_KEY": "dl-current",
               "DEVICE_CCACHE_KEY": "ccache-current", "GITHUB_REPOSITORY": "owner/repo",
               "GITHUB_REF": "refs/heads/main"}
        entries = [{"id": i, "key": prefix + "-" + str(i)*64, "createdAt": "2020-01-01T00:00:00Z"}
                   for i in (1, 2, 3)]
        with patch.dict(os.environ, env), patch.object(roll.subprocess, "check_output", side_effect=["[]", json.dumps(entries)]), \
             patch.object(roll.subprocess, "run") as remove:
            roll.main()
        remove.assert_not_called()


if __name__ == "__main__":
    unittest.main()
