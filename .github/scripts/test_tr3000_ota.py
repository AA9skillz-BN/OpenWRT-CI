"""TR3000 OTA executes the real guard/CLI/installer with isolated device I/O."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
OTA_DIR = REPO / "Scripts/TR3000-512/ota"
LIB = (OTA_DIR / "tr3000-system.sh").read_text(encoding="utf8")
CLI = (OTA_DIR / "tr3000-ota").read_text(encoding="utf8").rsplit('\nmain "$@"', 1)[0].replace('. /lib/functions/tr3000-system.sh || exit 1', '')
BASH = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else None)
JQ = os.environ.get("OTA_TEST_JQ") or shutil.which("jq")
TAG = "TR3000-512-VIKINGYFY-owrt-test"
NAME = "immortalwrt-mediatek-filogic-cudy_tr3000-v1-squashfs-sysupgrade-VIKINGYFY-test.bin"


@unittest.skipUnless(BASH and JQ, "Bash and jq required")
class TrOtaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, text in {
            "board": "cudy,tr3000-v1\n", "mtd": 'dev: size erasesize name\nmtd5: 1da40000 00020000 "ubi"\n',
            "mtdsys/mtd5/size": "497287168\n", "mtdsys/mtd5/erasesize": "131072\n", "mtdsys/mtd5/writesize": "2048\n",
            "ubisys/ubi0/mtd_num": "5\n", "ubisys/ubi0_1/name": "rootfs\n",
            "mounts": "/dev/ubiblock0_1 /rom squashfs ro 0 0\nubi0:rootfs_data /overlay ubifs rw 0 0\n",
            "mountinfo": "42 1 254:1 / /rom ro - squashfs /dev/root ro\n",
            "blocksys/ubiblock0_1/dev": "254:1\n",
            "current": "TR3000-512-VIKINGYFY-owrt-old\n", "guard": LIB, "abi": "1\n",
        }.items():
            file = self.root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(text, encoding="utf8", newline="\n")
        (self.root / "dt/part").mkdir(parents=True)
        (self.root / "dt/part/label").write_bytes(b"ubi\0")
        (self.root / "dt/part/reg").write_bytes(bytes.fromhex("005c00001da40000"))

    def contract(self, **changes):
        value = {"schema": 1, "model": "tr3000-512", "board": "cudy,tr3000-v1", "layout": "ubi-005c0000-1da40000",
                 "ubi_reg": [0x5c0000, 0x1da40000], "format": "sysupgrade-tar", "tag": TAG, "asset": NAME,
                 "size": 4, "sha256": hashlib.sha256(b"good").hexdigest()}
        value.update(changes)
        raw = json.dumps(value).encode()
        (self.root / "descriptor").write_bytes(raw)
        return raw

    def release(self, **changes):
        raw = self.contract()
        value = {"tag_name": TAG, "draft": False, "prerelease": False, "id": 1, "assets": [
            {"name": NAME, "size": 4, "digest": "sha256:" + hashlib.sha256(b"good").hexdigest()},
            {"name": "tr3000-ota-image.json", "size": len(raw), "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}]}
        value.update(changes)
        return value

    def shell(self, body, api=None, shell=None):
        fixture = '''
TR_BOARD="$TASK_TMP/board"; TR_MTD="$TASK_TMP/mtd"; TR_DT="$TASK_TMP/dt"
TR_SYS_MTD="$TASK_TMP/mtdsys"; TR_SYS_UBI="$TASK_TMP/ubisys"; TR_MOUNTS="$TASK_TMP/mounts"
TR_MOUNTINFO="$TASK_TMP/mountinfo"; TR_SYS_BLOCK="$TASK_TMP/blocksys"
BUILD_TAG_FILE="$TASK_TMP/current"; PENDING_FILE="$TASK_TMP/pending"; RESULT_FILE="$TASK_TMP/result"
DL_TAG_FILE="$TASK_TMP/dl.tag"; DL_RESULT_FILE="$TASK_TMP/dl.result"
CACHE_FILE="$TASK_TMP/check.json"; OP_FILE="$TASK_TMP/op.json"; OP_LOG="$TASK_TMP/op.log"
TR_GUARD="$TASK_TMP/guard"; RUNTIME_ABI_FILE="$TASK_TMP/abi"; OTA_SCRIPT="$TASK_TMP/installed"
LOCK="$TASK_TMP/ota.lock"
jq() { "$OTA_TEST_JQ" "$@"; }
cfg() { return 1; }; need_tools() { :; }; work_dir() { printf '%s' "$TASK_TMP"; }
fetch_api() { printf '%s' "$API_JSON"; }
free_kb() { echo 999999; }; mirror_list() { :; }; sync() { :; }
sysupgrade() {
 printf '%s\\n' "$*" >> "$TASK_TMP/sysupgrade"
 case "$1" in -T) [ ! -f "$TASK_TMP/bad-image" ];; -b) [ ! -f "$TASK_TMP/bad-backup" ];; esac
}
curl() {
 local dest='' url=''
 while [ "$#" -gt 0 ]; do if [ "$1" = -o ]; then shift; dest="$1"; else url="$1"; fi; shift; done
 printf '%s\\n' "$url" >> "$TASK_TMP/download-urls"
 case "$url" in
  */tr3000-ota-image.json) cp "$TASK_TMP/descriptor" "$dest";;
  */tr3000-ota-update.json) cp "$TASK_TMP/update-meta" "$dest";;
  */tr3000-ota) cp "$TASK_TMP/candidate" "$dest";;
  *) if [ -f "$TASK_TMP/short" ]; then printf bad > "$dest"; elif [ -f "$TASK_TMP/corrupt" ]; then printf evil > "$dest"; else printf good > "$dest"; fi;;
 esac
}
'''
        prefix = 'TASK_TMP=$(cygpath -u "$TASK_TMP")\n' if os.name == "nt" else ""
        script = self.root / "test.sh"
        script.write_text(prefix + LIB + "\n" + CLI + "\n" + fixture + body, encoding="utf8", newline="\n")
        return subprocess.run((shell or [BASH]) + [script.as_posix()], text=True, capture_output=True, encoding="utf8",
                              env=dict(os.environ, TASK_TMP=self.root.as_posix(), OTA_TEST_JQ=Path(JQ).as_posix(), API_JSON=json.dumps(api or [])), timeout=30)

    def test_running_layout_checks_dtb_mtd_geometry_and_active_root(self):
        self.assertEqual(self.shell("tr_check_layout").returncode, 0)
        for path, bad in [("board", b"cudy,tr3000-v1-ubootmod"), ("mtd", b'mtd5: 04000000 00020000 "ubi"\n'),
                          ("mtdsys/mtd5/writesize", b"4096"), ("dt/part/reg", bytes.fromhex("005c000004000000")),
                          ("ubisys/ubi0/mtd_num", b"6"), ("ubisys/ubi0_1/name", b"kernel"),
                          ("mounts", b"rootfs / rootfs rw 0 0\n")]:
            with self.subTest(path=path):
                file = self.root / path
                original = file.read_bytes()
                file.write_bytes(bad)
                result = self.shell("cmd_upgrade --yes", [self.release()])
                file.write_bytes(original)
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertFalse((self.root / "sysupgrade").exists())
                self.assertFalse((self.root / "pending").exists())

    def test_root_alias_resolves_actual_mount_device_and_ubi_owner(self):
        for source in ("/dev/root", "ubi0:rootfs"):
            self.root.joinpath("mounts").write_text(f"{source} /rom squashfs ro 0 0\n")
            result = self.shell("tr_check_layout")
            self.assertEqual(result.returncode, 0, result.stderr)
        self.root.joinpath("mounts").write_text("/dev/root /rom squashfs ro 0 0\n")
        self.root.joinpath("blocksys/ubiblock0_1/dev").write_text("254:2\n")
        self.assertNotEqual(self.shell("tr_check_layout").returncode, 0)
        self.root.joinpath("blocksys/ubiblock0_1/dev").write_text("254:1\n")
        self.root.joinpath("ubisys/ubi0/mtd_num").write_text("6\n")
        self.assertNotEqual(self.shell("tr_check_layout").returncode, 0)
        self.assertFalse(self.root.joinpath("sysupgrade").exists())

    def test_channel_requires_single_correct_image_and_512_descriptor(self):
        good = self.release()
        bads = [dict(good, tag_name="AP3000M-immortalwrt-master-test"), dict(good, tag_name="TR3000-512-immortalwrt-master-test"),
                dict(good, draft=True), dict(good, prerelease=True), dict(good, assets=good["assets"][:1]),
                dict(good, assets=good["assets"] * 2), dict(good, assets=[dict(good["assets"][0], name="cudy_tr3000-v1-ubootmod-sysupgrade.bin"), good["assets"][1]])]
        for release in bads:
            result = self.shell('fetch_api | parse_tag ""', [release])
            self.assertEqual(result.stdout.strip(), "", result.stderr)
        self.assertTrue(self.shell('fetch_api | parse_tag ""', [good]).stdout.startswith(TAG + "\t"))

    def test_512_descriptor_digest_and_fields_are_mandatory(self):
        for changes in ({"ubi_reg": [0x5c0000, 0x4000000]}, {"model": "ap3000m"}, {"asset": "wrong.bin"}, {"sha256": "0" * 64}, {"tag": TAG + "-other"}):
            release = self.release()
            raw = self.contract(**changes)
            release["assets"][1].update(size=len(raw), digest="sha256:" + hashlib.sha256(raw).hexdigest())
            result = self.shell("cmd_download", [release])
            self.assertNotEqual(result.returncode, 0, changes)
            self.assertFalse((self.root / (TAG + ".bin")).exists())
        release = self.release()
        (self.root / "descriptor").write_bytes(b"evil")
        self.assertNotEqual(self.shell("cmd_download", [release]).returncode, 0)

    def test_download_offline_keep_wipe_and_repeated_digest_checks(self):
        result = self.shell("cmd_download", [self.release()])
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ("--keep", "--wipe"):
            result = self.shell(f'fetch_api() {{ return 1; }}\ncmd_upgrade {flag} --yes --tag {TAG}')
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = (self.root / "sysupgrade").read_text().splitlines()
            self.assertTrue(lines[-3].startswith("-T "))
            self.assertTrue(lines[-2].startswith("-b "))
            self.assertEqual(lines[-1].startswith("-n "), flag == "--wipe")
            self.assertEqual((self.root / "pending").read_text().strip(), TAG)
        (self.root / "pending").unlink()
        (self.root / "sysupgrade").unlink()
        (self.root / (TAG + ".bin")).write_bytes(b"evil")
        result = self.shell(f'cmd_upgrade --yes --tag {TAG}')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "sysupgrade").exists())
        self.assertFalse((self.root / "pending").exists())

    def test_image_test_backup_failure_and_unverified_local_file_never_flash(self):
        for fail in ("bad-image", "bad-backup"):
            self.root.joinpath(fail).touch()
            result = self.shell("cmd_upgrade --yes", [self.release()])
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.root / "pending").exists())
            lines = (self.root / "sysupgrade").read_text().splitlines()
            self.assertTrue(all(line.startswith(("-T ", "-b ")) for line in lines))
            self.root.joinpath(fail).unlink()
            self.root.joinpath("sysupgrade").unlink()
        result = self.shell('cmd_upgrade --image /tmp/unverified.bin --yes')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "sysupgrade").exists())

    def test_failed_inline_upgrade_download_clears_progress_for_retry(self):
        self.root.joinpath("short").touch()
        result = self.shell("run_operation upgrade --yes", [self.release()])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.root.joinpath("dl.tag").exists())
        self.assertFalse(self.root.joinpath(TAG + ".bin.part").exists())
        self.assertEqual(json.loads(self.root.joinpath("op.json").read_bytes())["state"], "failed")
        self.assertFalse(self.root.joinpath("sysupgrade").exists())
        self.root.joinpath("short").unlink()
        result = self.shell("run_operation upgrade --yes", [self.release()])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_bad_download_and_legacy_metadata_cannot_become_valid_offline_copy(self):
        self.root.joinpath("corrupt").touch()
        result = self.shell("cmd_download", [self.release()])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / (TAG + ".bin.json")).exists())
        self.root.joinpath("corrupt").unlink()
        result = self.shell("cmd_download", [self.release()])
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = self.root / (TAG + ".bin.json")
        value = json.loads(meta.read_bytes()); value.pop("layout")
        meta.write_text(json.dumps(value))
        self.assertEqual(self.shell(f"verified_local {TAG}").returncode, 2)

    def test_boot_result_and_guard_status_are_read_only(self):
        self.root.joinpath("pending").write_text(TAG)
        self.root.joinpath("current").write_text(TAG)
        result = self.shell("cmd_boot_hook; cmd_ui_status")
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertTrue(value["ota_ready"])
        self.assertTrue(value["last_result"].startswith("success " + TAG))
        self.assertFalse(self.root.joinpath("pending").exists())
        self.assertFalse(self.root.joinpath("sysupgrade").exists())

    @unittest.skipIf(os.name == "nt", "actual Linux flock and BusyBox ash")
    def test_shared_lock_background_failure_and_busybox_guard(self):
        result = self.shell('acquire_lock\n(exec 9>&-; main download) && exit 90\nexit 0')
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.shell('background_operation upgrade --image /tmp/rejected\nwait\ncat "$OP_FILE"')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout[result.stdout.index("{"):])["state"], "failed")
        self.assertEqual(self.shell("tr_check_layout", shell=[shutil.which("busybox"), "ash"]).returncode, 0)

    def update_fixture(self, **changes):
        script = b'#!/bin/sh\nexit 0\n'
        self.root.joinpath("candidate").write_bytes(script)
        self.root.joinpath("installed").write_bytes(b"old CLI\n")
        info = {"schema": 1, "model": "tr3000-512", "runtime_abi": "1", "tag": TAG,
                "asset": "tr3000-ota", "size": len(script), "sha256": hashlib.sha256(script).hexdigest(),
                "guard_sha256": hashlib.sha256(self.root.joinpath("guard").read_bytes()).hexdigest(), "requires": ["sh"]}
        info.update(changes)
        raw = json.dumps(info).encode(); self.root.joinpath("update-meta").write_bytes(raw)
        value = self.release()
        value["assets"] = [{"name": "tr3000-ota-update.json", "size": len(raw), "digest": "sha256:" + hashlib.sha256(raw).hexdigest()},
                           {"name": "tr3000-ota", "size": len(script), "digest": "sha256:" + hashlib.sha256(script).hexdigest()}]
        return value

    def test_self_update_requires_abi_guard_digest_and_valid_release_bytes(self):
        for changes in ({"runtime_abi": "2"}, {"guard_sha256": "0" * 64}, {"requires": ["missing_dependency_999"]}):
            result = self.shell("acquire_lock() { :; }; cmd_self_update", [self.update_fixture(**changes)])
            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.root.joinpath("installed").read_bytes(), b"old CLI\n")
            self.assertFalse(self.root.joinpath("installed.bak").exists())
        release = self.update_fixture()
        result = self.shell("acquire_lock() { :; }; cmd_self_update", [release])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.root.joinpath("installed.bak").read_bytes(), b"old CLI\n")
        self.assertEqual(self.root.joinpath("installed").read_bytes(), self.root.joinpath("candidate").read_bytes())


class TrOtaInstallationTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "actual shell hook and Make installer run on Linux")
    def test_hook_and_actual_make_install_match_every_runtime_image_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory)
            (top / "package").mkdir()
            for _ in range(2):
                result = subprocess.run([BASH, str(OTA_DIR / "install.sh")], cwd=top,
                                        env=dict(os.environ, GITHUB_WORKSPACE=str(REPO)), text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            base = top / "package/base-files/files"
            lines = (base / "etc/sysupgrade.conf").read_text().splitlines()
            self.assertEqual(lines, ["/etc/tr3000-pending", "/etc/tr3000-result"])
            (top / "include").mkdir()
            (top / "include/package.mk").touch()
            (top / "rules.mk").write_text("INCLUDE_DIR:=$(TOPDIR)/include\nINSTALL_DIR:=install -d\nINSTALL_DATA:=install -m 0644\n")
            probe = top / "probe.mk"
            probe.write_text("include Makefile\ninstall:\n\t$(call Package/luci-app-tr3000-ota/install,$(TOPDIR)/package/base-files/files)\n")
            result = subprocess.run(["make", "-f", str(probe), "install", f"TOPDIR={top}"],
                                    cwd=top / "package/luci-app-tr3000-ota", text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            profile = json.loads((REPO / "Profiles/TR3000-512.json").read_bytes())
            for item in profile["required_files"]:
                if "Scripts/TR3000-512/ota/" in item.get("source", "") or "Scripts/TR3000-512/luci-app-tr3000-ota/" in item.get("source", ""):
                    file = base / item["path"].lstrip("/")
                    self.assertEqual(file.read_bytes(), (REPO / item["source"]).read_bytes(), item["path"])
                    if item.get("executable"):
                        self.assertTrue(file.stat().st_mode & 0o111, item["path"])
            self.assertEqual((base / "etc/rc.d/S99tr3000-ota-boot").readlink().as_posix(), "../init.d/tr3000-ota-boot")

    def test_release_attachments_bind_verified_layout_image_and_guard(self):
        spec = importlib.util.spec_from_file_location("tr_build", Path(__file__).with_name("device-build.py"))
        build = importlib.util.module_from_spec(spec); spec.loader.exec_module(build)
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory)
            image = top / NAME; image.write_bytes(b"actual accepted artifact bytes")
            verified = {"dtb_compatible": ["cudy,tr3000-v1"], "ubi_reg": [0x5c0000, 0x1da40000]}
            report = top / "verified-payload.json"; report.write_text(json.dumps(verified))
            data = {"supported_device": "cudy,tr3000-v1"}
            build.package_tr3000_contract(top, image, TAG, data, top)
            meta = json.loads((top / "tr3000-ota-image.json").read_bytes())
            self.assertEqual(meta["sha256"], hashlib.sha256(image.read_bytes()).hexdigest())
            self.assertEqual((meta["tag"], meta["asset"], meta["size"]), (TAG, NAME, image.stat().st_size))
            verified["ubi_reg"] = [0x5c0000, 0x4000000]; report.write_text(json.dumps(verified))
            with self.assertRaisesRegex(ValueError, "verified actual 512MB"):
                build.package_tr3000_contract(top, image, TAG, data, top)
            build.package_ota_update(REPO, top, TAG, "TR3000-512")
            info = json.loads((top / "tr3000-ota-update.json").read_bytes())
            self.assertEqual(info["model"], "tr3000-512")
            self.assertEqual(info["guard_sha256"], hashlib.sha256((OTA_DIR / "tr3000-system.sh").read_bytes()).hexdigest())
            self.assertEqual(info["sha256"], hashlib.sha256((top / "tr3000-ota").read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
