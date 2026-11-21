"""Execute the cold-start helper against isolated UCI, USB and AT boundaries."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "Scripts/AP3000M/fm350/ap3000m-fm350-prepare"
BASH = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else None)
JQ = os.environ.get("OTA_TEST_JQ") or shutil.which("jq")

MODEM = r'''
import json, os, sys
from pathlib import Path
root = Path(os.environ['TASK_TMP_NATIVE'])
state = json.loads((root / 'state.json').read_text())
cfg = json.loads((root / 'scenario.json').read_text())
command = sys.argv[-1]
state.setdefault('commands', []).append(command)
result = 'OK'
transport_ok = command != cfg.get('bad_json')
if command == 'AT+CPIN?': result = '+CPIN: ' + cfg.get('sim', 'READY') + '\nOK'
elif command == 'AT+CFUN?': result = '+CFUN: ' + state.get('cfun', cfg.get('cfun', '1')) + '\nOK'
elif command == 'dial': state['cfun'] = '1'
elif command == 'AT+CEREG?':
    result = '+CEREG: 0,' + cfg.get('registration', '1') + '\nOK'
elif command == 'AT+CGACT?':
    result = ('+CGACT: 2,1\n' if state.get('active') else cfg.get('inactive', '')) + 'OK'
elif command == 'AT+CGACT=1,2':
    state['attempts'] = state.get('attempts', 0) + 1
    if state.get('cycled') or state['attempts'] >= cfg.get('succeed_on', 99):
        state['active'] = not cfg.get('false_ack', False)
    else: result = '+CME ERROR: ' + cfg.get('error', '5702')
elif command == 'AT+CFUN=0': state['off'] = True
elif command == 'AT+CFUN=1':
    state['off'] = False
    state['cycled'] = True
    if cfg.get('auto_active'): state['active'] = True
if command == cfg.get('fail_command'): result = 'ERROR'
(root / 'state.json').write_text(json.dumps(state))
print(json.dumps({'ok': transport_ok, 'result': result}))
'''


@unittest.skipUnless(BASH and JQ, "Bash and jq required")
class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "board").write_text("airpi,ap3000m\n")
        (self.root / "tty-present").touch()
        self.usb = self.root / "usb/interface/tty"
        self.usb.mkdir(parents=True)
        (self.root / "usb/idVendor").write_text("0e8d\n")
        (self.root / "usb/idProduct").write_text("7127\n")
        (self.root / "state.json").write_text("{}")
        (self.root / "modem.py").write_text(MODEM)

    def run_helper(self, scenario=None, shell=None, real_flock=False, **switches):
        (self.root / "scenario.json").write_text(json.dumps(scenario or {}))
        source = SOURCE.read_text(encoding="utf-8").rsplit('\nmain "$@"', 1)[0]
        source = source.replace("/tmp/sysinfo/board_name", '"$TASK_TMP/board"')
        source = source.replace("/var/lock", "$TASK_TMP/locks")
        source = source.replace("/tmp/ap3000m-fm350-prepare.done", "$TASK_TMP/done")
        # Substitute only the physical character-device/sysfs boundaries.
        source = source.replace('[ -c "$port" ]', '[ -f "$TASK_TMP/tty-present" ]')
        mocks = r'''
uci() {
    case "$3" in
        fm350.main.enabled) printf '%s' "${TASK_ENABLED:-1}";;
        fm350.main.auto_dial) printf '%s' "${TASK_AUTODIAL:-1}";;
        fm350.main.boot_recovery) printf '%s' "${TASK_RECOVERY:-1}";;
        fm350.main.at_port) printf '%s' "${TASK_PORT:-/dev/ttyUSB3}";;
        fm350.main.cid) printf '%s' "${TASK_CID:-2}";;
        *) return 1;;
    esac
}
logger() { printf '%s\n' "$*" >> "$TASK_TMP/log"; }
pgrep() { [ "${TASK_RUNNING:-0}" = 1 ]; }
readlink() { printf '%s\n' "$TASK_TMP/usb/interface/tty"; }
sleep() { if [ "${TASK_TERM:-0}" = 1 ] && [ "$1" = 3 ]; then kill -TERM $$; fi; return 0; }
timeout() { case "$1" in 15|70) ;; *) exit 97;; esac; shift; "$@"; }
fm350d() { "$TASK_PYTHON" "$TASK_TMP/modem.py" "$@"; }
jq() { "$TASK_JQ" "$@"; }
'''
        if not real_flock:
            mocks += 'flock() { [ "${TASK_LOCKED:-0}" != 1 ]; }\n'
        prefix = 'TASK_TMP=$(cygpath -u "$TASK_TMP")\n' if os.name == "nt" else ""
        script = self.root / "run.sh"
        script.write_text(prefix + source + mocks + '\nmain "$@"\n', encoding="utf-8", newline="\n")
        return subprocess.run(shell or [BASH, script.as_posix()], capture_output=True, text=True,
                              timeout=40, env=dict(os.environ, TASK_TMP=self.root.as_posix(),
                              TASK_TMP_NATIVE=str(self.root), TASK_PYTHON=Path(sys.executable).as_posix(),
                              TASK_JQ=Path(JQ).as_posix(), **{"TASK_" + k.upper(): v for k, v in switches.items()}))

    def state(self):
        return json.loads((self.root / "state.json").read_text())

    def test_reproduced_5702_rebuilds_once_and_confirms_target_context(self):
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.state()["commands"]
        self.assertEqual(commands.count("AT+CGACT=1,2"), 3)
        self.assertEqual(commands.count("AT+CFUN=0"), 1)
        self.assertEqual(commands.count("AT+CFUN=1"), 1)
        self.assertEqual(commands[-1], "AT+CGACT?")
        self.assertTrue(self.state()["active"])
        self.assertTrue((self.root / "done").exists())
        before = list(commands)
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertEqual(self.state()["commands"], before)

    def test_existing_pdp_and_successful_first_or_second_activation_do_not_reset(self):
        for initial, scenario in (({"active": True}, {}), ({}, {"succeed_on": 1}),
                                  ({}, {"succeed_on": 2})):
            (self.root / "done").unlink(missing_ok=True)
            (self.root / "state.json").write_text(json.dumps(initial))
            result = self.run_helper(scenario)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("AT+CFUN=0", self.state()["commands"])
            self.assertEqual(self.state()["commands"][-1], "AT+CGACT?")

    def test_false_activation_ack_never_claims_success(self):
        for scenario in ({"succeed_on": 1, "false_ack": True}, {"false_ack": True}):
            (self.root / "done").unlink(missing_ok=True)
            (self.root / "state.json").write_text("{}")
            self.assertNotEqual(self.run_helper(scenario).returncode, 0)

    def test_disabled_manual_daemon_and_lock_contention_never_send_at(self):
        for switches in ({"enabled": "0"}, {"autodial": "0"}, {"recovery": "0"},
                         {"running": "1"}, {"locked": "1"}):
            result = self.run_helper(**switches)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.state(), {})

    def test_unready_sim_flight_mode_and_no_service_never_activate_or_reset(self):
        for scenario in ({"sim": "SIM PIN"}, {"cfun": "0"}, {"registration": "2"}):
            (self.root / "state.json").write_text("{}")
            self.run_helper(scenario)
            self.assertNotIn("AT+CGACT=1,2", self.state()["commands"])
            self.assertNotIn("AT+CFUN=0", self.state()["commands"])

    def test_boot_mode_four_uses_existing_auto_dial_policy_before_recovery(self):
        result = self.run_helper({"cfun": "4"})
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.state()["commands"]
        self.assertEqual(commands.count("dial"), 1)
        self.assertLess(commands.index("dial"), commands.index("AT+CFUN=0"))
        self.assertTrue(self.state()["active"])

    def test_wrong_board_usb_invalid_port_cid_and_missing_port_send_no_at(self):
        for switches in ({"port": "/dev/ttyUSB3;reboot"}, {"cid": "0"}, {"cid": "17"}):
            self.assertNotEqual(self.run_helper(**switches).returncode, 0)
            self.assertEqual(self.state(), {})
        for path, value in (("board", "cudy,tr3000-v1"), ("usb/idVendor", "ffff"),
                            ("usb/idProduct", "ffff")):
            target = self.root / path
            original = target.read_text()
            target.write_text(value)
            self.assertNotEqual(self.run_helper().returncode, 0)
            self.assertEqual(self.state(), {})
            target.write_text(original)
        (self.root / "tty-present").unlink()
        self.assertNotEqual(self.run_helper().returncode, 0)
        self.assertEqual(self.state(), {})

    def test_other_context_or_dns_residue_does_not_imply_target_activation(self):
        result = self.run_helper({"inactive": "+CGACT: 1,1\n", "registration": "5"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.state()["commands"].count("AT+CFUN=0"), 1)

    def test_unknown_error_bad_transport_and_failed_radio_do_not_claim_success(self):
        for scenario in ({"error": "54015"}, {"bad_json": "AT+CPIN?"},
                         {"fail_command": "AT+CFUN=0"}, {"fail_command": "AT+CFUN=1"}):
            (self.root / "done").unlink(missing_ok=True)
            (self.root / "state.json").write_text("{}")
            self.assertNotEqual(self.run_helper(scenario).returncode, 0)
            commands = self.state()["commands"]
            if "AT+CFUN=0" in commands:
                self.assertEqual(commands[-1], "AT+CFUN=1")
            else:
                self.assertNotIn("AT+CFUN=1", commands)

    def test_termination_after_radio_off_attempts_restore(self):
        result = self.run_helper(term="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.state()["commands"][-1], "AT+CFUN=1")
        self.assertFalse(self.state()["off"])

    @unittest.skipIf(os.name == "nt", "real BusyBox ash and flock required")
    def test_real_busybox_ash_recovery_and_radio_off_trap(self):
        busybox = shutil.which("busybox")
        self.assertIsNotNone(busybox)
        self.assertIsNotNone(shutil.which("flock"))
        for terminated in ("0", "1"):
            (self.root / "done").unlink(missing_ok=True)
            (self.root / "state.json").write_text("{}")
            script = self.root / "run.sh"
            result = self.run_helper(term=terminated, shell=[busybox, "ash", str(script)], real_flock=True)
            self.assertEqual(result.returncode, int(terminated == "1"), result.stderr)
            self.assertFalse(self.state()["off"])

    def test_real_build_hook_installs_exact_helpers_and_ordered_executable_link(self):
        source = (REPO / "Scripts/AP3000M/private.sh").read_text(encoding="utf-8")
        # Run the complete new installation block from the actual hook.
        start = source.index('_ap3000m_prepare=')
        end = source.index('\n# 8)', start)
        script = self.root / "install.sh"
        prefix = ('TASK_TMP=$(cygpath -u "$TASK_TMP")\n'
                  'GITHUB_WORKSPACE=$(cygpath -u "$GITHUB_WORKSPACE")\n') if os.name == "nt" else ""
        script.write_text('set -eu\n' + prefix + '_ap3000m_bf="$TASK_TMP/rootfs"\n'
                          'mkdir -p "$_ap3000m_bf/usr/sbin" "$_ap3000m_bf/etc/init.d" "$_ap3000m_bf/etc/rc.d"\n'
                          + source[start:end], encoding="utf-8", newline="\n")
        result = subprocess.run([BASH, script.as_posix()], capture_output=True, text=True,
                                env=dict(os.environ, TASK_TMP=self.root.as_posix(), GITHUB_WORKSPACE=REPO.as_posix()))
        self.assertEqual(result.returncode, 0, result.stderr)
        rootfs = self.root / "rootfs"
        for deployed, original in (("usr/sbin/ap3000m-fm350-prepare", SOURCE),
                                  ("etc/init.d/ap3000m-fm350-prepare", SOURCE.with_suffix(".init"))):
            self.assertEqual((rootfs / deployed).read_bytes(), original.read_bytes())
            if os.name != "nt": self.assertEqual((rootfs / deployed).stat().st_mode & 0o777, 0o755)
        link = rootfs / "etc/rc.d/S94ap3000m-fm350-prepare"
        if os.name != "nt": self.assertEqual(os.readlink(link), "../init.d/ap3000m-fm350-prepare")
        self.assertEqual(link.read_bytes(), SOURCE.with_suffix(".init").read_bytes())
        self.assertIn("START=94", link.read_text())
        profile = json.loads((REPO / "Profiles/AP3000M.json").read_text())
        required = {item["path"]: item for item in profile["required_files"]}
        for path in ("/usr/sbin/ap3000m-fm350-prepare", "/etc/init.d/ap3000m-fm350-prepare",
                     "/etc/rc.d/S94ap3000m-fm350-prepare"):
            self.assertTrue(required[path]["executable"])


if __name__ == "__main__":
    unittest.main()
