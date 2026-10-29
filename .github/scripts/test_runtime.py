"""Run actual device scripts with isolated sysfs/UCI and no real upgrades."""
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else None)


def read(name):
    return (REPO / name).read_text(encoding="utf-8")


def module(path):
    spec = importlib.util.spec_from_file_location("fixture_module", REPO / path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@unittest.skipUnless(BASH, "bash required")
class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def shell(self, source, body="", args=()):
        script = self.root / "run.sh"
        script.write_text(('TASK_TMP=$(cygpath -u "$TASK_TMP")\n' if os.name == "nt" else "") + source + "\n" + body, encoding="utf-8", newline="\n")
        return subprocess.run([BASH, script.as_posix(), *args], text=True, capture_output=True,
                              encoding="utf-8", env=dict(os.environ, TASK_TMP=self.root.as_posix(),
                                                        TASK_PYTHON=Path(sys.executable).as_posix()))

    def guard(self, body):
        source = read("Scripts/AP3000M/rdnis-guard/ap3000m-rdnis-guard").rsplit('\nmain "$@"', 1)[0]
        return self.shell(source, 'sleep() { :; }\nlog() { :; }\n' + body)

    def test_disappeared_modem_and_timeout_never_report_recovery(self):
        result = self.guard('''
wait_dial() { return 1; }
snapshot() { printf '0 0 0'; }
verify_healed && exit 90
wait_dial() { return 0; }
dev_exists() { return 1; }
verify_healed && exit 91
exit 0
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_recovery_requires_valid_counters_and_received_packets(self):
        for counters, expected in (("0 0 0", 1), ("", 1), ("5 10 0", 0)):
            result = self.guard('''
wait_dial() { return 0; }
dev_exists() { return 0; }
have_ipv4() { return 0; }
have_route() { return 0; }
snapshot() { printf '%s' ''' + repr(counters) + '''; [ -n ''' + repr(counters) + ''' ]; }
verify_healed
''')
            self.assertEqual(result.returncode, expected, result.stderr)

    def test_missing_counters_are_unknown_rather_than_healthy(self):
        result = self.guard('''
dev_exists() { return 0; }; have_ipv4() { return 0; }; have_route() { return 0; }
stat() { return 1; }
cmd_check
''')
        self.assertEqual(result.returncode, 11, result.stderr)

    def test_route_must_belong_to_the_modem(self):
        result = self.guard('''
dev() { printf 'usb0'; }
ip() { printf '%s\n' "$*" > "$TASK_TMP/ip-args"; return 0; }
have_route
''')
        self.assertEqual(result.returncode, 1)
        self.assertEqual((self.root / "ip-args").read_text().strip(), "-4 route show default dev usb0")

    def test_watch_switch_stops_existing_cron_calls(self):
        result = self.guard('''
uci() { printf 0; }
heal() { exit 90; }
watch_once
MODE=watch
acquire_lock() { exit 91; }
main
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipIf(os.name == "nt" or not shutil.which("flock"), "Linux flock required")
    def test_recovery_modes_share_kernel_lock_and_ignore_old_pid_contents(self):
        result = self.guard('''
LOCK="$TASK_TMP/guard.lock"
printf 99999999 > "$LOCK"
( acquire_lock && touch "$TASK_TMP/first"; ) || exit 90
acquire_lock || exit 91
( exec 9>&-; acquire_lock ) && exit 92
exit 0
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cron_installation_is_idempotent_and_preserves_other_jobs(self):
        for name in ("ota/99-ap3000m-ota-cron", "rdnis-guard/99-ap3000m-rdnis-cron"):
            cron = self.root / "crontabs/root"
            cron.parent.mkdir(exist_ok=True)
            cron.write_text("0 0 * * * other-job\n")
            source = read("Scripts/AP3000M/" + name).replace("/etc/crontabs", '"$TASK_TMP/crontabs"')
            source = source.replace("/etc/init.d/cron", "cron_mock").replace("exit 0", ":")
            result = self.shell('cron_mock() { :; }; logger() { :; }\n' + source + source)
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = cron.read_text().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertIn("other-job", lines[0])

    def test_legacy_fan_command_delegates_to_the_single_controller(self):
        source = read("Files/C8-688/usr/sbin/c8-fan").replace(
            "exec /usr/sbin/h5000m-fancontrol", "fan_mock")
        result = self.shell('fan_mock() { printf "%s" "$*"; }\n' + source, args=("once",))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "apply")

    def platform(self, body):
        patcher = module("Scripts/C8-688/patch-upgrade.py")
        source = read(".github/scripts/fixtures/filogic-platform.sh")
        adapted = patcher.patch_platform(source)
        self.assertEqual(patcher.patch_platform(adapted), adapted)
        source = adapted.replace(". /lib/upgrade/c8-688.sh", "")
        return self.shell(source, body)

    def test_c8_upgrade_check_and_copy_dispatch_without_affecting_upstream_board(self):
        result = self.platform('''
board_name() { printf nradio,c8-688; }
c8_688_check_image() { echo CHECK; return 17; }
c8_688_do_upgrade() { echo UPGRADE; return 18; }
c8_688_copy_config() { echo COPY; return 19; }
platform_check_image test; [ $? = 17 ] || exit 90
platform_do_upgrade test; [ $? = 18 ] || exit 91
platform_copy_config test; [ $? = 19 ] || exit 92
board_name() { printf nradio,c8-668gl; }
emmc_do_upgrade() { echo "UPSTREAM $CI_KERNPART $CI_ROOTPART"; }
platform_do_upgrade test
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), ["CHECK", "UPGRADE", "COPY", "UPSTREAM kernel_2nd rootfs_2nd"])

    def test_changed_upstream_dispatch_requires_review(self):
        patcher = module("Scripts/C8-688/patch-upgrade.py")
        source = read(".github/scripts/fixtures/filogic-platform.sh")
        for broken in (source.replace("platform_copy_config()", "platform_copy_new()"),
                       source + "\n# nradio,c8-688 handled upstream\n"):
            with self.assertRaises(ValueError):
                patcher.patch_platform(broken)

    def c8(self, body):
        # Only device discovery/stat reads are mocked. Archive validation is real.
        return self.shell(read("Files/C8-688/lib/upgrade/c8-688.sh"), '''
board_name() { printf nradio,c8-688; }
find_mmc_part() { case "$1" in kernel_2nd) echo /dev/mmcblk0p8;; rootfs_2nd) echo /dev/mmcblk0p9;; esac; }
function [ { if test "$1" = '-b'; then return 0; fi; if test "$1" = '!' && test "$2" = '-b'; then return 1; fi; builtin [ "$@"; }
cat() { case "$1" in /sys/class/block/*/size) printf 4096;; *) command cat "$@";; esac; }
emmc_do_upgrade() { echo WRITE; }
''' + body)

    def archive(self, size=1024, extra_dir=False):
        with tarfile.open(self.root / "image.tar", "w", format=tarfile.USTAR_FORMAT) as archive:
            for name in (["sysupgrade-wrong/"] if extra_dir else []) + ["sysupgrade-device/"]:
                item = tarfile.TarInfo(name)
                item.type = tarfile.DIRTYPE
                archive.addfile(item)
            for name in ("kernel", "root"):
                item = tarfile.TarInfo("sysupgrade-device/" + name)
                item.size = size
                archive.addfile(item, io.BytesIO(b"x" * size))

    def test_c8_wrong_layout_is_rejected_before_any_write(self):
        self.archive()
        result = self.c8('''
find_mmc_part() { echo /dev/mmcblk0p7; }
c8_688_do_upgrade "$TASK_TMP/image.tar"
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("WRITE", result.stdout)

    def test_c8_size_directory_and_partition_stat_gates(self):
        self.archive()
        result = self.c8('c8_688_do_upgrade "$TASK_TMP/image.tar"\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "WRITE")
        self.archive(size=2 * 1024 * 1024)
        self.assertNotEqual(self.c8('c8_688_do_upgrade "$TASK_TMP/image.tar"\n').returncode, 0)
        self.archive(extra_dir=True)
        self.assertNotEqual(self.c8('c8_688_do_upgrade "$TASK_TMP/image.tar"\n').returncode, 0)
        self.archive()
        self.assertNotEqual(self.c8('cat() { :; }; c8_688_do_upgrade "$TASK_TMP/image.tar"\n').returncode, 0)

    def test_c8_copy_never_uses_an_oem_data_partition(self):
        result = self.c8('''
c8_688_prepare_upgrade || exit 90
[ -z "$CI_DATAPART$EMMC_DATA_DEV" ] || exit 91
EMMC_ROOTFS_BLOCKS=128
emmc_copy_config() { echo COPY; }
c8_688_copy_config || exit 92
EMMC_DATA_DEV=/dev/mmcblk0p10
c8_688_copy_config && exit 93
exit 0
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "COPY")


class ReleaseRetentionTests(unittest.TestCase):
    def test_previews_cannot_evict_stable_and_other_devices_are_untouched(self):
        cleaner = module(".github/scripts/release-cleanup.py")
        releases = [{"id": i, "tag_name": f"C8-688-build-{i}", "published_at": str(i),
                     "draft": False, "prerelease": False} for i in range(1, 5)]
        releases += [{"id": i, "tag_name": f"preview-C8-688-build-{i}", "published_at": str(i),
                      "draft": False, "prerelease": True} for i in range(5, 9)]
        releases += [{"id": 99, "tag_name": "AIROHA-build", "draft": False}]
        plan = cleaner.plan(releases)
        self.assertEqual([(channel, r["id"]) for _, channel, delete, r in plan if delete],
                         [("stable", 1), ("preview", 5)])
        self.assertEqual(len(plan), 8)

    def test_drafts_and_unknown_markers_are_preserved(self):
        cleaner = module(".github/scripts/release-cleanup.py")
        self.assertEqual(cleaner.plan([{"draft": True}]), [])
        self.assertIsNone(cleaner.model_of({"tag_name": "AP3000M-test", "body": "<!-- firmware-model: other -->"}))
        with self.assertRaises(ValueError):
            cleaner.plan([], keep=0)


if __name__ == "__main__":
    unittest.main()
