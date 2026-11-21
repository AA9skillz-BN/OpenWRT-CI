"""Execute the boot worker, bounding recovery and preserving healthy/custom WANs."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
SOURCE = (REPO / 'Files/TR3000-512/etc/tr3000-wan-boot-guard.sh').read_text(encoding='utf8').rsplit('\nmain "$@"', 1)[0]
BASH = shutil.which('bash') or ('C:/Program Files/Git/bin/bash.exe' if os.name == 'nt' else None)


@unittest.skipUnless(BASH, 'shell required')
class WanBootTests(unittest.TestCase):
    def run_worker(self, setup='', mode='', shell=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'net/statistics').mkdir(parents=True)
            for name, value in {'board': 'cudy,tr3000-v1', 'uptime': '150.00 0',
                                'net/carrier': '1', 'net/statistics/rx_packets': '0',
                                'net/statistics/tx_packets': '10'}.items():
                (root / name).write_text(value + '\n')
            script = root / 'run.sh'
            script.write_text(SOURCE + r'''
NET="$TASK_TMP/net"; BOARD="$TASK_TMP/board"; BOOT_UPTIME="$TASK_TMP/uptime"; STATE="$TASK_TMP/state"
PROTO=dhcp; DEVICE=eth0; AUTO=1; DISABLED=0; OPT=1; UP=false; PENDING=true; CARRIER=1
uci() {
 case "$3" in
 network.wan.proto) echo "$PROTO";; network.wan.device) echo "$DEVICE";;
 network.wan.auto) echo "$AUTO";; network.wan.disabled) echo "$DISABLED";;
 network.wan.boot_rx_recovery) echo "$OPT";; *) return 1;; esac
}
ubus() { printf 'device=%s\nproto=%s\nup=%s\npending=%s\n' "$DEVICE" "$PROTO" "$UP" "$PENDING"; }
jsonfilter() { key=${2#@.}; sed -n "s/^$key=//p"; }
logger() { printf '%s\n' "$*" >> "$TASK_TMP/log"; }
ip() {
 case "$*" in
 '-4 -o address show dev eth0') printf '%s' "${ADDR:-}";;
 'link set dev eth0 down')
  echo down >> "$TASK_TMP/actions"
  [ "$MODE" != down_fail ] || return 1
  [ "$MODE" != interrupt ] || kill -TERM "$BASHPID"
  ;;
 'link set dev eth0 up') echo up >> "$TASK_TMP/actions";;
 *) return 1;; esac
}
sleep() {
 now=$(uptime_seconds); echo "$((now + $1)).00 0" > "$BOOT_UPTIME"
 tx=$(cat "$NET/statistics/tx_packets")
 if number "$tx" && [ "$MODE" != no_tx ]; then echo "$((tx + 1))" > "$NET/statistics/tx_packets"; fi
 if [ "$MODE" = recovers_during_wait ] && [ "$1" = 10 ]; then echo 1 > "$NET/statistics/rx_packets"; fi
}
''' + setup + '\nmain\nmain\n', encoding='utf8', newline='\n')
            result = subprocess.run((shell or [BASH]) + [script.as_posix()],
                                    env=dict(os.environ, TASK_TMP=root.as_posix(), MODE=mode),
                                    capture_output=True, text=True, timeout=45)
            actions = (root / 'actions').read_text().splitlines() if (root / 'actions').exists() else []
            return result, actions

    def test_stall_recovers_exactly_once_despite_repeated_start(self):
        result, actions = self.run_worker()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(actions, ['down', 'up'])

    def test_healthy_custom_disabled_disconnected_and_late_boot_untouched(self):
        for setup in ('echo 1 > "$NET/statistics/rx_packets"', 'UP=true; PENDING=false',
                      'ADDR="inet 192.0.2.1/24"', 'PROTO=static', 'DEVICE=dummy0',
                      'AUTO=0', 'DISABLED=1', 'OPT=0',
                      'echo other > "$BOARD"', 'mkdir "$NET/master"',
                      'echo 0 > "$NET/carrier"', 'echo 181.00 > "$BOOT_UPTIME"',
                      'echo invalid > "$NET/statistics/tx_packets"',
                      'echo invalid > "$NET/statistics/rx_packets"'):
            with self.subTest(setup=setup):
                self.assertEqual(self.run_worker(setup)[1], [])

    def test_no_reset_if_dhcp_stops_transmitting_or_rx_recovers(self):
        for mode in ('no_tx', 'recovers_during_wait'):
            self.assertEqual(self.run_worker(mode=mode)[1], [])

    def test_link_up_is_restored_even_when_down_command_fails(self):
        self.assertEqual(self.run_worker(mode='down_fail')[1], ['down', 'up'])

    def test_interrupt_during_link_reset_restores_link_up(self):
        self.assertEqual(self.run_worker(mode='interrupt')[1], ['down', 'up'])

    @unittest.skipUnless(shutil.which('busybox'), 'Linux BusyBox required')
    def test_actual_busybox_ash_worker(self):
        result, actions = self.run_worker(shell=[shutil.which('busybox'), 'ash'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(actions, ['down', 'up'])


if __name__ == '__main__':
    unittest.main()
