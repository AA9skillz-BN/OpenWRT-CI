"""Verify hotplug preserves persistent UCI settings using an isolated UCI store."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else None)

UCI = r'''
import json, os, sys
from pathlib import Path
p = Path(os.environ['TASK_TMP']) / 'uci.json'
d = json.loads(p.read_text())
args = sys.argv[1:]
if args[0] == '-q': args.pop(0)
cmd, args = args[0], args[1:]
if cmd == 'get':
    if args[0] not in d: sys.exit(1)
    v = d[args[0]]
    print(' '.join(v) if isinstance(v, list) else v)
elif cmd == 'show':
    for k, v in d.items():
        if k.startswith(args[0] + '.'):
            print(k + '=' + (' '.join("'" + t + "'" for t in v) if isinstance(v, list) else "'" + v + "'"))
elif cmd in ('set', 'add_list', 'del_list', 'rename'):
    key, value = args[0].split('=', 1)
    if cmd == 'set': d[key] = value
    if cmd == 'add_list': d.setdefault(key, []).append(value)
    if cmd == 'del_list': d[key] = [v for v in d.get(key, []) if v != value]
    if cmd == 'rename':
        prefix = key.rsplit('.', 1)[0] + '.' + value
        for k in list(d):
            if k == key or k.startswith(key + '.'):
                d[prefix + k[len(key):]] = d.pop(k)
elif cmd == 'delete': d.pop(args[0], None)
elif cmd != 'commit': raise ValueError(cmd)
p.write_text(json.dumps(d))
'''


@unittest.skipUnless(BASH, "bash required")
class UsbWanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "uci.py").write_text(UCI)

    def run_wan(self, config):
        (self.root / "uci.json").write_text(json.dumps(config))
        source = (REPO / "Files/TR3000-512/etc/tr3000-usbwan.sh").read_text(encoding="utf-8")
        source = source.rsplit('\nmain "$@"', 1)[0].replace("/etc/init.d/firewall reload", "firewall_reload")
        script = self.root / "run.sh"
        script.write_text(source + '''
uci() { "$TASK_PYTHON" "$TASK_TMP/uci.py" "$@"; }
is_usb_netdev() { return 0; }
sleep() { :; }
logger() { :; }
firewall_reload() { :; }
ifup() { touch "$TASK_TMP/ifup"; }
main usb0
''', encoding="utf-8", newline="\n")
        result = subprocess.run([BASH, script.as_posix()], capture_output=True, text=True, encoding="utf-8",
                                env=dict(os.environ, TASK_TMP=self.root.as_posix(),
                                         TASK_PYTHON=Path(sys.executable).as_posix()))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads((self.root / "uci.json").read_text())

    def zone(self, masq="1", name="wan", networks=None):
        return {"firewall.wan": "zone", "firewall.wan.name": name,
                "firewall.wan.network": networks or ["wan"], "firewall.wan.masq": masq}

    def test_existing_static_address_dns_and_priority_survive_repeated_hotplug(self):
        config = dict(self.zone(), **{"network.f50": "interface", "network.f50.proto": "static",
                    "network.f50.ipaddr": "192.0.2.2", "network.f50.dns": ["192.0.2.53"],
                    "network.f50.metric": "55", "network.f50.peerdns": "1", "network.f50.device": "eth2"})
        expected = dict(config, **{"network.f50.device": "usb0", "firewall.wan.network": ["wan", "f50"]})
        actual = self.run_wan(config)
        self.assertEqual(actual, expected)
        self.assertEqual(self.run_wan(actual), expected)

    def test_old_interface_and_firewall_references_migrate_without_losing_options(self):
        actual = self.run_wan(dict(self.zone(networks=["wan", "F50"]), **{
            "network.F50": "interface", "network.F50.proto": "dhcp", "network.F50.metric": "21",
            "network.F50.ifname": "eth2"}))
        self.assertNotIn("network.F50", actual)
        self.assertNotIn("network.f50.ifname", actual)
        self.assertEqual(actual["network.f50.metric"], "21")
        self.assertEqual(actual["firewall.wan.network"], ["wan", "f50"])

    def test_disabled_interface_is_not_started_or_reconfigured(self):
        config = dict(self.zone(), **{"network.f50": "interface", "network.f50.proto": "dhcp",
                                      "network.f50.disabled": "1", "network.f50.device": "eth2"})
        self.assertEqual(self.run_wan(config), config)
        self.assertFalse((self.root / "ifup").exists())

    def test_initial_nat_has_no_literal_quotes_and_user_nat_zero_is_preserved(self):
        actual = self.run_wan(self.zone(masq="'1'"))
        self.assertEqual(actual["firewall.wan.masq"], "1")
        self.assertEqual(actual["network.f50.proto"], "dhcp")
        actual["firewall.wan.masq"] = "0"
        self.assertEqual(self.run_wan(actual)["firewall.wan.masq"], "0")

    def test_user_selected_firewall_zone_and_absent_nat_are_preserved(self):
        zone = self.zone(masq="", name="custom", networks=["f50"])
        actual = self.run_wan(zone)
        self.assertEqual(actual["firewall.wan.name"], "custom")
        self.assertEqual(actual["firewall.wan.masq"], "")
        self.assertEqual(actual["firewall.wan.network"], ["f50"])


if __name__ == "__main__":
    unittest.main()
