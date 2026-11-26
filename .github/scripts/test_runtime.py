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


class ReleaseRetentionTests(unittest.TestCase):
    def test_previews_cannot_evict_stable_and_other_devices_are_untouched(self):
        cleaner = module(".github/scripts/release-cleanup.py")
        releases = [{"id": i, "tag_name": f"AP3000M-build-{i}", "published_at": str(i),
                     "draft": False, "prerelease": False} for i in range(1, 5)]
        releases += [{"id": i, "tag_name": f"preview-AP3000M-build-{i}", "published_at": str(i),
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
