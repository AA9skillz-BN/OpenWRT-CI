"""Exercise final-image validation with real FIT, DTB and squashfs tools on Linux."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from device_image import verify_payload

TOOLS = ("dtc", "mkimage", "dumpimage", "fdtget", "mksquashfs", "unsquashfs")


@unittest.skipUnless(all(shutil.which(tool) for tool in TOOLS), "Linux image tools required")
class ImageToolTests(unittest.TestCase):
    def test_real_fit_dtb_and_rootfs_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            source = temp / "helper.sh"
            source.write_text("#!/bin/sh\nexit 0\n")
            root = temp / "root"
            (root / "usr/sbin").mkdir(parents=True)
            helper = root / "usr/sbin/device-helper"
            shutil.copy2(source, helper)
            helper.chmod(0o755)
            (root / "etc/rc.d").mkdir(parents=True)
            (root / "etc/rc.d/S99helper").symlink_to("../../usr/sbin/device-helper")
            (root / "etc/device-build.tag").write_text("fixture-tag\n")
            (temp / "device.dts").write_text('''/dts-v1/;
/ {
 compatible = "cudy,tr3000-v1", "mediatek,mt7981";
 partitions {
  #address-cells = <1>;
  #size-cells = <1>;
  partition@5c0000 { label = "ubi"; reg = <0x5c0000 0x1da40000>; };
 };
};
''')
            (temp / "kernel.bin").write_bytes(b"fixture kernel")
            (temp / "image.its").write_text('''/dts-v1/;
/ {
 description = "image validation fixture";
 #address-cells = <1>;
 images {
  kernel-1 {
   description = "kernel";
   data = /incbin/("kernel.bin");
   type = "kernel"; arch = "arm64"; os = "linux"; compression = "none";
   load = <0x40000000>; entry = <0x40000000>;
  };
  fdt-1 {
   description = "device tree";
   data = /incbin/("device.dtb");
   type = "flat_dt"; arch = "arm64"; compression = "none";
  };
 };
 configurations {
  default = "config-1";
  config-1 { description = "fixture"; kernel = "kernel-1"; fdt = "fdt-1"; };
 };
};
''')
            for command in (["dtc", "-I", "dts", "-O", "dtb", "-o", "device.dtb", "device.dts"],
                            ["mkimage", "-f", "image.its", "kernel.itb"],
                            ["mksquashfs", "root", "root.squashfs", "-noappend", "-processors", "1", "-no-progress"]):
                subprocess.run(command, cwd=temp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            image = temp / "sysupgrade.bin"
            with tarfile.open(image, "w") as archive:
                archive.add(temp / "kernel.itb", arcname="sysupgrade-device/kernel")
                archive.add(temp / "root.squashfs", arcname="sysupgrade-device/root")
            data = {"supported_device": "cudy,tr3000-v1", "ubi_reg": [0x5c0000, 0x1da40000],
                    "build_tag_file": "/etc/device-build.tag", "required_files": [
                        {"path": "/usr/sbin/device-helper", "source": "helper.sh", "executable": True},
                        {"path": "/etc/rc.d/S99helper", "executable": True}]}
            with tarfile.open(image) as archive, patch.dict(os.environ, {"WRT_RELEASE_TAG": "fixture-tag"}):
                verify_payload(archive, *archive.getmembers(), data, temp, temp)
            self.assertTrue((temp / "verified-payload.json").is_file())
            # The adapted platform file must be the exact bytes in squashfs.
            adapted = temp / "adapted/helper.sh"
            adapted.parent.mkdir()
            shutil.copy2(source, adapted)
            data["required_files"][0]["build_source"] = "adapted/helper.sh"
            with tarfile.open(image) as archive, patch.dict(os.environ, {"WRT_RELEASE_TAG": "fixture-tag"}):
                verify_payload(archive, *archive.getmembers(), data, temp, temp, tree=temp)
            adapted.write_text("changed after source preparation\n")
            with tarfile.open(image) as archive, patch.dict(os.environ, {"WRT_RELEASE_TAG": "fixture-tag"}), \
                 self.assertRaisesRegex(ValueError, "differs from adapted source"):
                verify_payload(archive, *archive.getmembers(), data, temp, temp, tree=temp)
            del data["required_files"][0]["build_source"]
            data["required_files"][0]["elf_machine"] = 183
            with tarfile.open(image) as archive, patch.dict(os.environ, {"WRT_RELEASE_TAG": "fixture-tag"}), \
                 self.assertRaisesRegex(ValueError, "wrong ELF architecture"):
                verify_payload(archive, *archive.getmembers(), data, temp, temp)
            del data["required_files"][0]["elf_machine"]
            with tarfile.open(image) as archive, patch.dict(os.environ, {"WRT_RELEASE_TAG": "wrong-build"}), \
                 self.assertRaisesRegex(ValueError, "build tag differs"):
                verify_payload(archive, *archive.getmembers(), data, temp, temp)


if __name__ == "__main__":
    unittest.main()
