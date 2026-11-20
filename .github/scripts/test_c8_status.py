"""Run the status backend in real ucode, replacing only sysfs/ubus I/O."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
UCODE = os.environ.get("UCODE_TEST_BIN") or shutil.which("ucode")
BACKEND = REPO / "Scripts/C8-688/luci-app-c8-688-status/root/usr/share/rpcd/ucode/c8-688-status.uc"


def harness():
    source = re.sub(r"^import .*?;$", "", BACKEND.read_text(encoding="utf-8"), flags=re.M)
    return r'''
let files = {
    '/proc/cmdline': 'console=ttyS0 root=PARTLABEL=rootfs_2nd rootwait',
    '/proc/net/route': 'Iface Destination Gateway Flags RefCnt Use Metric Mask\neth1 00000000 0108A8C0 0003 0 0 11 00000000\nlan1 00000000 00000000 0201 0 0 0 00000000\nlan2 00000000 00000000 0000 0 0 0 00000000',
    '/proc/net/ipv6_route': '00000000000000000000000000000000 00 00000000000000000000000000000000 00 fe800000000000000000000000000001 00000400 00000000 00000000 00000003 wwan0_1\n00000000000000000000000000000000 00 00000000000000000000000000000000 00 00000000000000000000000000000000 ffffffff 00000000 00000000 00200200 lo',
    '/sys/class/net/eth1/operstate': 'up',
    '/sys/class/net/eth1/carrier': '1',
    '/sys/class/net/eth1/speed': '2500',
    '/sys/class/net/eth1/statistics/rx_errors': '0',
    '/sys/class/net/lan1/operstate': 'down',
    '/sys/class/net/lan1/carrier': '0',
    '/sys/class/net/lan1/speed': '-1',
    '/sys/class/hwmon/hwmon0/name': 'mdio_bus:15',
    '/sys/class/hwmon/hwmon0/temp1_input': '85000',
    '/sys/class/hwmon/hwmon1/name': 'cpu_thermal',
    '/sys/class/hwmon/hwmon1/temp1_input': '60000',
    '/sys/class/hwmon/hwmon2/name': 'pwmfan',
    '/sys/class/hwmon/hwmon2/pwm1': '0',
    '/sys/class/hwmon/hwmon2/fan1_input': '0',
    '/sys/bus/usb/devices/1-1/idVendor': '3466',
    '/sys/bus/usb/devices/1-1/idProduct': '3301',
    '/sys/bus/usb/devices/1-1/speed': '480',
    '/sys/bus/usb/devices/usb2/speed': '20000'
};
let paths = {
    '/sys/class/hwmon/hwmon*': ['/sys/class/hwmon/hwmon0', '/sys/class/hwmon/hwmon1', '/sys/class/hwmon/hwmon2'],
    '/sys/class/hwmon/hwmon0/temp*_input': ['/sys/class/hwmon/hwmon0/temp1_input'],
    '/sys/class/hwmon/hwmon1/temp*_input': ['/sys/class/hwmon/hwmon1/temp1_input'],
    '/sys/bus/usb/devices/*': ['/sys/bus/usb/devices/usb2', '/sys/bus/usb/devices/1-1', '/sys/bus/usb/devices/1-1:1.0']
};
let reads = [], calls = [], disconnected = 0;
let board = { model: 'NRadio C8-688', board_name: 'nradio,c8-688', kernel: '6.18', private: 'PRIVATE-BOARD', release: { description: 'fixture' } };
let network = { interface: [{ interface: 'cell', l3_device: 'eth1', proto: 'dhcp', up: true, 'ipv4-address': [{ address: 'PRIVATE-IP' }], data: { password: 'PRIVATE-PASSWORD', imsi: 'PRIVATE-SIM' } }] };
function readfile(path) { push(reads, path); return files[path]; }
function readlink(path) { return path == '/sys/class/net/lan1/master' ? '../../br-lan' : null; }
function glob(path) { return paths[path]; }
function connect() {
    return {
        call: function(object, method) {
            push(calls, object + '.' + method);
            if (object == 'system' && method == 'board') return board;
            if (object == 'network.interface' && method == 'dump') return network;
            die('unexpected ubus operation');
        },
        disconnect: function() { disconnected++; }
    };
}
function backend() {
''' + source + r'''
}
let api = backend()['luci.c8_688_status'];
let s = api.status.call();
assert(s.schema_version == 1 && s.boot.second_slot_running);
assert(s.network.available && !s.network.internet_tested && !s.network.forwarding_tested);
assert(s.network.devices[0].name == 'eth1' && s.network.devices[0].speed_mbps == 2500);
assert(s.network.devices[0].rx_errors == 0 && s.network.devices[0].tx_errors == null);
assert(s.network.devices[1].carrier == 0 && s.network.devices[1].speed_mbps == null);
assert(s.network.devices[1].bridge == 'br-lan');
assert(s.thermal.cpu_millidegrees == 60000); // PHY is hotter but is not the CPU.
assert(s.thermal.fans[0].pwm == 0 && s.thermal.fans[0].rpm == 0);
assert(length(s.usb) == 1 && s.usb[0].speed_mbps == 480); // Not root hub's 20000.
assert(length(s.network.ipv4_defaults) == 1 && s.network.ipv4_defaults[0].device == 'eth1');
assert(length(s.network.ipv6_defaults) == 1 && s.network.ipv6_defaults[0].metric == 1024);
assert(index(sprintf('%J', s), 'PRIVATE-') < 0);

files['/sys/class/hwmon/hwmon1/temp1_input'] = null;
files['/sys/class/hwmon/hwmon2/pwm1'] = '256';
s = api.status.call();
assert(s.thermal.cpu_millidegrees == null && s.thermal.fans[0].pwm == null);
assert(index(s.warnings, 'cpu_temperature_unknown') >= 0 && index(s.warnings, 'fan_pwm_unknown') >= 0);

paths['/sys/class/thermal/thermal_zone*'] = ['/sys/class/thermal/thermal_zone0'];
files['/sys/class/thermal/thermal_zone0/type'] = 'cpu-thermal';
files['/sys/class/thermal/thermal_zone0/temp'] = '76000';
s = api.status.call();
assert(s.thermal.cpu_millidegrees == 76000 && index(s.warnings, 'cpu_temperature_high') >= 0);

network = null;
files['/proc/net/route'] = null;
s = api.status.call();
assert(!s.network.available && length(s.network.interfaces) == 0);
assert(s.network.ipv4_defaults == null && index(s.warnings, 'network_status_unavailable') >= 0);

network = { interface: [{ interface: 'cell', l3_device: '../../etc/shadow', device: 'eth1' }] };
reads = [];
s = api.status.call();
assert(s.network.interfaces[0].device == null);
assert(index(join(' ', reads), '..') < 0);
files['/proc/cmdline'] = 'root=PARTLABEL=rootfs_2nd_bad';
assert(!api.status.call().boot.second_slot_running);

board = { board_name: 'another,device', private: 'PRIVATE-BOARD' };
assert(api.status.call().error == 'unsupported_board');
board = null;
assert(api.status.call().error == 'board_status_unavailable');
assert(disconnected == 8);
assert(length(filter(calls, function(x) { return x != 'system.board' && x != 'network.interface.dump'; })) == 0);
print('C8 status runtime and negative cases passed\n');
'''


@unittest.skipUnless(UCODE, "ucode runtime required (installed by Linux CI)")
class C8StatusTests(unittest.TestCase):
    def test_actual_backend_with_missing_sensors_routes_and_private_data(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "status.uc"
            script.write_text(harness(), encoding="utf-8", newline="\n")
            result = subprocess.run([UCODE, str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("negative cases passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
