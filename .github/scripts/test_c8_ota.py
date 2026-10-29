"""C8 OTA/boot selection: execute real shell logic; mock only device/network I/O."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else None)
JQ = os.environ.get("OTA_TEST_JQ") or shutil.which("jq")
LIB = (REPO / "Files/C8-688/lib/functions/c8-system.sh").read_text(encoding="utf-8")
OTA = (REPO / "Scripts/C8-688/ota/c8-688-ota").read_text(encoding="utf-8")
OTA = OTA.rsplit('\nmain "$@"', 1)[0].replace('. /lib/functions/c8-system.sh || exit 1', '')
TAG = "C8-688-immortalwrt-master-test"


@unittest.skipUnless(BASH and JQ, "bash and jq required")
class C8OtaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "board").write_text("nradio,c8-688\n")
        (self.root / "cmdline").write_text("console=ttyS0 root=PARTLABEL=rootfs_2nd rootwait\n")
        (self.root / "envconf").write_text("/dev/mmcblk0p2 0x0 0x8000\n")
        (self.root / "boot").write_text("1")
        for n, label in ((2, "u-boot-env"), (8, "kernel_2nd"), (9, "rootfs_2nd")):
            (self.root / f"block/mmcblk0p{n}").mkdir(parents=True)
            (self.root / f"block/mmcblk0p{n}/uevent").write_text(f"PARTNAME={label}\n")
            (self.root / f"mmcblk0p{n}").touch()

    def shell(self, body, api=None, library=False):
        source = LIB.replace('[ -b "$C8_DEV/${part%%:*}" ]', '[ -e "$C8_DEV/${part%%:*}" ]')
        fixture = '''
C8_BOARD="$TASK_TMP/board"; C8_CMDLINE="$TASK_TMP/cmdline"
C8_BLOCK="$TASK_TMP/block"; C8_DEV="$TASK_TMP"; C8_ENV_CONF="$TASK_TMP/envconf"
C8_LOCK="$TASK_TMP/system.lock"; LOCK="$C8_LOCK"
BUILD_TAG_FILE="$TASK_TMP/current"; PENDING_FILE="$TASK_TMP/pending"; RESULT_FILE="$TASK_TMP/result"
DL_TAG_FILE="$TASK_TMP/dl.tag"; DL_RESULT_FILE="$TASK_TMP/dl.result"
CACHE_FILE="$TASK_TMP/check.json"; OP_FILE="$TASK_TMP/op.json"; OP_LOG="$TASK_TMP/op.log"
jq() { "$OTA_TEST_JQ" "$@"; }
cfg() { return 1; }; need_tools() { :; }; work_dir() { printf '%s' "$TASK_TMP"; }
fetch_api() { printf '%s' "$API_JSON"; }
free_kb() { echo 999999; }; mirror_list() { :; }
fw_printenv() { cat "$TASK_TMP/boot"; }
fw_setenv() { printf '%s' "$*" >> "$TASK_TMP/env-written"; for last do :; done; printf '%s' "$last" > "$TASK_TMP/boot"; }
sysupgrade() { printf '%s\\n' "$*" >> "$TASK_TMP/sysupgrade"; [ "$1" != -T ] || [ ! -f "$TASK_TMP/bad-image" ]; }
reboot() { touch "$TASK_TMP/rebooted"; }; sync() { :; }; sleep() { :; }
curl() { while [ "$#" -gt 0 ]; do if [ "$1" = -o ]; then shift; printf good > "$1"; return; fi; shift; done; return 1; }
'''
        script = self.root / "test.sh"
        prefix = 'TASK_TMP=$(cygpath -u "$TASK_TMP")\n' if os.name == "nt" else ""
        script.write_text(prefix + source + ("\n" + OTA if not library else "") + fixture + body,
                          encoding="utf-8", newline="\n")
        return subprocess.run([BASH, script.as_posix()], text=True, capture_output=True, encoding="utf-8",
                              env=dict(os.environ, TASK_TMP=self.root.as_posix(), OTA_TEST_JQ=Path(JQ).as_posix(),
                                       API_JSON=json.dumps(api or [])), timeout=30)

    def release(self, **overrides):
        data = {"tag_name": TAG, "draft": False, "prerelease": False, "id": 1,
                "assets": [{"name": "immortalwrt-nradio_c8-688-squashfs-sysupgrade.bin", "size": 4,
                            "digest": "sha256:" + hashlib.sha256(b"good").hexdigest()}]}
        data.update(overrides)
        return data

    def test_channel_and_exact_device_image_filter(self):
        for changes in ({"tag_name": "AP3000M-immortalwrt-master-test"}, {"prerelease": True}, {"draft": True},
                        {"assets": [{"name": "nradio_c8-668gl-sysupgrade.bin"}]},
                        {"assets": self.release()["assets"] * 2}):
            result = self.shell('fetch_api | parse_tag ""\n', [self.release(**changes)])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "")
        result = self.shell('fetch_api | parse_tag ""\n', [self.release()])
        self.assertTrue(result.stdout.startswith(TAG + "\t"), result.stderr)

    def test_download_and_offline_upgrade_revalidate(self):
        result = self.shell('cmd_download\n', [self.release()])
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.shell(f'fetch_api() {{ return 1; }}\ncmd_upgrade --keep --yes --tag {TAG}\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = (self.root / "sysupgrade").read_text().splitlines()
        self.assertTrue(lines[0].startswith("-T "))
        self.assertEqual((self.root / "pending").read_text().strip(), TAG)
        self.assertFalse((self.root / "env-written").exists())
        (self.root / "sysupgrade").unlink()
        (self.root / "pending").unlink()
        (self.root / (TAG + ".bin")).write_bytes(b"evil")
        result = self.shell(f'cmd_upgrade --keep --yes --tag {TAG}\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "sysupgrade").exists())
        self.assertFalse((self.root / "pending").exists())

    def test_bad_device_test_never_flashes_or_sets_pending(self):
        (self.root / "bad-image").touch()
        result = self.shell('cmd_upgrade --keep --yes\n', [self.release()])
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "pending").exists())
        self.assertEqual(len((self.root / "sysupgrade").read_text().splitlines()), 1)

    def test_platform_env_and_staged_first_system_refuse_upgrade(self):
        cases = [("board", "nradio,c8-668gl"), ("cmdline", "root=PARTLABEL=rootfs"),
                 ("cmdline", "root=PARTLABEL=rootfs_2nd root=/dev/mmcblk0p7"),
                 ("block/mmcblk0p8/uevent", "PARTNAME=kernel"),
                 ("envconf", "/dev/mmcblk0p3 0x0 0x8000"), ("boot", "invalid"), ("boot", "0")]
        for path, bad in cases:
            file = self.root / path
            original = file.read_text()
            file.write_text(bad)
            result = self.shell('cmd_upgrade --keep --yes\n', [self.release()])
            file.write_text(original)
            self.assertNotEqual(result.returncode, 0, path)
            self.assertFalse((self.root / "sysupgrade").exists(), path)
            self.assertFalse((self.root / "env-written").exists(), path)

    def test_switch_confirm_crc_readback_and_reboot_errors(self):
        for body in ('cmd_official', 'fw_printenv() { return 1; }; cmd_official --yes',
                     'fw_setenv() { return 1; }; cmd_official --yes',
                     'fw_setenv() { :; }; cmd_official --yes'):
            result = self.shell(body)
            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertFalse((self.root / "rebooted").exists())
        result = self.shell('cmd_official --yes')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "boot").read_text(), "0")
        self.assertTrue((self.root / "rebooted").exists())
        self.assertFalse((self.root / "sysupgrade").exists())

    def test_status_is_read_only_and_reports_staged_boot(self):
        (self.root / "boot").write_text("0")
        result = self.shell('cmd_ui_status')
        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(result.stdout)
        self.assertTrue(status["boot"]["ready"])
        self.assertFalse(status["ota_ready"])
        self.assertFalse((self.root / "env-written").exists())
        self.assertFalse((self.root / "rebooted").exists())

    def test_env_probe_installs_only_crc_valid_config_and_preserves_on_failure(self):
        probe = (REPO / "Files/C8-688/usr/sbin/c8-env-detect").read_text(encoding="utf-8")
        probe = probe.replace('. /lib/functions/c8-system.sh || exit 1', '')
        probe = probe.replace('CONF=/etc/fw_env.config', 'CONF="$TASK_TMP/detected"')
        (self.root / "detected").write_text("previous config\n")
        for valid in (False, True):
            mock = '''
logger() { :; }; flock() { :; }
fw_printenv() {
 [ "$1" = -c ] || return 1
 grep -q '0x80000$' "$2" || return 1
''' + ('echo 1\n' if valid else 'return 1\n') + '}\n'
            result = self.shell(mock + probe, library=True)
            if valid:
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((self.root / "detected").read_text(), "/dev/mmcblk0p2 0x0 0x80000\n")
            else:
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((self.root / "detected").read_text(), "previous config\n")
            self.assertFalse((self.root / "env-written").exists())

    @unittest.skipIf(os.name == "nt" or not shutil.which("flock"), "Linux flock required")
    def test_switch_and_ota_cannot_overlap(self):
        result = self.shell('''
acquire_lock
(c8_lock && c8_select_system 0) && exit 90
exit 0
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "env-written").exists())
        result = self.shell('c8_lock\n(acquire_lock) && exit 90\nexit 0\n')
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
