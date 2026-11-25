"""Regression tests for AP3000M FM350 USB3/RNDIS tuning, guard, OTA UI and defaults."""
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
NODE = shutil.which("node")

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

    def test_profile_and_config_include_all_ap3000m_gates_and_retire_old_hotplug(self):
        profile = json.loads((REPO / "Profiles/AP3000M.json").read_text(encoding="utf-8"))
        for pkg in ("sgdisk", "iputils-ping"):
            self.assertIn(pkg, profile["required_packages"])
        self.assertIn("/etc/hotplug.d/net/20-ap3000m-net-tune", profile["forbidden_files"])
        self.assertFalse((REPO / "Scripts/AP3000M/net-tune/20-ap3000m-net-tune").exists())
        required = {item["path"]: item for item in profile["required_files"]}
        expected_sources = {
            "/etc/uci-defaults/98-ap3000m-gpt-fix": ("Scripts/AP3000M/98-ap3000m-gpt-fix", True),
            "/etc/hotplug.d/net/90-ap3000m-net-tune": ("Scripts/AP3000M/net-tune/90-ap3000m-net-tune", True),
            "/etc/hotplug.d/iface/90-ap3000m-iface-tune": ("Scripts/AP3000M/net-tune/90-ap3000m-iface-tune", True),
            "/etc/sysctl.d/99-ap3000m-tune.conf": ("Scripts/AP3000M/net-tune/99-ap3000m-tune.conf", False),
            "/etc/config/ap3000m_ota": ("Scripts/AP3000M/ota/ap3000m_ota.config", False),
            "/usr/share/luci/menu.d/luci-app-ap3000m-ota.json": (
                "Scripts/AP3000M/luci-app-ap3000m-ota/root/usr/share/luci/menu.d/luci-app-ap3000m-ota.json", False
            ),
            "/usr/share/rpcd/acl.d/luci-app-ap3000m-ota.json": (
                "Scripts/AP3000M/luci-app-ap3000m-ota/root/usr/share/rpcd/acl.d/luci-app-ap3000m-ota.json", False
            ),
        }
        for path, (src, exe) in expected_sources.items():
            self.assertEqual(required[path]["source"], src)
            if exe:
                self.assertTrue(required[path].get("executable"))
        self.assertEqual(required["/bin/config_generate"]["build_source"], "package/base-files/files/bin/config_generate")
        self.assertEqual(required["/etc/config/dhcp"]["build_source"], "package/network/services/dnsmasq/files/dhcp.conf")
        tune_conf = (REPO / "Scripts/AP3000M/net-tune/99-ap3000m-tune.conf").read_text(encoding="utf-8")
        self.assertIn("net.core.rps_sock_flow_entries=32768", tune_conf)
        self.assertIn("net.core.netdev_budget=600", tune_conf)

    def test_ota_overview_uses_darkmode_safe_tokens_and_luci_modal(self):
        view_path = REPO / "Scripts/AP3000M/luci-app-ap3000m-ota/htdocs/luci-static/resources/view/ap3000m-ota/overview.js"
        text = view_path.read_text(encoding="utf-8")
        self.assertIn("'require ui';", text)
        self.assertIn("ui.showModal(", text)
        self.assertNotIn("confirm(", text)
        self.assertNotIn("#333", text)
        self.assertNotIn("#e6e6e6", text)
        if NODE:
            res = subprocess.run([NODE, "--check", str(view_path)], capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)


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

    def test_net_tune_locks_usb3_u0_gro_rfs_irq_and_dual_stack_mtu(self):
        source = (REPO / "Scripts/AP3000M/net-tune/ap3000m-net-tune").read_text(encoding="utf-8")
        funcs = source.rsplit('\ncase "${1:-apply}" in', 1)[0]
        adapted = (
            funcs.replace("/proc/interrupts", '"$TASK_TMP/interrupts"')
            .replace("/proc/irq/", '"$TASK_TMP/irq/"')
            .replace("/sys/class/net/", '"$TASK_TMP/net/"')
        )
        harness = f"""
{adapted}
USB3_PORT="$TASK_TMP/usb/port1"
USB3_DEV="$TASK_TMP/usb/2-1"
USB3_BUS="$TASK_TMP/usb/usb2"
MTU_CACHE="$TASK_TMP/ap3000m-mtu"
mkdir -p "$USB3_PORT" "$USB3_DEV/power" "$USB3_BUS/power" "$TASK_TMP/irq/73" "$TASK_TMP/irq/78"
mkdir -p "$TASK_TMP/net/eth2/queues/rx-0" "$TASK_TMP/net/eth2/queues/tx-0"

printf 'u1_u2\\n' > "$USB3_PORT/usb3_lpm_permit"
printf 'auto\\n' > "$USB3_DEV/power/control"
printf 'auto\\n' > "$USB3_BUS/power/control"
printf '0e8d\\n' > "$USB3_DEV/idVendor"
printf '7127\\n' > "$USB3_DEV/idProduct"
printf '3\\n' > "$TASK_TMP/irq/73/smp_affinity"
printf '3\\n' > "$TASK_TMP/irq/78/smp_affinity"
printf '0\\n' > "$TASK_TMP/net/eth2/queues/rx-0/rps_cpus"
printf '0\\n' > "$TASK_TMP/net/eth2/queues/rx-0/rps_flow_cnt"
printf '0\\n' > "$TASK_TMP/net/eth2/queues/tx-0/xps_cpus"
printf '0\\n' > "$TASK_TMP/net/eth2/gro_flush_timeout"
printf '0\\n' > "$TASK_TMP/net/eth2/napi_defer_hard_irqs"
printf '1000\\n' > "$TASK_TMP/net/eth2/tx_queue_len"
printf '1500\\n' > "$TASK_TMP/net/eth2/mtu"

cat > "$TASK_TMP/interrupts" <<'EOF'
           CPU0       CPU1
 73:   15732210          0    GICv3 237 Level     mt7915e
 78:    1165143          0    GICv3 205 Level     xhci-hcd:usb1
EOF

readlink() {{ printf '/sys/devices/platform/soc/11200000.usb/usb2/2-0:1.0/usb2-port1\\n'; }}
cpu_count() {{ printf '2'; }}
log() {{ :; }}
uci() {{ :; }}
ip() {{
  case "$*" in
    "-4 -o addr show dev eth2") printf '7: eth2    inet 10.10.10.2/24\\n' ;;
    "-6 -o addr show dev eth2 scope global") printf '7: eth2    inet6 2409:891f::2/64 scope global\\n' ;;
    *) return 0 ;;
  esac
}}

# Non-FM350 USB device must not have LPM modified
printf '1234\\n' > "$USB3_DEV/idVendor"
apply_tuning eth2
[ "$(cat "$USB3_PORT/usb3_lpm_permit")" = "u1_u2" ] || exit 89

# Reviewed FM350 locks LPM=0, power=on, RFS=4096, GRO=20000, IRQ=2
printf '0e8d\\n' > "$USB3_DEV/idVendor"
apply_tuning eth2
[ "$(cat "$USB3_PORT/usb3_lpm_permit")" = "0" ] || exit 90
[ "$(cat "$USB3_DEV/power/control")" = "on" ] || exit 91
[ "$(cat "$USB3_BUS/power/control")" = "on" ] || exit 92
[ "$(cat "$TASK_TMP/irq/78/smp_affinity")" = "2" ] || exit 93
[ "$(cat "$TASK_TMP/net/eth2/queues/rx-0/rps_cpus")" = "3" ] || exit 94
[ "$(cat "$TASK_TMP/net/eth2/queues/rx-0/rps_flow_cnt")" = "4096" ] || exit 95
[ "$(cat "$TASK_TMP/net/eth2/gro_flush_timeout")" = "20000" ] || exit 96
[ "$(cat "$TASK_TMP/net/eth2/napi_defer_hard_irqs")" = "2" ] || exit 97

# Dual-stack MTU probe picks largest MTU passing BOTH IPv4 and IPv6 DF (1452 here)
ping() {{
  case "$*" in
    "-M do -c 1 -W 1 127.0.0.1") return 0 ;;
    "-6 -c 1 -W 1 -I eth2 "*) return 0 ;;
    "-6 -M do -s "*)
      local prev="" sz=0
      for a in "$@"; do
        case "$prev" in -s) sz="$a" ;; esac
        prev="$a"
      done
      [ "$sz" -le 1404 ]
      ;;
    "-M do -s "*) return 0 ;;
  esac
}}
probe_mtu eth2
[ "$(cat "$MTU_CACHE")" = "1452" ] || exit 98
"""
        result = self.run_sh(harness)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_rdnis_guard_stops_daemon_before_cfun_and_filters_usb_vid_pid(self):
        source = (REPO / "Scripts/AP3000M/rdnis-guard/ap3000m-rdnis-guard").read_text(encoding="utf-8")
        funcs = source.rsplit('\nmain "$@"', 1)[0]
        adapted = funcs.replace("/sys/bus/usb/devices/", '"$TASK_TMP/usb/"').replace(
            "/etc/init.d/fm350d", "fm350d_init"
        )
        harness = f"""
{adapted}
sleep() {{ :; }}
log() {{ printf '%s\\n' "$*" >> "$TASK_TMP/logs"; }}
timeout() {{ shift; "$@"; }}
mkdir -p "$TASK_TMP/usb/1-1/1-1:1.6/ttyUSB0" "$TASK_TMP/usb/2-1/2-1:1.6/ttyUSB3"
printf '1a86\\n' > "$TASK_TMP/usb/1-1/idVendor"
printf '7523\\n' > "$TASK_TMP/usb/1-1/idProduct"
printf '0e8d\\n' > "$TASK_TMP/usb/2-1/idVendor"
printf '7127\\n' > "$TASK_TMP/usb/2-1/idProduct"

at_port_from_usb() {{
  for ifc in "$TASK_TMP"/usb/*/*:1.6 "$TASK_TMP"/usb/*/*:1.2; do
    [ -d "$ifc" ] && is_fm350_ifc "$ifc" || continue
    for node in "$ifc"/ttyUSB*; do
      [ -e "$node" ] || continue
      printf '/dev/%s' "${{node##*/}}"
      return 0
    done
  done
  return 1
}}
[ "$(at_port_from_usb)" = "/dev/ttyUSB3" ] || exit 90

# Verify watch_once is silent when offline
uci() {{ printf '1'; }}
fm350d_init() {{ printf 'init:%s\\n' "$1" >> "$TASK_TMP/events"; [ "$1" != status ]; }}
fm350d() {{ printf 'at:%s\\n' "$*" >> "$TASK_TMP/events"; return 0; }}
verify_healed() {{ return 0; }}
watch_once
[ ! -e "$TASK_TMP/logs" ] || exit 91

# Verify level1 stops fm350d BEFORE sending AT+CFUN=1,1
rm -f "$TASK_TMP/events"
uci() {{ if [ "$1" = "-q" ]; then printf '/dev/ttyUSB3'; fi; }}
level1
head -n 2 "$TASK_TMP/events" | tr '\\n' ',' | grep -q '^init:stop,at:at AT+CFUN=1,1,' || exit 92
"""
        result = self.run_sh(harness)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_ota_retains_latest_three_surviving_backups_and_uses_posix_df(self):
        source = (REPO / "Scripts/AP3000M/ota/ap3000m-ota").read_text(encoding="utf-8")
        self.assertIn('df -Pk "$(work_dir)"', source)
        funcs = source.rsplit('\nmain "$@"', 1)[0]
        harness = f"""
{funcs}
BACKUP_DIR="$TASK_TMP/backups"
PENDING_FILE="$TASK_TMP/pending"
mkdir -p "$BACKUP_DIR"
touch -t 202601010001 "$BACKUP_DIR/config-old1.tar.gz"
touch -t 202601010002 "$BACKUP_DIR/config-old2.tar.gz"
touch -t 202601010003 "$BACKUP_DIR/config-old3.tar.gz"
printf 'good' > "$TASK_TMP/img.bin"
need_tools() {{ :; }}
ensure_dir() {{ :; }}
current_tag() {{ printf 'AP3000M-now'; }}
sysupgrade() {{
  if [ "$1" = "-b" ]; then
    printf 'bk' > "$2"
    touch -t 202601010004 "$2"
    return 0
  fi
  return 0
}}
cmd_upgrade --image "$TASK_TMP/img.bin" --keep --yes >/dev/null 2>&1
[ "$(ls -1 "$BACKUP_DIR"/config-*.tar.gz | wc -l)" -eq 3 ] || exit 90
[ ! -e "$BACKUP_DIR/config-old1.tar.gz" ] || exit 91
"""
        result = self.run_sh(harness)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
