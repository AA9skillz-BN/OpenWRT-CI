"""Regression tests for config and publication gates; no OpenWrt SDK required."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("device_build", Path(__file__).with_name("device-build.py"))
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)
REPO = Path(__file__).resolve().parents[2]
ROLL_SPEC = importlib.util.spec_from_file_location("roll_device_cache", Path(__file__).with_name("roll-device-cache.py"))
roll = importlib.util.module_from_spec(ROLL_SPEC)
ROLL_SPEC.loader.exec_module(roll)


class DeviceContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tree = Path(self.temp.name)
        self.reports = self.tree / "reports"
        self.reports.mkdir()
        self.data = build.profile(REPO, "AP3000M")
        self.target = self.tree / "bin/targets/mediatek/filogic"
        self.target.mkdir(parents=True)
        self.prefix = "immortalwrt-mediatek-filogic-airpi_ap3000m"
        self.image = self.target / (self.prefix + "-squashfs-sysupgrade.bin")
        self.manifest = self.target / (self.prefix + ".manifest")
        self.metadata = {"supported_devices": ["airpi,ap3000m"], "version": {"target": "mediatek/filogic"}}
        self.valid_config()
        self.valid_image()

    def valid_config(self):
        values = {self.data["device_symbol"]: "y", "CONFIG_TARGET_mediatek": "y",
                  "CONFIG_TARGET_mediatek_filogic": "y", "CONFIG_CCACHE": "y"}
        values.update({f"CONFIG_PACKAGE_{p}": "y" for p in self.data["required_packages"]})
        build.write(self.tree / ".config", "".join(f"{k}={v}\n" for k, v in values.items()))

    def valid_image(self, parts=("kernel", "root")):
        with tarfile.open(self.image, "w") as archive:
            for name in parts:
                item = tarfile.TarInfo("sysupgrade-airpi_ap3000m/" + name)
                item.size = 4
                archive.addfile(item, io.BytesIO(b"test"))
        build.write(self.manifest, "".join(f"{p} - 1.0\n" for p in self.data["required_packages"]))

    def fwtool(self, command, **kwargs):
        self.assertEqual(command[1], "-i")
        self.assertEqual(Path(command[-1]), self.image)
        build.write(Path(command[2]), json.dumps(self.metadata))

    def verify(self):
        with patch.object(build.subprocess, "run", side_effect=self.fwtool), patch.object(build, "verify_payload"):
            return build.verify_image(self.tree, self.data, self.reports, REPO)

    def test_feature_fragments_preserve_existing_general_choices(self):
        for name in ("AP3000M", "TR3000-512", "C8-688"):
            data = build.profile(REPO, name)
            fragments = {}
            for item in data["config_fragments"]:
                fragments.update(build.read_config(REPO / item))
            self.assertEqual(fragments, build.read_config(REPO / "Config/GENERAL.txt"))

    def test_device_overrides_base_config(self):
        build.merge_config(REPO, self.tree, "AP3000M", self.data)
        config = build.read_config(self.tree / ".config")
        self.assertEqual(config[self.data["device_symbol"]], "y")
        self.assertEqual(config["CONFIG_TARGET_ALL_PROFILES"], "n")

    def test_config_gate_accepts_complete_device(self):
        build.check_config(self.tree, self.data)

    def test_defconfig_exit_zero_cannot_hide_kconfig_errors(self):
        result = build.subprocess.CompletedProcess(["make", "defconfig"], 0,
                 stdout="tmp/.config-package.in:12:error: recursive dependency detected!\nconfiguration written to .config\n")
        with patch.object(build.subprocess, "run", return_value=result), self.assertRaisesRegex(ValueError, "Kconfig reported"):
            build.defconfig(self.tree, self.data, self.reports)

    def test_clean_defconfig_output_is_allowed(self):
        build.check_kconfig_output("# configuration written to .config\nWARNING: optional unselected package missing\n")

    def test_defconfig_reports_silently_dropped_package(self):
        with (self.tree / ".config").open("a") as stream:
            stream.write("CONFIG_PACKAGE_unavailable-example=y\n")
        def resolve(*args, **kwargs):
            self.valid_config()
            return build.subprocess.CompletedProcess(["make", "defconfig"], 0, stdout="configuration written\n")
        with patch.object(build.subprocess, "run", side_effect=resolve):
            build.defconfig(self.tree, self.data, self.reports)
        changes = json.loads((self.reports / "config-resolution.json").read_text())
        self.assertEqual(changes, {"CONFIG_PACKAGE_unavailable-example": {
            "requested": "y", "resolved": "unavailable"}})

    def test_excluded_broken_packages_cannot_be_requested_silently(self):
        with patch.dict(os.environ, {"WRT_PACKAGE": "CONFIG_PACKAGE_squeezelite-custom=y"}), \
             self.assertRaisesRegex(ValueError, "excluded due to upstream Kconfig"):
            build.merge_config(REPO, self.tree, "AP3000M", self.data)

    def test_every_device_critical_package_is_explicitly_configured(self):
        for name in ("AP3000M", "TR3000-512", "C8-688"):
            data = build.profile(REPO, name)
            build.merge_config(REPO, self.tree, name, data)
            # Settings.sh adds the workflow theme after fragment merging.
            theme = "argon" if name == "C8-688" else "aurora"
            with (self.tree / ".config").open("a") as stream:
                stream.write(f"CONFIG_PACKAGE_luci-theme-{theme}=y\nCONFIG_PACKAGE_luci-app-{theme}-config=y\n")
            build.check_config(self.tree, data)

    def test_config_gate_rejects_dropped_package(self):
        with (self.tree / ".config").open("a") as stream:
            stream.write("# CONFIG_PACKAGE_luci-app-fm350 is not set\n")
        with self.assertRaisesRegex(ValueError, "luci-app-fm350"):
            build.check_config(self.tree, self.data)

    def test_config_gate_rejects_extra_device(self):
        with (self.tree / ".config").open("a") as stream:
            stream.write("CONFIG_TARGET_DEVICE_mediatek_filogic_DEVICE_other=y\n")
        with self.assertRaisesRegex(ValueError, "expected only"):
            build.check_config(self.tree, self.data)

    def test_manifest_has_no_squashfs_suffix(self):
        image, manifest = self.verify()
        self.assertEqual(manifest, self.manifest)
        self.assertEqual(image, self.image)

    def test_missing_manifest_cannot_publish(self):
        self.manifest.unlink()
        build.write(self.target / "unrelated.manifest", "luci-app-fm350 - 1.0\n")
        with self.assertRaisesRegex(ValueError, "matching manifest"):
            self.verify()

    def test_upstream_target_manifest_for_single_device(self):
        generic = self.target / "immortalwrt-mediatek-filogic.manifest"
        self.manifest.rename(generic)
        self.assertEqual(self.verify()[1], generic)

    def test_target_manifest_rejects_multiple_selected_devices(self):
        self.manifest.rename(self.target / "immortalwrt-mediatek-filogic.manifest")
        with (self.tree / ".config").open("a") as stream:
            stream.write("CONFIG_TARGET_DEVICE_mediatek_filogic_DEVICE_other=y\n")
        with self.assertRaisesRegex(ValueError, "expected only"):
            self.verify()

    def test_target_manifest_still_requires_packages(self):
        self.manifest.unlink()
        build.write(self.target / "immortalwrt-mediatek-filogic.manifest", "ttyd - 1.0\n")
        with self.assertRaisesRegex(ValueError, "manifest missing"):
            self.verify()

    def test_missing_package_cannot_publish(self):
        build.write(self.manifest, "ttyd - 1.0\n")
        with self.assertRaisesRegex(ValueError, "manifest missing"):
            self.verify()

    def test_kernel_only_tar_cannot_publish(self):
        self.valid_image(parts=("kernel",))
        with self.assertRaisesRegex(ValueError, "kernel and root"):
            self.verify()

    def test_wrong_device_cannot_publish(self):
        self.metadata["supported_devices"] = ["cudy,tr3000-v1"]
        with self.assertRaisesRegex(ValueError, "wrong supported_devices"):
            self.verify()

    def test_new_compatibility_metadata(self):
        self.metadata["new_supported_devices"] = ["airpi,ap3000m"]
        self.metadata["supported_devices"] = ["legacy compatibility message"]
        self.verify()

    def test_wrong_platform_cannot_publish(self):
        self.metadata["version"]["target"] = "x86/64"
        with self.assertRaisesRegex(ValueError, "wrong target"):
            self.verify()

    def test_multiple_sysupgrade_images_cannot_publish(self):
        build.write(self.target / (self.prefix + "-extra-sysupgrade.bin"), "invalid")
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.verify()

    def test_package_retains_provenance_and_checksums(self):
        env = {"WRT_RELEASE_TAG": "AP3000M-test-123-1", "WRT_BRANCH": "feature/test",
               "WRT_INFO": "immortalwrt", "WRT_DATE": "26.10.27-12.00.00", "WRT_REPO": "https://example.test/source",
               "WRT_IP": "192.168.10.1", "WRT_NAME": "AP3000M"}
        for file in ("source-commit.txt", "feeds.buildinfo", "packages.lock.json", "build-inputs.json"):
            build.write(self.reports / file, "test\n")
        with patch.dict(os.environ, env), patch.object(build, "run", return_value="a" * 40), \
             patch.object(build.subprocess, "run", side_effect=self.fwtool), patch.object(build, "verify_payload"):
            build.package(self.tree, REPO, "AP3000M", self.data, self.reports)
        assets = {f.name for f in (self.tree / "upload").iterdir()}
        self.assertIn("sha256sums", assets)
        upload = self.tree / "upload"
        ota = json.loads((upload / "ap3000m-ota-update.json").read_text())
        self.assertEqual(ota["runtime_abi"], "1")
        self.assertEqual(ota["tag"], env["WRT_RELEASE_TAG"])
        import hashlib
        self.assertEqual(ota["sha256"], hashlib.sha256((upload / "ap3000m-ota").read_bytes()).hexdigest())
        self.assertEqual(ota["size"], (upload / "ap3000m-ota").stat().st_size)
        sums = (upload / "sha256sums").read_text()
        self.assertIn("  ap3000m-ota\n", sums)
        self.assertIn("  ap3000m-ota-update.json\n", sums)
        self.assertIn(self.manifest.name, assets)
        self.assertIn("feeds.buildinfo", assets)
        for line in (self.tree / "upload/sha256sums").read_text().splitlines():
            digest, file = line.split("  ", 1)
            self.assertEqual(digest, build.hashlib.sha256((self.tree / "upload" / file).read_bytes()).hexdigest())

    def test_toolchain_cache_ignores_image_date_but_tracks_feed_and_rust(self):
        build.write(self.reports / "packages.lock.json", "{}")
        build.write(self.reports / "host-toolchains.txt", "host toolchain version")
        build.write(self.tree / "feeds/packages/.git", "fixture")
        build.write(self.tree / "os-release", "Ubuntu 24.04")
        output = self.tree / "github-output"
        versions = {"rust": "rustc 1.96.0", "feed": "a" * 40}

        def fake_run(*args, **kwargs):
            if args[0] == "gcc":
                return "13.3.0"
            if args[0] == "rustc":
                return versions["rust"]
            if args[0] == "uname":
                return "x86_64"
            if args[0] == "git":
                return versions["feed"] if Path(kwargs["cwd"]).name == "packages" else "b" * 40
            return "src-git packages https://example.test/feed"

        def fake_path(value):
            return self.tree / "os-release" if str(value) == "/etc/os-release" else Path(value)

        def key():
            output.write_text("")
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}), \
                 patch.object(build, "run", side_effect=fake_run), patch.object(build, "Path", side_effect=fake_path):
                build.cache_keys(REPO, self.tree, "AP3000M", self.reports)
            return dict(line.split("=", 1) for line in output.read_text().splitlines())

        first = key()
        build.write(self.tree / "files/etc/ap3000m-build.tag", "a different run and date")
        self.assertEqual(first, key())
        versions["feed"] = "c" * 40
        changed_feed = key()
        self.assertNotEqual(first["staging"], changed_feed["staging"])
        self.assertEqual(first["ccache"], changed_feed["ccache"])
        versions["rust"] = "rustc 1.97.0"
        changed_rust = key()
        self.assertNotEqual(changed_feed["staging"], changed_rust["staging"])
        self.assertNotEqual(changed_feed["ccache"], changed_rust["ccache"])


class CacheRetentionTests(unittest.TestCase):
    def plan(self, entries):
        env = {"DEVICE_CACHE_PREFIX": "wrt-v2-ap3000m-linux-x64", "GITHUB_REPOSITORY": "owner/repo",
               "GITHUB_REF": "refs/heads/test", "DEVICE_DL_KEY": "wrt-v2-ap3000m-linux-x64-dl-current",
               "DEVICE_CCACHE_KEY": "wrt-v2-ap3000m-linux-x64-ccache-current",
               "DEVICE_STAGING_KEY": "wrt-v2-ap3000m-linux-x64-staging-current"}
        with patch.dict(os.environ, env), patch.object(roll.subprocess, "check_output", return_value=json.dumps(entries)) as listing, \
             patch.object(roll.subprocess, "run") as deleting:
            roll.main()
        self.assertIn("refs/heads/test", listing.call_args.args[0])
        return [call.args[0][3] for call in deleting.call_args_list]

    def test_unrelated_model_cache_is_never_deleted(self):
        entries = [{"id": 1, "key": "wrt-v2-tr3000-512-linux-x64-dl-old", "createdAt": "1"}]
        self.assertEqual(self.plan(entries), [])

    def test_missing_current_cache_keeps_fallback(self):
        entries = [{"id": i, "key": f"wrt-v2-ap3000m-linux-x64-dl-old{i}", "createdAt": str(i)} for i in (1, 2)]
        self.assertEqual(self.plan(entries), [])

    def test_current_and_newer_cache_survive_rollover(self):
        entries = [{"id": i, "key": f"wrt-v2-ap3000m-linux-x64-dl-{suffix}", "createdAt": str(i)}
                   for i, suffix in ((1, "old"), (2, "current"), (3, "newer"))]
        self.assertEqual(self.plan(entries), ["1"])


if __name__ == "__main__":
    unittest.main()
