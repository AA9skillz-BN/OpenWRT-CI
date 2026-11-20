"""Execute C8 cellular adapters; hardware and complete firmware remain separate."""
import asyncio
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import urllib.request

REPO = Path(__file__).resolve().parents[2]
CELL = REPO / 'Scripts/C8-688'
PACKAGE = CELL / 'luci-app-c8-cellular'
RUNTIME = PACKAGE / 'root/usr/libexec/c8-cellular'
BASH = shutil.which('bash') or 'C:/Program Files/Git/bin/bash.exe'


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    result = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(result)
    finally:
        sys.dont_write_bytecode = previous
    return result


adapt = module('c8_cellular_adapt', CELL / 'cellular/adapt.py')
broker = module('c8_broker', RUNTIME / 'broker.py')
notify = module('c8_notify', RUNTIME / 'notify.py')


def unpack(data, dest, prefix=''):
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for item in archive:
            name = Path(*Path(item.name).parts[1:])
            if not item.isfile() or '..' in name.parts or name.is_absolute():
                continue
            if prefix:
                try:
                    name = name.relative_to(prefix)
                except ValueError:
                    continue
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(item).read())
            target.chmod(item.mode)


class SourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.original = Path(cls.temporary.name)
        local = os.environ.get('C8_TEST_REFERENCE')
        if local:
            source = Path(local)
            shutil.copytree(source / 'mt5700m-full', cls.original / 'mt')
            shutil.copytree(source / 'mt5700webui-reference-7abccd4/at-webserver', cls.original / 'web')
        else:
            for key, prefix, dest in [('mt5700m', '', 'mt'), ('c8-cellular-webui', 'at-webserver', 'web')]:
                lock = json.loads((REPO / 'Profiles/packages.lock.json').read_text(encoding='utf-8'))[key]
                url = lock['url'].removesuffix('.git').replace('https://github.com/', 'https://codeload.github.com/')
                with urllib.request.urlopen(url + '/tar.gz/' + lock['commit'], timeout=60) as response:
                    unpack(response.read(), cls.original / dest, prefix)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.top = Path(self.temp.name)
        self.manager, self.web = self.top / 'manager', self.top / 'web'
        shutil.copytree(self.original / 'mt/luci-app-mt5700m', self.manager)
        shutil.copytree(self.original / 'web', self.web)

    def test_actual_locked_sources_repeat_and_reject_drift_before_mutation(self):
        adapt.adapt(self.manager, self.web)
        def snapshot():
            return {str(p.relative_to(self.top)): p.read_bytes() for p in self.top.rglob('*') if p.is_file()}
        before = snapshot()
        adapt.adapt(self.manager, self.web)
        self.assertEqual(before, snapshot())
        for root, name in [(self.manager, 'root/usr/share/luci/menu.d/luci-app-mt5700m.json'),
                           (self.web, 'files/www/5700/network/dial/index.html'),
                           (self.web, 'files/www/5700/umi.ec9b4b52.js')]:
            original = (root / name).read_bytes()
            (root / name).write_bytes(original + b'\n')
            changed = snapshot()
            with self.assertRaisesRegex(ValueError, 'drift'):
                adapt.adapt(self.manager, self.web)
            self.assertEqual(changed, snapshot())
            (root / name).write_bytes(original)
        menu = json.loads((self.manager / 'root/usr/share/luci/menu.d/luci-app-mt5700m.json').read_bytes())
        self.assertNotIn('admin/modem', menu)
        self.assertEqual(len(menu), 9)
        for name in ('status', 'connection', 'network', 'sms', 'system', 'terminal', 'advanced', 'settings'):
            self.assertIn('admin/network/c8_cellular/maintenance/' + name, menu)

    def test_execute_pinned_frontend_sms_response_handler(self):
        adapt.adapt(self.manager, self.web)
        result = subprocess.run(['node', str(Path(__file__).with_name('test_c8_cellular_ui.js')),
                                 str(self.web / 'files/www/5700/umi.ec9b4b52.js')],
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_actual_profile_transaction_preserves_dial_mode_and_rolls_back_write_failure(self):
        adapt.adapt(self.manager, self.web)
        self.root = self.top
        source = (self.manager / 'root/usr/share/mt5700m/profile.sh').read_text(encoding='utf-8')
        for failure in ('none', 'modem', 'uci', 'query'):
            (self.top / 'profile').write_text('1,1,"IPV4V6","old","","",0')
            body = '''
PROFILE_JOURNAL="$TASK_TMP/profile-rollback"
OLD='1,1,"IPV4V6","old","","",0'
NEW='1,2,"IPV4V6","new","","",0'
log_message() { :; }; sync() { :; }; sleep() { :; }
uci() {
    printf '%s\\n' "$*" >> "$TASK_TMP/uci-args"
    if [ "$FAILURE" = uci ] && [ "$*" = '-q set mt5700m.connection.dial_mode=2' ]; then return 1; fi
}
AT_HELPER=at_mock
at_mock() {
    case "$2" in
        'AT^SETAUTODIAL?') printf '^SETAUTODIAL: %s\\nOK\\n' "$(cat "$TASK_TMP/profile")"; [ "$FAILURE" != query ];;
        AT^SETAUTODIAL=*)
            [ "$FAILURE" != modem ] || [ "$2" != "AT^SETAUTODIAL=$NEW" ] || return 1
            printf '%s' "${2#*=}" > "$TASK_TMP/profile"
            printf 'OK\\n';;
        *) return 1;;
    esac
}
if [ "$FAILURE" = none ]; then
    apply_profile_transaction "$NEW" || exit 90
    [ "$(cat "$TASK_TMP/profile")" = "$NEW" ] || exit 91
else
    apply_profile_transaction "$NEW" && exit 92
    [ "$(cat "$TASK_TMP/profile")" = "$OLD" ] || exit 93
fi
[ ! -f "$PROFILE_JOURNAL" ] || exit 94
'''
            result = ShellTests.shell(self, source, body, FAILURE=failure)
            self.assertEqual(result.returncode, 0, result.stderr)
        calls = (self.top / 'uci-args').read_text()
        self.assertIn('mt5700m.connection.dial_mode=2', calls)
        self.assertNotIn('network.', calls)
        self.assertNotIn('firewall.', calls)

    @unittest.skipIf(os.name == 'nt', 'native C transport and PTY run on Linux')
    def test_real_locked_native_transport_tcp_pty_sms_and_bridge_pdu(self):
        source = self.original / 'mt/mt5700m-transport'
        binary = self.top / 'transport'
        subprocess.run(['cc', '-O2', '-Wall', '-Wextra', '-Werror', '-o', str(binary),
                        str(source / 'src/transport.c')], check=True, capture_output=True)
        result = subprocess.run([sys.executable, str(source / 'tests/test_transport.py'), '-v'],
                                env=dict(os.environ, TRANSPORT_BIN=str(binary)),
                                text=True, capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print('Pinned MT5700M C transport TCP/PTY/SMS tests:\n' + result.stderr)
        self.assertRegex(result.stderr, r'Ran 19 tests')
        self.assertNotIn('skipped=', result.stderr)
        with patch.dict(os.environ, TRANSPORT_BIN=str(binary)):
            tests = module('pinned_transport_tests', source / 'tests/test_transport.py')
        # Feed the actual browser-style frame through the new normalizer and
        # the compiled original C transport, independently checking wire bytes.
        command = broker.normalize('AT+CMGS=2\\r000102\r')
        modem = tests.TransportTests('test_fragmented_success')
        reply = modem.run_modem([(command.encode(), [b'\r\n+CMGS: 4\r\nOK\r\n'])], ['at', command])
        self.assertTrue(broker.result_for(command, reply.returncode, reply.stdout.decode())['success'])

    @unittest.skipIf(os.name == 'nt', 'actual package make install runs on Linux')
    def test_actual_recipes_install_only_reviewed_assets_and_runtime(self):
        adapt.adapt(self.manager, self.web)
        top = self.top
        (top / 'include').mkdir()
        (top / 'include/package.mk').touch()
        (top / 'rules.mk').write_text('''
INCLUDE_DIR:=$(TOPDIR)/include
INSTALL_DIR:=install -d
INSTALL_DATA:=install -m 0644
CP:=cp -fpR
''')
        for tree in (self.web, PACKAGE):
            probe = top / 'probe.mk'
            probe.write_text('include Makefile\ninstall:\n\t$(call Package/$(PKG_NAME)/install,$(TOPDIR)/rootfs)\n')
            result = subprocess.run(['make', '-f', str(probe), 'install', f'TOPDIR={top}'],
                                    cwd=tree, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        root = top / 'rootfs'
        for name in adapt.REVIEWED['web']:
            if name == 'Makefile':
                continue
            self.assertEqual((root / name.removeprefix('files/')).read_bytes(), (self.web / name).read_bytes())
        self.assertFalse((root / 'usr/bin/at-server.py').exists())
        self.assertFalse((root / 'etc/init.d/at-webserver').exists())
        self.assertFalse((root / 'www/cgi-bin/at-ws-info').exists())
        self.assertEqual((root / 'etc/config/c8_cellular').stat().st_mode & 0o777, 0o600)
        for name in ('etc/init.d/c8-cellular', 'usr/libexec/rpcd/luci.c8_cellular', 'usr/libexec/c8-cellular/action'):
            self.assertEqual((root / name).stat().st_mode & 0o777, 0o755)
        required = {i['path']: i for i in json.loads((REPO / 'Profiles/C8-688.json').read_bytes())['required_files']}
        for file in root.rglob('*'):
            if file.is_file():
                self.assertIn('/' + file.relative_to(root).as_posix(), required)

    @unittest.skipIf(os.name == 'nt', 'Linux flock, SIGKILL and BusyBox ash recovery guard')
    def test_actual_action_lock_excludes_peers_and_blocks_pending_recovery_after_kill(self):
        adapt.adapt(self.manager, self.web)
        source = (self.manager / 'root/usr/share/mt5700m/lock.sh').read_text(encoding='utf8')
        journal = self.top / 'slot-rollback'
        source = source.replace('/etc/mt5700m/slot-rollback', str(journal))
        source = source.replace('/etc/mt5700m/profile-rollback', str(self.top / 'profile-rollback'))
        lock = self.top / 'lock.sh'
        lock.write_text(source)
        script = '. ' + shlex.quote(str(lock)) + '; mt5700m_action_lock || exit $?; '
        env = dict(os.environ, MT5700M_ACTION_LOCK=str(self.top / 'action.lock'))
        shell = ['busybox', 'ash', '-c']
        holder = subprocess.Popen([*shell, script + 'echo holding; exec sleep 30'],
                                  env=env, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), 'holding')
            rejected = subprocess.run([*shell, script + 'echo acquired'], env=env,
                                      capture_output=True, text=True, timeout=10)
            self.assertEqual(rejected.returncode, 75)
            journal.write_text('1 1 1 0 1\n')
        finally:
            holder.kill()
            holder.wait(timeout=5)
            holder.stdout.close()
        rejected = subprocess.run([*shell, script + 'echo acquired'], env=env,
                                  capture_output=True, text=True, timeout=10)
        self.assertEqual(rejected.returncode, 75)
        self.assertNotIn('acquired', rejected.stdout)
        recovery = subprocess.run([*shell, script + 'echo recovered'],
                                  env=dict(env, C8_SLOT_RECOVERY='1'), capture_output=True,
                                  text=True, timeout=10)
        self.assertEqual(recovery.returncode, 0, recovery.stderr)
        self.assertEqual(recovery.stdout.strip(), 'recovered')
        self.assertTrue(journal.exists(), 'lock acquisition alone must not remove the recovery journal')


class ProtocolTests(unittest.TestCase):
    def test_notification_save_failure_or_pending_edits_preserve_live_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'c8_cellular'
            original = b'config notify notify\n option enabled 0\n option custom keep\n'
            for failed in ('pending', 'enabled', 'url', 'commit'):
                config.write_bytes(original)
                def fake_uci(args, **kwargs):
                    if 'changes' in args:
                        return subprocess.CompletedProcess(args, 0, stdout=b'pending' if failed == 'pending' else b'')
                    if (failed in ('enabled', 'url') and f'.{failed}=' in args[-1]) or (failed == 'commit' and 'commit' in args):
                        raise subprocess.CalledProcessError(1, args)
                    return subprocess.CompletedProcess(args, 0)
                with patch.object(notify, 'CONFIG', config), patch.object(notify.subprocess, 'run', side_effect=fake_uci):
                    with self.assertRaises((ValueError, subprocess.CalledProcessError)):
                        notify.webhook_command('AT+WEBHOOK=1,"https://example.invalid/hook"')
                self.assertEqual(config.read_bytes(), original)
                self.assertEqual(list(Path(directory).iterdir()), [config])

    @unittest.skipIf(os.name == 'nt', 'Linux atomic config replacement and directory fsync')
    def test_notification_atomic_replacement_preserves_extra_options(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'c8_cellular'
            original = b'config notify notify\n option enabled 0\n option custom keep\n'
            config.write_bytes(original)
            calls = []
            def fake_uci(args, **kwargs):
                calls.append(args)
                if 'changes' in args:
                    return subprocess.CompletedProcess(args, 0, stdout=b'')
                staged = Path(args[args.index('-c') + 1]) / args[-1].split('.')[0]
                if 'commit' in args:
                    self.assertEqual(config.read_bytes(), original)
                    staged.write_bytes(original + b' option url https://example.invalid/hook\n')
                return subprocess.CompletedProcess(args, 0)
            with patch.object(notify, 'CONFIG', config), patch.object(notify.subprocess, 'run', side_effect=fake_uci):
                self.assertEqual(notify.webhook_command('AT+WEBHOOK=1,"https://example.invalid/hook"'), 'OK\r\n')
            self.assertIn(b'custom keep', config.read_bytes())
            self.assertIn(b'example.invalid/hook', config.read_bytes())
            self.assertEqual(config.stat().st_mode & 0o777, 0o600)
            self.assertTrue(all('c8_cellular.notify.' not in ' '.join(c) for c in calls))

    def test_failed_notification_is_not_retried_and_never_contains_sms_body(self):
        records = ['+CMGL: 1,0,,2\r\n0001', '+CMGL: 1,0,,2\r\n0001\r\n+CMGL: 2,0,,2\r\n0002']
        iterations, sent = [0], []
        def local_request(method, **params):
            if method == 'submit':
                return {'id': 'test'}
            return {'state': 'done', 'response': {'success': True, 'data': records[min(iterations[0], 1)]}}
        def sleep(seconds):
            if seconds == 60:
                iterations[0] += 1
                if iterations[0] == 3:
                    raise KeyboardInterrupt()
        class Opener:
            def open(self, request, **kwargs):
                sent.append(request.data)
                raise TimeoutError('ambiguous HTTP delivery')
        with patch.object(notify, 'request', side_effect=local_request), \
             patch.object(notify, 'uci', side_effect=lambda *args: '1' if args[-1].endswith('enabled') else 'https://example.invalid/hook'), \
             patch.object(notify.time, 'sleep', side_effect=sleep), \
             patch.object(notify.urllib.request, 'build_opener', return_value=Opener()):
            with self.assertRaises(KeyboardInterrupt):
                notify.monitor()
        self.assertEqual(len(sent), 1)
        self.assertNotIn(b'0002', sent[0])
        self.assertEqual(json.loads(sent[0])['msgtype'], 'text')

    def test_pdu_validation_terminal_errors_and_no_private_echo(self):
        command = broker.normalize('AT+CMGS=2\\r000102\r')
        self.assertEqual(command, 'AT+CMGS=2\\r000102')
        self.assertEqual(broker.normalize('AT+CMGS=2\r000102\x1a'), command)
        for bad in ('AT+CMGS=3\\r000102', 'AT+CMGS=2\\r001', 'AT\rAT&F', 'AT^SCICHG=0,1',
                    'AT^scichg =0,1', 'AT+CMGS="123"\rsecret\x1a'):
            with self.assertRaises(ValueError):
                broker.normalize(bad)
        for code, text in [(0, 'OK'), (0, 'ECHO +CMGS: 1\r\nOK'), (124, '+CMGS: 1\r\nOK')]:
            result = broker.result_for(command, code, text)
            self.assertFalse(result['success'])
            self.assertNotIn('000102', json.dumps(result))
        self.assertTrue(broker.result_for(command, 0, '+CMGS: 1\r\nOK')['success'])
        self.assertTrue(broker.result_for('AT+CMGL=4', 0, '+DATA: ERROR statistics\r\nOK')['success'])
        self.assertFalse(broker.result_for('AT', 0, 'OK\r\nERROR')['success'])


class JobTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipIf(os.name == 'nt', 'Linux Unix socket peer credentials and root rpcd executable')
    async def test_real_unix_socket_and_rpcd_exec_protocol_reject_method_override(self):
        calls = []
        async def runner(operation, value):
            calls.append((operation, value))
            return 0, 'OK\r\n'
        service = broker.Broker(runner)
        with tempfile.TemporaryDirectory() as directory:
            socket_path = Path(directory) / 'rpc.sock'
            client = Path(directory) / 'luci.c8_cellular'
            source = (PACKAGE / 'root/usr/libexec/rpcd/luci.c8_cellular').read_text(encoding='utf8')
            client.write_text(source.replace(broker.SOCKET, str(socket_path)))
            server = await asyncio.start_unix_server(service.handle, path=str(socket_path))
            socket_path.chmod(0o600)
            worker = asyncio.create_task(service.worker())
            async def invoke(method, data=None):
                args = [sys.executable, str(client), 'list'] if data is None else [sys.executable, str(client), 'call', method]
                if os.geteuid() != 0:
                    args = ['sudo', '-n', *args]
                proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                output, error = await asyncio.wait_for(proc.communicate(json.dumps(data).encode()), 10)
                self.assertEqual(proc.returncode, 0, error.decode())
                return json.loads(output)
            try:
                self.assertEqual(set(await invoke('list')), {'submit', 'result'})
                submitted = await invoke('submit', dict(operation='at', value='AT', request='a' * 32,
                                                        method='result', executable='/bin/false'))
                self.assertIn('id', submitted)
                await service.queue.join()
                result = await invoke('result', dict(id=submitted['id'], method='submit', value='AT&F'))
                self.assertTrue(result['response']['success'])
                self.assertEqual(calls, [('at', 'AT')])
                if os.geteuid() != 0:
                    reader, writer = await asyncio.open_unix_connection(str(socket_path))
                    writer.write(b'{"method":"submit"}\n')
                    await writer.drain()
                    self.assertEqual(json.loads(await reader.readline())['error'], 'permission_denied')
                    writer.close()
                    await writer.wait_closed()
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
                server.close()
                await server.wait_closed()

    async def test_single_worker_deduplication_lost_result_and_queue_expiry(self):
        calls, times = [], [0]
        async def runner(op, value):
            calls.append((op, value))
            await asyncio.sleep(0)
            return 0, 'OK\r\n'
        service = broker.Broker(runner, lambda: times[0])
        request = dict(operation='at', value='AT+CFUN=1', request='a' * 32)
        first = service.submit(request)
        self.assertEqual(service.submit(request), first)
        worker = asyncio.create_task(service.worker())
        try:
            await service.queue.join()
            self.assertTrue(service.result(first['id'])['response']['success'])
            self.assertEqual(len(calls), 1)
            self.assertEqual(service.submit(request)['id'], first['id'])
            with self.assertRaisesRegex(ValueError, 'reused'):
                service.submit(dict(request, value='AT'))
            times[0] = 61
            with self.assertRaisesRegex(ValueError, 'expired'):
                service.submit(request)
            self.assertEqual(len(calls), 1)
            second = service.submit(dict(request, request='b' * 32))
            times[0] = 80
            await service.queue.join()
            self.assertEqual(service.result(second['id'])['response']['error'], 'queue_expired_not_executed')
            self.assertEqual(len(calls), 1)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_frequent_queries_prune_results_without_replaying_writes(self):
        times = [0]
        async def runner(*_):
            return 0, 'OK'
        service = broker.Broker(runner, lambda: times[0])
        worker = asyncio.create_task(service.worker())
        try:
            for number in range(400):
                times[0] = number
                service.submit(dict(operation='at', value='AT', request=f'{number:032x}'))
                await service.queue.join()
            self.assertLessEqual(len(service.jobs), 62)
            self.assertEqual(len(service.nonces), 400)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    @unittest.skipIf(os.name == 'nt', 'Linux process group termination')
    async def test_timeout_kills_stuck_action_once_without_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / 'action'
            script.write_text('#!/bin/sh\ntrap "exit 1" TERM\nsleep 30 &\nwait\n')
            script.chmod(0o755)
            code, text = await broker.run_action('at', 'AT', str(script), timeout=0.05)
            self.assertEqual((code, text), (124, ''))


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def shell(self, source, body, **env):
        script = self.root / 'run.sh'
        script.write_text(('TASK_TMP=$(cygpath -u "$TASK_TMP")\n' if os.name == 'nt' else '') +
                          source + '\n' + body, encoding='utf-8', newline='\n')
        return subprocess.run([*getattr(self, 'shell_executable', [BASH]), script.as_posix()], capture_output=True, text=True, encoding='utf-8',
                              env=dict(os.environ, TASK_TMP=self.root.as_posix(), **env), timeout=30)

    def slot(self, body, failure=''):
        for number in (0, 1):
            directory = self.root / f'gpio/cpe-sel{number}'
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'value').write_text('1\n')
        (self.root / 'sci').write_text('1,0')
        (self.root / 'fun').write_text('1')
        source = (PACKAGE / 'root/usr/share/c8-cellular/slots.sh').read_text(encoding='utf-8')
        # Keep EXIT rollback traces in captured stderr, including on Linux.
        source = source.replace('slot_recover >/dev/null 2>&1 || true', 'slot_recover || true')
        setup = '''
SLOT_JOURNAL="$TASK_TMP/slot-rollback"
export C8_GPIO_ROOT="$TASK_TMP/gpio"
log_message() { :; }; sleep() { :; }; sync() { :; }
slot_at() {
    case "$1" in
        'AT^SCICHG?') printf '^SCICHG: %s\\nOK\\n' "$(cat "$TASK_TMP/sci")"; return 0;;
        'AT+CFUN?') printf '+CFUN: %s\\nOK\\n' "$(cat "$TASK_TMP/fun")"; return 0;;
    esac
    printf '%s\\n' "$1" >> "$TASK_TMP/writes"
    if [ "$1" = "$FAILURE" ] && [ ! -f "$TASK_TMP/failed" ]; then touch "$TASK_TMP/failed"; return 1; fi
    case "$1" in
        AT^SCICHG=*) printf '%s' "${1#*=}" > "$TASK_TMP/sci";;
        AT+CFUN=*) printf '%s' "${1#*=}" > "$TASK_TMP/fun";;
    esac
    printf 'OK\\n'
}
'''
        return self.shell(source, setup + body, FAILURE=failure)

    def test_all_three_slot_values_and_noop_are_readback_checked(self):
        for target, expected in [('0', '0 1 0 1 1'), ('6', '1 1 0 1 1'), ('1', '1 1 1 0 1')]:
            with self.subTest(target=target):
                result = self.slot(f'slot_switch {target} || exit 90\nslot_snapshot\n[ ! -f "$SLOT_JOURNAL" ]\n')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), expected)
        (self.root / 'writes').unlink(missing_ok=True)
        result = self.slot('slot_switch 3 && exit 90\n[ ! -f "$TASK_TMP/writes" ]\n')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failure_at_each_modem_write_restores_original_under_conditional_call(self):
        for command in ('AT^SCICHG=0,1', 'AT^HVSST=1,0', 'AT^HVSST=1,1', 'AT+CFUN=0', 'AT+CFUN=1'):
            (self.root / 'failed').unlink(missing_ok=True)
            result = self.slot('''
slot_switch 0 && exit 90
[ "$(slot_snapshot)" = '1 1 1 0 1' ] || exit 91
[ ! -f "$SLOT_JOURNAL" ] || exit 92
''', command)
            self.assertEqual(result.returncode, 0, command + result.stderr)

    def test_unconfirmed_rollback_retains_journal_blocks_and_recovers_later(self):
        result = self.slot('''
original_slot_at=$(declare -f slot_at)
slot_at() { return 1; }
printf '1 1 1 0 1\\n' > "$SLOT_JOURNAL"
slot_switch 0 && exit 90
[ -f "$SLOT_JOURNAL" ] || exit 91
eval "$original_slot_at"
slot_recover || exit 92
[ ! -f "$SLOT_JOURNAL" ] || exit 93
printf '1 1; touch BAD\\n' > "$SLOT_JOURNAL"
slot_recover && exit 94
[ ! -f BAD ]
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gpio_write_failure_does_not_continue_to_modem_changes(self):
        result = self.slot('''
printf() {
    if [ "$1" = '0\\n' ] && [ ! -f "$TASK_TMP/failed" ]; then touch "$TASK_TMP/failed"; return 1; fi
    command printf "$@"
}
slot_switch 0 && exit 90
[ "$(slot_snapshot)" = '1 1 1 0 1' ] || exit 91
[ ! -f "$SLOT_JOURNAL" ]
''')
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipIf(os.name == 'nt', 'real BusyBox ash on Linux')
    def test_slot_transactions_and_each_failure_under_real_busybox_ash(self):
        # A query helper is an external process in production and reports its
        # own explicit exit status. Bare `return` in a Bash EXIT trap instead
        # inherits the original failing status on Linux, corrupting that mock.
        self.shell_executable = [shutil.which('busybox'), 'ash']
        self.test_all_three_slot_values_and_noop_are_readback_checked()
        self.test_failure_at_each_modem_write_restores_original_under_conditional_call()
        (self.root / 'failed').unlink(missing_ok=True)
        self.test_gpio_write_failure_does_not_continue_to_modem_changes()

    def test_ethernet_preserves_network_policy_disabled_state_and_usb_selection(self):
        source = (PACKAGE / 'root/usr/share/c8-cellular/network.sh').read_text(encoding='utf-8')
        source = source.replace('. /usr/share/c8-cellular/slots.sh', ':')
        setup = '''
uci() {
    case "$*" in
        '-q show network') printf 'network.cell=interface\\n';;
        '-q get network.cell.device') echo eth1;;
        '-q get network.cell.auto') echo "$AUTO";;
        '-q get network.MT5700M.device') [ "$USB" = 1 ] && echo usb0 || return 1;;
        '-q get mt5700m.connection.enabled') echo "$ENABLED";;
        *) return 1;;
    esac
}
sync_manager() { echo USB; }
ifup() { printf '%s' "$*" > "$TASK_TMP/ifup"; }
ifdown() { printf '%s' "$*" > "$TASK_TMP/ifdown"; }
'''
        for auto, enabled in [('0', '1'), ('1', '0'), ('1', '1')]:
            (self.root / 'ifup').unlink(missing_ok=True)
            result = self.shell(setup + source, 'sync_manager; bring_down; bring_up',
                                AUTO=auto, ENABLED=enabled, USB='0')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')
            self.assertEqual((self.root / 'ifup').exists(), auto == enabled == '1')
            self.assertFalse((self.root / 'ifdown').exists())
        result = self.shell(setup + source, 'sync_manager', AUTO='1', ENABLED='1', USB='1')
        self.assertEqual(result.stdout.strip(), 'USB')

    def test_create_only_migration_is_repeatable_and_preserves_disabled_connection(self):
        source = (PACKAGE / 'root/etc/uci-defaults/94-c8-cellular').read_text(encoding='utf-8')
        source = source.replace('/etc/init.d/c8-cellular enable', 'service_mock enable')
        source = source.replace('exit 0', 'return 0')
        setup = '''
uci() {
    printf '%s\\n' "$*" >> "$TASK_TMP/uci"
    case "$*" in
        '-q get mt5700m.settings'|'-q get mt5700m.connection'|'-q get c8_cellular.notify') return 0;;
        '-q commit mt5700m') return 0;;
        *) return 91;;
    esac
}
service_mock() { printf '%s\\n' "$*" >> "$TASK_TMP/service"; }
'''
        result = self.shell(setup + '\nmigrate() {\n' + source + '\n}\n',
                            'migrate && migrate', IPKG_INSTROOT='')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(' set ', (self.root / 'uci').read_text())
        self.assertEqual((self.root / 'service').read_text().splitlines(), ['enable', 'enable'])
        (self.root / 'uci').unlink()
        result = self.shell(setup + '\nmigrate() {\n' + source + '\n}\n',
                            'migrate', IPKG_INSTROOT='/isolated-root')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / 'uci').exists())


if __name__ == '__main__':
    unittest.main()
