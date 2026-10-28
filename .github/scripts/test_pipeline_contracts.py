"""Regression checks for credentials, source snapshots and final image content."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import yaml
import device_image

REPO = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), Path(__file__).with_name(name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


sources = module("device-sources")
defaults = module("tr3000-defaults")


class PipelineTests(unittest.TestCase):
    def test_cache_maintenance_cannot_gate_verified_firmware_publication(self):
        data = yaml.safe_load((REPO / ".github/workflows/DEVICE-CORE.yml").read_text(encoding="utf-8"))
        steps = {step.get("name"): step for step in data["jobs"]["core"]["steps"]}
        for name in ("Seal Installed Toolchain Cache", "Save Exact Toolchain Cache", "Save Downloads",
                     "Save Compiler Cache", "Report Toolchain Cache Reuse", "Restore Exact Toolchain Cache"):
            self.assertTrue(steps[name]["continue-on-error"], name)
        self.assertNotIn("continue-on-error", steps["Verify and Package Device Firmware"])
        self.assertIn("steps.device-dl.outputs.cache-hit != 'true'", steps["Save Downloads"]["if"])
        self.assertEqual(steps["Restore Downloads"]["with"]["key"], "${{steps.device-cache.outputs.dl}}")
        self.assertEqual(steps["Save Downloads"]["with"]["key"], "${{steps.device-cache.outputs.dl}}")
        self.assertIn("bad_toolchain_key", data["jobs"]["device-cache-rollover"]["if"])

    def test_builder_has_no_publication_credentials(self):
        data = yaml.safe_load((REPO / ".github/workflows/DEVICE-CORE.yml").read_text(encoding="utf-8"))
        builder = data["jobs"]["core"]
        self.assertEqual(builder["permissions"], {"contents": "read"})
        self.assertNotIn("GITHUB_TOKEN", data["env"])
        initialize = next(step for step in builder["steps"] if step.get("id") == "initialize-values")["run"]
        self.assertIn("$GITHUB_REF\" != 'refs/heads/main'", initialize)
        self.assertIn('WRT_RELEASE_TAG="preview-$WRT_RELEASE_TAG"', initialize)
        for step in builder["steps"]:
            self.assertNotIn("secrets.GITHUB_TOKEN", json.dumps(step))
            if "checkout@" in step.get("uses", ""):
                self.assertFalse(step["with"]["persist-credentials"])
        publisher = data["jobs"]["publish"]
        self.assertEqual(publisher["permissions"], {"contents": "write"})
        self.assertFalse(any("checkout@" in step.get("uses", "") for step in publisher["steps"]))
        release = next(step for step in publisher["steps"] if step.get("id") == "release")
        self.assertEqual(release["with"]["prerelease"], "${{ github.ref != 'refs/heads/main' }}")
        self.assertIn("inputs.WRT_PUBLISH", publisher["if"])

    def test_every_device_uses_isolated_core(self):
        for name in ("TR3000", "AP3000M", "C8-688"):
            data = yaml.safe_load((REPO / f".github/workflows/{name}.yml").read_text(encoding="utf-8"))
            self.assertTrue(all(job["uses"].endswith("/DEVICE-CORE.yml") for job in data["jobs"].values()))

    def test_version_lock_does_not_follow_a_different_source_silently(self):
        with self.assertRaisesRegex(ValueError, "source differs"):
            sources.resolve(REPO, "AP3000M", "https://github.com/openwrt/openwrt.git", "main", "locked")

    def test_feed_lock_replaces_branch_with_exact_commit(self):
        lock = {"packages": {"url": "https://example.test/packages.git", "commit": "a" * 40}}
        result = sources.feed_config("src-git packages https://example.test/packages.git;master\n"
                                     "src-git video https://example.test/video\n", lock)
        self.assertEqual(result, "src-git packages https://example.test/packages.git^" + "a" * 40 + "\n")
        with self.assertRaisesRegex(ValueError, "feed set"):
            sources.feed_config("", lock)
        with self.assertRaisesRegex(ValueError, "URL differs"):
            sources.feed_config("src-git packages https://wrong.test/packages.git", lock)

    def test_every_source_and_feed_is_locked_to_a_full_commit(self):
        data = json.loads((REPO / "Profiles/sources.lock.json").read_text())
        self.assertEqual(set(data), {"TR3000-512", "AP3000M", "C8-688"})
        for item in data.values():
            sources.sha(item["commit"])
            self.assertTrue(item["feeds"])
            for feed in item["feeds"].values():
                sources.sha(feed["commit"])

    def test_tr3000_defaults_do_not_reconfigure_restored_user_settings(self):
        for file in ("99-tr3000-local-settings", "99-tr3000-flow-offload"):
            self.assertFalse((REPO / "Files/TR3000-512/etc/uci-defaults" / file).exists())
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "turboacc"
            config.write_text("config turboacc 'config'\n" + "".join(
                f"\toption {key} '0'\n" for key in ("sw_flow", "hw_flow", "sfe_flow", "hw_wed", "bbr_cca", "fullcone_nat")))
            defaults.configure(config)
            self.assertIn("option hw_flow '1'", config.read_text())
            self.assertIn("option sfe_flow '0'", config.read_text())

    def test_wrong_final_ubi_partition_is_rejected(self):
        data = {"supported_device": "cudy,tr3000-v1", "ubi_reg": [0x5c0000, 0x1da40000]}
        def fdt(*args):
            if "-l" in args:
                return "partition@5c0000" if args[-1] == "/" else ""
            return {"compatible": "cudy,tr3000-v1 mediatek,mt7981", "label": "ubi",
                    "reg": "5c0000 4000000"}[args[-1]]
        with patch.object(device_image, "output", side_effect=fdt), self.assertRaisesRegex(ValueError, "wrong UBI"):
            device_image.check_dtb(Path("fixture.dtb"), data)

    def test_correct_final_ubi_partition_is_accepted(self):
        data = {"supported_device": "cudy,tr3000-v1", "ubi_reg": [0x5c0000, 0x1da40000]}
        def fdt(*args):
            if "-l" in args:
                return "partition@5c0000" if args[-1] == "/" else ""
            return {"compatible": "cudy,tr3000-v1 mediatek,mt7981", "label": "ubi",
                    "reg": "5c0000 1da40000"}[args[-1]]
        with patch.object(device_image, "output", side_effect=fdt):
            self.assertIn("cudy,tr3000-v1", device_image.check_dtb(Path("fixture.dtb"), data))

    def test_bbr_service_respects_saved_disabled_setting(self):
        import shutil
        import subprocess
        bash = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else None)
        if not bash:
            self.skipTest("bash unavailable")
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "bbr.sh"
            service = (REPO / "Files/TR3000-512/etc/init.d/tr3000-bbr").read_text(encoding="utf-8")
            script.write_text(service + '\nuci() { printf 0; }\nmodprobe() { exit 91; }\nstart\n', encoding="utf-8", newline="\n")
            result = subprocess.run([bash, script.as_posix()], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_c8_bootargs_must_use_second_system(self):
        data = {"supported_device": "nradio,c8-688", "root_partition": "rootfs_2nd"}
        def fdt(*args):
            return "nradio,c8-688" if args[-1] == "compatible" else "root=PARTLABEL=rootfs"
        with patch.object(device_image, "output", side_effect=fdt), self.assertRaisesRegex(ValueError, "wrong root"):
            device_image.check_dtb(Path("fixture.dtb"), data)

    def test_missing_script_in_final_rootfs_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "image.tar"
            with tarfile.open(image, "w") as archive:
                for name in ("kernel", "root"):
                    item = tarfile.TarInfo("sysupgrade-device/" + name)
                    item.size = 4
                    archive.addfile(item, io.BytesIO(b"test"))
            data = {"required_files": [{"path": "/usr/sbin/missing"}], "build_tag_file": "/etc/build.tag"}
            answers = ["config-1", "fdt-1", "kernel-1 fdt-1"]
            with tarfile.open(image) as archive, patch.object(device_image, "output", side_effect=answers), \
                 patch.object(device_image, "check_dtb", return_value=["device"]), \
                 patch.object(device_image.subprocess, "run"), self.assertRaisesRegex(ValueError, "missing required file"):
                device_image.verify_payload(archive, *archive.getmembers(), data, REPO, root)


if __name__ == "__main__":
    unittest.main()
