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
        if not (self.tree / cache.BOOTSTRAP).exists():
            self.put("staging_dir/host/bin/bootstrap", "pristine host prerequisite")
            cache.prepare(self.tree, self.reports)
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

    def test_make_unset_and_literal_n_are_not_equivalent(self):
        first = self.key()
        with (self.tree / ".config").open("a") as stream:
            stream.write("# CONFIG_GDB is not set\n")
        self.assertEqual(first, self.key())
        with (self.tree / ".config").open("a") as stream:
            stream.write("CONFIG_GDB=n\n")
        self.assertNotEqual(first, self.key())

    def test_internal_compiler_install_prefix_is_not_an_input_change(self):
        self.values.update({"GNU_TARGET_NAME": "aarch64-openwrt-linux",
                            "REAL_GNU_TARGET_NAME": "aarch64-openwrt-linux-musl",
                            "TARGET_CROSS": "aarch64-openwrt-linux-"})
        first = self.key()
        self.values["TARGET_CROSS"] = "aarch64-openwrt-linux-musl-"
        self.assertEqual(first, self.key())
        self.values["TARGET_CROSS"] = "different-compiler-"
        with self.assertRaisesRegex(ValueError, "unexpected internal toolchain prefix"):
            self.key()

    def test_generated_kernel_config_is_not_a_source_input(self):
        linux = self.tree / "build_dir/target-aarch64/linux-mediatek_filogic/linux-6.18.1"
        self.values["LINUX_DIR"] = str(linux)
        first = self.key()
        kernel_config = self.put(linux.relative_to(self.tree) / ".config", "CONFIG_ARM64=y\n")
        self.values["MAKEFILE_LIST"] += " " + kernel_config.as_posix()
        self.assertEqual(first, self.key())
        kernel_config.write_text("CONFIG_ARM64=y\nCONFIG_NEW_KERNEL_SYMBOL=y\n")
        self.assertEqual(first, self.key())
        # The exception must not drop arbitrary build or target includes.
        extra = self.put("build_dir/other.mk", "CONFIG_EXTRA_INPUT=y\n")
        self.values["MAKEFILE_LIST"] += " " + extra.as_posix()
        self.assertNotEqual(first, self.key())
        self.values["MAKEFILE_LIST"] = self.values["MAKEFILE_LIST"].removesuffix(" " + extra.as_posix())
        self.put("target/linux/mediatek/filogic/target.mk", "CPU_TYPE:=cortex-a72")
        self.assertNotEqual(first, self.key())

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

    def test_corruption_missing_files_and_wrong_manifest_fall_back_before_touch(self):
        for corruption in ("damaged", "missing", "wrong manifest"):
            with self.subTest(corruption=corruption):
                self.setup_installed()
                source = self.tree / "tools/Makefile"
                before = source.stat().st_mtime_ns
                binary = self.tree / "staging_dir/host/bin/example"
                if corruption == "damaged":
                    binary.write_text("damaged")
                elif corruption == "missing":
                    binary.unlink()
                else:
                    manifest = json.loads((self.tree / cache.MANIFEST).read_text())
                    manifest["key"] = "wrong"
                    cache.write(self.tree / cache.MANIFEST, manifest)
                cache.restore(self.tree, self.reports, True)
                state = json.loads((self.reports / cache.STATE).read_text())
                self.assertEqual(state["fallback"], "cold")
                self.assertEqual(state["bad_key"], cache.expected(self.reports)["key"])
                self.assertEqual(source.stat().st_mtime_ns, before)
                self.assertEqual((self.tree / "staging_dir/host/bin/bootstrap").read_text(), "pristine host prerequisite")
                self.assertFalse(binary.exists())
                self.assertFalse((self.tree / cache.MANIFEST).exists())
                self.assertFalse((self.tree / "staging_dir/toolchain-aarch64/stamp/.gcc_final_installed").exists())

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
    def test_known_absent_manual_alias_survives_copy_and_preserves_stamps(self):
        self.setup_installed()
        manual = self.tree / "staging_dir/host/share/man/man3"
        manual.mkdir(parents=True)
        alias = manual / "bn_add_words.3"
        alias.symlink_to("bn_dump.3")
        cache.seal(self.tree, self.reports)
        saved = json.loads((self.tree / cache.MANIFEST).read_text())
        self.assertEqual(saved["files"][alias.relative_to(self.tree).as_posix()],
                         {"type": "link", "target": "bn_dump.3", "dangling": True})
        completed = cache.stamps(self.tree, cache.expected(self.reports)["inputs"])
        times = [p.stat().st_mtime_ns for p in completed]
        host = self.tree / "staging_dir/host"
        with tempfile.TemporaryDirectory() as directory:
            shutil.copytree(host, Path(directory) / "host", symlinks=True)
            shutil.rmtree(host)
            shutil.copytree(Path(directory) / "host", host, symlinks=True)
        cache.restore(self.tree, self.reports, True)
        self.assertTrue(json.loads((self.reports / cache.STATE).read_text())["validated"])
        self.assertEqual(times, [p.stat().st_mtime_ns for p in completed])
        self.assertEqual(os.readlink(alias), "bn_dump.3")
        self.assertFalse(alias.exists())

    @unittest.skipIf(os.name == "nt", "POSIX links")
    def test_manual_exception_does_not_hide_cache_changes(self):
        for change in ("removed", "retargeted", "target appeared", "normal target removed"):
            with self.subTest(change=change):
                self.setup_installed()
                manual = self.tree / "staging_dir/host/share/man/man3"
                manual.mkdir(parents=True, exist_ok=True)
                alias = manual / "bn_add_words.3"
                alias.unlink(missing_ok=True)
                alias.symlink_to("bn_dump.3")
                target = manual / "bn_dump.3"
                if change == "normal target removed":
                    target.write_text("present at seal")
                cache.seal(self.tree, self.reports)
                if change == "removed":
                    alias.unlink()
                elif change == "retargeted":
                    alias.unlink()
                    alias.symlink_to("different.3")
                elif change == "target appeared":
                    target.write_text("unexpected content")
                else:
                    target.unlink()
                cache.restore(self.tree, self.reports, True)
                self.assertEqual(json.loads((self.reports / cache.STATE).read_text())["fallback"], "cold")

    @unittest.skipIf(os.name == "nt", "POSIX links")
    def test_all_unsafe_links_are_reported_without_broad_manual_exception(self):
        self.setup_installed()
        manual = "staging_dir/host/share/man/man3/"
        unsafe = {
            manual + "unknown.3": "bn_dump.3",
            manual + "bn_add_words.3": "../../../../../../build_dir/bn_dump.3",
            manual + "bn_div_words.3": "/missing/bn_dump.3",
            "staging_dir/host/bin/bn_expand.3": "bn_dump.3",
            "staging_dir/toolchain-aarch64/share/man/man3/bn_mul_words.3": "bn_dump.3",
        }
        for name, target in unsafe.items():
            path = self.tree / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(target)
        with self.assertRaises(cache.InstalledLinksError):
            cache.seal(self.tree, self.reports)
        rejected = json.loads((self.reports / "toolchain-links-rejected.json").read_text())
        self.assertEqual({item["path"]: item["target"] for item in rejected["links"]}, unsafe)

    @unittest.skipIf(os.name == "nt", "POSIX links")
    def test_manual_directory_escape_and_missing_target_chain_are_rejected(self):
        self.setup_installed()
        outside = self.tree / "build_dir/manual"
        outside.mkdir(parents=True)
        (outside / "bn_add_words.3").symlink_to("bn_dump.3")
        share = self.tree / "staging_dir/host/share/man"
        share.mkdir(parents=True)
        (share / "man3").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(cache.InstalledLinksError):
            cache.seal(self.tree, self.reports)
        (share / "man3").unlink()
        (share / "man3").mkdir()
        (share / "man3/bn_add_words.3").symlink_to("bn_dump.3")
        (share / "man3/bn_dump.3").symlink_to("missing.3")
        with self.assertRaises(cache.InstalledLinksError):
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
            cache.restore(self.tree, self.reports, True)
            self.assertEqual(json.loads((self.reports / cache.STATE).read_text())["fallback"], "cold")

    def test_miss_leaves_source_timestamps_alone(self):
        key, inputs = cache.fingerprint(self.tree)
        cache.write(self.reports / cache.INPUTS, {"key": key, "inputs": inputs})
        path = self.tree / "tools/Makefile"
        before = path.stat().st_mtime_ns
        cache.restore(self.tree, self.reports, False)
        self.assertEqual(before, path.stat().st_mtime_ns)

    def test_partial_restore_service_error_restores_prerequisites_without_deleting_key(self):
        self.setup_installed()
        cache.restore(self.tree, self.reports, False, restore_failed=True)
        state = json.loads((self.reports / cache.STATE).read_text())
        self.assertEqual(state["fallback"], "cold")
        self.assertEqual(state["bad_key"], "")
        self.assertTrue((self.tree / "staging_dir/host/bin/bootstrap").is_file())

    @unittest.skipIf(os.name == "nt", "POSIX links")
    def test_links_to_key_covered_source_scripts_are_valid(self):
        self.setup_installed()
        (self.tree / "staging_dir/host/bin/source-tool").symlink_to(self.tree / "scripts/timestamp.pl")
        cache.seal(self.tree, self.reports)
        cache.restore(self.tree, self.reports, True)
        self.assertTrue(json.loads((self.reports / cache.STATE).read_text())["validated"])

    def test_host_package_closure_ignores_unrelated_runner_software(self):
        first = "gcc\t13\tlibc6 (>= 2.38), libgcc | alternate\t\nlibc6\t2.39\t\t\nlibgcc\t13\t\t\nazure-cli\t1\t\t"
        expected = {"gcc": "13", "libc6": "2.39", "libgcc": "13"}
        self.assertEqual(cache.package_versions(first, ["gcc"]), expected)
        self.assertEqual(cache.package_versions(first.replace("azure-cli\t1", "azure-cli\t2"), ["gcc"]), expected)
        self.assertNotEqual(cache.package_versions(first.replace("libc6\t2.39", "libc6\t2.40"), ["gcc"]), expected)
        with self.assertRaisesRegex(ValueError, "missing build host packages"):
            cache.package_versions(first, ["missing"])

    @unittest.skipIf(os.name == "nt", "Linux CPU feature source")
    def test_cpu_flags_are_only_required_for_native_compilation(self):
        self.assertNotIn("native_cpu_features", cache.fingerprint(self.tree)[1]["host"])
        self.put(".config", 'CONFIG_TARGET_OPTIMIZATION="-O2 -march=native"\n')
        self.assertIn("native_cpu_features", cache.fingerprint(self.tree)[1]["host"])

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
    def test_bad_exact_key_is_removed_even_without_a_successful_new_save(self):
        self.bad_key_plan("2020-01-01T00:00:00Z", ["99"])

    def test_newer_replacement_of_bad_key_is_preserved(self):
        self.bad_key_plan("2099-01-01T00:00:00Z", [])

    def bad_key_plan(self, created, expected):
        prefix = "wrt-v3-toolchain-mediatek-filogic"
        key = prefix + "-" + "a"*64
        env = {"TOOLCHAIN_CACHE_PREFIX": prefix, "DEVICE_STAGING_KEY": key,
               "BAD_TOOLCHAIN_KEY": key, "BAD_TOOLCHAIN_DETECTED_AT": "2026-10-28T12:00:00+00:00",
               "DEVICE_CACHE_PREFIX": "wrt-v2-ap3000m-linux-x64", "DEVICE_DL_KEY": "dl-current",
               "DEVICE_CCACHE_KEY": "ccache-current", "GITHUB_REPOSITORY": "owner/repo",
               "GITHUB_REF": "refs/heads/main"}
        entries = [{"id": 99, "key": key, "createdAt": created}]
        with patch.dict(os.environ, env), patch.object(roll.subprocess, "check_output", side_effect=["[]", json.dumps(entries)]), \
             patch.object(roll.subprocess, "run") as remove:
            remove.return_value.returncode = 0
            roll.main()
        self.assertEqual([call.args[0][3] for call in remove.call_args_list], expected)

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
