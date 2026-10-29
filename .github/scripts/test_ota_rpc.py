"""Exercise the rpcd backend in ucode; only its process/UCI I/O is replaced."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
UCODE = os.environ.get("UCODE_TEST_BIN") or shutil.which("ucode")


@unittest.skipUnless(UCODE, "ucode runtime required (installed by Linux CI)")
class OtaRpcTests(unittest.TestCase):
    def test_ap_rpc_contract(self):
        self.exercise_device("AP3000M", "ap3000m")

    def test_c8_rpc_contract_and_explicit_switch_confirmation(self):
        self.exercise_device("C8-688", "c8-688")

    def exercise_device(self, device, cli):
        source = (REPO / f"Scripts/{device}/luci-app-{cli}-ota/root/usr/share/rpcd/ucode/{cli}-ota.uc").read_text(encoding="utf-8")
        source = re.sub(r"^import .*?;$", "", source, flags=re.M)
        harness = '''
let code = 0, output = 'started', calls = [], is_enabled = '1';
function popen(command, mode) {
    push(calls, command);
    return { read: function() { return output; }, close: function() { return code; } };
}
function readfile() { return 'fixture-tag'; }
function cursor() { return { get: function() { return is_enabled; } }; }
function load_backend() {
''' + source + '''
}
let api = load_backend()['luci.ap3000m_ota'];
let req = { args: { keep: true, tag: 'AP3000M-test' } };
let r = api.upgrade.call(req);
assert(r.started == true);
assert(index(calls[0], 'upgrade --background --keep --yes --tag AP3000M-test') >= 0);
code = 1; output = 'image check failed';
r = api.upgrade_tag.call(req);
assert(r.started != true && r.error == output);
code = 0; output = 'unexpected output';
assert(api.download.call(req).started != true);
output = 'started';
let count = length(calls);
is_enabled = '0';
assert(api.upgrade.call(req).error != null);
assert(api.download.call(req).error != null);
assert(api.check.call().error != null);
assert(length(calls) == count);
is_enabled = '1';
req.args.tag = 'AP3000M-test; echo injected';
assert(api.upgrade.call(req).error != null);
assert(api.upgrade_tag.call(req).error != null);
assert(api.download.call(req).error != null);
assert(length(calls) == count);
code = 1; output = '{"latest":"AP3000M-false-success"}';
assert(api.check.call().error != null);
code = 0; output = '{"latest":"AP3000M-ok"}';
assert(api.check.call().latest == 'AP3000M-ok');
print('rpc behavior verified\\n');
'''
        if device == "C8-688":
            harness = harness.replace("luci.ap3000m_ota", "luci.c8_688_ota").replace("AP3000M", "C8-688")
            harness += """
code = 0; output = 'started';
let count_before = length(calls);
assert(api.official.call({ args: { confirm: false } }).error != null);
assert(api.official.call({ args: { confirm: 'yes' } }).error != null);
assert(length(calls) == count_before);
assert(api.official.call({ args: { confirm: true } }).started == true);
assert(index(calls[-1], 'official --background --yes') >= 0);
is_enabled = '0';
assert(api.official.call({ args: { confirm: true } }).error != null);
"""
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "rpc.uc"
            script.write_text(harness, encoding="utf-8", newline="\n")
            result = subprocess.run([UCODE, str(script)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("rpc behavior verified", result.stdout)


if __name__ == "__main__":
    unittest.main()
