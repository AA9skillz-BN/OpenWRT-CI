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
        with patch.object(build.subprocess, "run", side_effect=self.fwtool):
            return build.verify_image(self.tree, self.data, self.reports)

    def test_feature_fragments_preserve_existing_general_choices(self):
        for name in ("AP3000M", "TR3000-512"):
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
             patch.object(build.subprocess, "run", side_effect=self.fwtool):
            build.package(self.tree, REPO, "AP3000M", self.data, self.reports)
        assets = {f.name for f in (self.tree / "upload").iterdir()}
        self.assertIn("sha256sums", assets)
        self.assertIn(self.manifest.name, assets)
        self.assertIn("feeds.buildinfo", assets)
        for line in (self.tree / "upload/sha256sums").read_text().splitlines():
            digest, file = line.split("  ", 1)
            self.assertEqual(digest, build.hashlib.sha256((self.tree / "upload" / file).read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
