"""Exercise the opt-in AP3000M USB workaround without touching real USB ports."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else None)
SOURCE = REPO / "Scripts/AP3000M/ap3000m-fm350-usb-compat"
TOPOLOGY = "/sys/devices/platform/soc/11200000.usb/usb2/2-0:1.0/usb2-port1"


@unittest.skipUnless(BASH, "bash required")
class UsbCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.port = self.root / "bus/usb2/2-0:1.0/usb2-port1"
        # ':' is not a Windows filename character. The sandbox path replacement
        # also allows these shell guard tests to run with Git Bash on Windows.
        if os.name == "nt":
            self.port = self.root / "bus/usb2/root-interface/usb2-port1"
        self.port.mkdir(parents=True)
        self.disable = self.port / "disable"
        self.disable.write_text("0\n")
        (self.root / "board").write_text("airpi,ap3000m\n")
        self.device = self.root / "bus/2-1"
        self.device.mkdir()
        (self.device / "idVendor").write_text("0e8d\n")
        (self.device / "idProduct").write_text("7127\n")

    def run_start(self, enabled="1", running="0", topology=TOPOLOGY):
        source = SOURCE.read_text(encoding="utf-8")
        source = source.replace("/tmp/sysinfo/board_name", '"$TASK_TMP/board"')
        source = source.replace("/sys/bus/usb/devices", '"$TASK_TMP/bus"')
        source = source.replace("/etc/init.d/fm350d", "dial_daemon")
        if os.name == "nt":
            source = source.replace('$TASK_TMP/bus"/usb2/2-0:1.0/', '$TASK_TMP/bus"/usb2/root-interface/')
        prefix = 'TASK_TMP=$(cygpath -u "$TASK_TMP")\n' if os.name == "nt" else ""
        mocks = '''
uci() { [ "$TASK_ENABLE" != missing ] || return 1; printf '%s' "$TASK_ENABLE"; }
readlink() { printf '%s\n' "$TASK_TOPOLOGY"; }
dial_daemon() { [ "$TASK_RUNNING" = 1 ]; }
logger() { :; }
'''
        script = self.root / "run.sh"
        script.write_text(prefix + source + mocks + "\nstart\n", encoding="utf-8", newline="\n")
        return subprocess.run([BASH, script.as_posix()], capture_output=True, text=True,
                              env=dict(os.environ, TASK_TMP=self.root.as_posix(), TASK_ENABLE=enabled,
                                       TASK_RUNNING=running, TASK_TOPOLOGY=topology))

    def test_default_and_disabled_preserve_usb3(self):
        for enabled in ("missing", "0", "", "yes"):
            result = self.run_start(enabled=enabled)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.disable.read_text(), "0\n")

    def test_known_fm350_and_early_boot_apply_compatibility(self):
        for product in ("7126", "7127", None):
            self.disable.write_text("0\n")
            if product is None:
                shutil.rmtree(self.device)
            else:
                (self.device / "idProduct").write_text(product + "\n")
            result = self.run_start()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.disable.read_text(), "1\n")

    def test_wrong_board_topology_and_usb_device_are_rejected(self):
        (self.root / "board").write_text("cudy,tr3000-v1\n")
        self.assertNotEqual(self.run_start().returncode, 0)
        self.assertEqual(self.disable.read_text(), "0\n")
        (self.root / "board").write_text("airpi,ap3000m\n")
        self.assertNotEqual(self.run_start(topology="/sys/different-controller/port1").returncode, 0)
        self.assertEqual(self.disable.read_text(), "0\n")
        for vendor, product in (("2cb7", "7127"), ("0e8d", "ffff")):
            (self.device / "idVendor").write_text(vendor + "\n")
            (self.device / "idProduct").write_text(product + "\n")
            self.assertNotEqual(self.run_start().returncode, 0)
            self.assertEqual(self.disable.read_text(), "0\n")

    def test_running_dial_daemon_is_not_disconnected_and_applied_state_is_idempotent(self):
        result = self.run_start(running="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.disable.read_text(), "0\n")
        self.disable.write_text("1\n")
        result = self.run_start(running="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.disable.read_text(), "1\n")


if __name__ == "__main__":
    unittest.main()
