"""Regression tests for AP3000M system defaults, GPT repair and xhci IRQ affinity."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else None)

SPEC = importlib.util.spec_from_file_location(
    "ap3000m_system_defaults", REPO / "Scripts/AP3000M/system-defaults.py"
)
sys_defaults = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sys_defaults)


IMMORTAL_CONFIG_GENERATE = """#!/bin/sh
generate_static_system() {
\tuci -q batch <<-EOF
\t\tdelete system.@system[0]
\t\tadd system system
\t\tset system.@system[-1].hostname='ImmortalWrt'
\t\tset system.@system[-1].timezone='GMT0'
\t\tset system.@system[-1].zonename='UTC'
\t\tset system.@system[-1].ttylogin='0'
\t\tset system.@system[-1].log_size='128'
\t\tset system.@system[-1].urandom_seed='0'

\t\tdelete system.ntp
\t\tset system.ntp='timeserver'
\t\tset system.ntp.enabled='1'
\t\tset system.ntp.enable_server='0'
\t\tadd_list system.ntp.server='time.apple.com'
\t\tadd_list system.ntp.server='time1.google.com'
\t\tadd_list system.ntp.server='time.cloudflare.com'
\t\tadd_list system.ntp.server='pool.ntp.org'
\tEOF
}
"""

OPENWRT_CONFIG_GENERATE = IMMORTAL_CONFIG_GENERATE.replace(
    "\t\tadd_list system.ntp.server='time.apple.com'\n"
    "\t\tadd_list system.ntp.server='time1.google.com'\n"
    "\t\tadd_list system.ntp.server='time.cloudflare.com'\n"
    "\t\tadd_list system.ntp.server='pool.ntp.org'\n",
    "\t\tadd_list system.ntp.server='0.openwrt.pool.ntp.org'\n"
    "\t\tadd_list system.ntp.server='1.openwrt.pool.ntp.org'\n"
    "\t\tadd_list system.ntp.server='2.openwrt.pool.ntp.org'\n"
    "\t\tadd_list system.ntp.server='3.openwrt.pool.ntp.org'\n",
)

IMMORTAL_DHCP = """config dnsmasq
\toption expandhosts\t1
\toption min_cache_ttl\t3600
\toption use_stale_cache\t3600
\toption cachesize\t8000
"""

OPENWRT_DHCP = """config dnsmasq
\toption expandhosts\t1
\toption cachesize\t1000
"""


class Ap3000mDefaultsTests(unittest.TestCase):
    def test_config_generate_patches_immortalwrt_and_openwrt_repeatably(self):
        for source in (IMMORTAL_CONFIG_GENERATE, OPENWRT_CONFIG_GENERATE):
            patched = sys_defaults.patch_config_generate(source)
            self.assertEqual(patched, sys_defaults.patch_config_generate(patched))
            self.assertIn("set system.@system[-1].timezone='CST-8'", patched)
            self.assertIn("set system.@system[-1].zonename='Asia/Shanghai'", patched)
            self.assertIn("set system.@system[-1].cronloglevel='9'", patched)
            self.assertIn("add_list system.ntp.server='ntp.aliyun.com'", patched)
            self.assertIn("add_list system.ntp.server='ntp.tencent.com'", patched)
            self.assertNotIn("time1.google.com", patched)
            self.assertNotIn("0.openwrt.pool.ntp.org", patched)

    def test_dhcp_conf_normalizes_min_cache_ttl_to_300_repeatably(self):
        for source in (IMMORTAL_DHCP, OPENWRT_DHCP):
            patched = sys_defaults.patch_dhcp_conf(source)
            self.assertEqual(patched, sys_defaults.patch_dhcp_conf(patched))
            self.assertIn("\toption min_cache_ttl\t300\n", patched)
            self.assertNotIn("min_cache_ttl\t3600", patched)

    def test_drift_in_config_generate_or_dhcp_conf_fails_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)
            cg = tree / sys_defaults.CONFIG_GENERATE
            dhcp = tree / sys_defaults.DHCP_CONF
            cg.parent.mkdir(parents=True)
            dhcp.parent.mkdir(parents=True)
            cg.write_text(IMMORTAL_CONFIG_GENERATE, encoding="utf-8", newline="\n")
            dhcp.write_text("config dnsmasq\n\toption unknown\t1\n", encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(ValueError, "dhcp.conf"):
                sys_defaults.configure(tree)
            self.assertEqual(cg.read_text(encoding="utf-8"), IMMORTAL_CONFIG_GENERATE)

    def test_profile_and_config_include_new_ap3000m_gates(self):
        profile = json.loads((REPO / "Profiles/AP3000M.json").read_text(encoding="utf-8"))
        self.assertIn("sgdisk", profile["required_packages"])
        required = {item["path"]: item for item in profile["required_files"]}
        self.assertEqual(required["/bin/config_generate"]["build_source"], "package/base-files/files/bin/config_generate")
        self.assertEqual(required["/etc/config/dhcp"]["build_source"], "package/network/services/dnsmasq/files/dhcp.conf")
        self.assertEqual(required["/etc/uci-defaults/98-ap3000m-gpt-fix"]["source"], "Scripts/AP3000M/98-ap3000m-gpt-fix")
        self.assertTrue(required["/etc/uci-defaults/98-ap3000m-gpt-fix"]["executable"])


@unittest.skipUnless(BASH, "bash required")
class Ap3000mRuntimeScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_sh(self, script_text):
        script = self.root / "test.sh"
        prefix = 'TASK_TMP=$(cygpath -u "$TASK_TMP")\n' if os.name == "nt" else ""
        script.write_text(prefix + script_text, encoding="utf-8", newline="\n")
        return subprocess.run(
            [BASH, script.as_posix()],
            text=True,
            capture_output=True,
            encoding="utf-8",
            env=dict(os.environ, TASK_TMP=self.root.as_posix()),
        )

    def test_gpt_fix_runs_only_for_ap3000m_with_unaligned_backup_header(self):
        source = (REPO / "Scripts/AP3000M/98-ap3000m-gpt-fix").read_text(encoding="utf-8")
        adapted = (
            source.replace("/tmp/sysinfo/board_name", '"$TASK_TMP/board_name"')
            .replace('[ -b /dev/mmcblk0 ]', '[ -e "$TASK_TMP/mmcblk0" ]')
            .replace("/dev/mmcblk0", '"$TASK_TMP/mmcblk0"')
            .replace("exit 0", "return 0")
        )
        harness = f"""
run_gpt() {{
{adapted}
}}
logger() {{ :; }}
sgdisk() {{
  printf '%s\\n' "$*" >> "$TASK_TMP/calls"
  if [ "$1" = "-v" ]; then
    cat "$TASK_TMP/verify_out"
  fi
  return 0
}}
printf 'other,board\\n' > "$TASK_TMP/board_name"
touch "$TASK_TMP/mmcblk0"
printf "Problem: The secondary header's self-pointer indicates that it doesn't reside at the end of the disk.\\n" > "$TASK_TMP/verify_out"
run_gpt
[ ! -e "$TASK_TMP/calls" ] || exit 90

printf 'airpi,ap3000m\\n' > "$TASK_TMP/board_name"
printf 'No problems found.\\n' > "$TASK_TMP/verify_out"
run_gpt
[ "$(wc -l < "$TASK_TMP/calls")" -eq 1 ] || exit 91

rm -f "$TASK_TMP/calls"
printf "Problem: GPT claims the disk is larger than it is!\\n" > "$TASK_TMP/verify_out"
run_gpt
grep -q '^-e ' "$TASK_TMP/calls" || exit 92
"""
        result = self.run_sh(harness)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_net_tune_pins_xhci_irq_to_cpu1_on_dual_core_only(self):
        source = (REPO / "Scripts/AP3000M/net-tune/ap3000m-net-tune").read_text(encoding="utf-8")
        funcs = source.rsplit('\ncase "${1:-apply}" in', 1)[0]
        adapted = (
            funcs.replace("/proc/interrupts", '"$TASK_TMP/interrupts"')
            .replace("/proc/irq/", '"$TASK_TMP/irq/"')
        )
        harness = f"""
{adapted}
mkdir -p "$TASK_TMP/irq/73" "$TASK_TMP/irq/78"
printf '3\\n' > "$TASK_TMP/irq/73/smp_affinity"
printf '3\\n' > "$TASK_TMP/irq/78/smp_affinity"
cat > "$TASK_TMP/interrupts" <<'EOF'
           CPU0       CPU1
 73:   15732210          0    GICv3 237 Level     mt7915e
 78:    1165143          0    GICv3 205 Level     xhci-hcd:usb1
EOF

cpu_count() {{ printf '1'; }}
pin_xhci_irq >/dev/null
[ "$(cat "$TASK_TMP/irq/78/smp_affinity")" = "3" ] || exit 90

cpu_count() {{ printf '2'; }}
out=$(pin_xhci_irq)
[ "$out" = "78->2" ] || exit 91
[ "$(cat "$TASK_TMP/irq/78/smp_affinity")" = "2" ] || exit 92
[ "$(cat "$TASK_TMP/irq/73/smp_affinity")" = "3" ] || exit 93
[ "$(xhci_irq_status)" = "78:2" ] || exit 94
"""
        result = self.run_sh(harness)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
