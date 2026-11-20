#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Optional WeCom new-SMS notification. No flash polling log or SMS deletion."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

CONFIG = Path('/etc/config/c8_cellular')


def save_settings(enabled, url):
    # Work on a private copy, not shared /tmp/.uci deltas. A failed set/commit
    # leaves the live file and other operators' pending changes untouched.
    if CONFIG.is_symlink():
        raise ValueError('invalid_config_path')
    pending = subprocess.run(['uci', '-q', 'changes', 'c8_cellular'],
                             capture_output=True, timeout=5, check=True)
    if pending.stdout.strip():
        raise ValueError('apply_or_revert_pending_config_first')
    original = CONFIG.read_bytes()
    with tempfile.TemporaryDirectory(prefix='.c8-notify-', dir=CONFIG.parent) as directory:
        stage = Path(directory)
        # libuci retains its default delta search path even with -t. Use a
        # fresh package name so unrelated pending deltas cannot enter this copy.
        package = 'c8_notify_' + secrets.token_hex(8)
        staged = stage / package
        staged.write_bytes(original)
        staged.chmod(0o600)
        for args in [('set', f'{package}.notify.enabled={enabled}'),
                     ('set', f'{package}.notify.url={url}'), ('commit', package)]:
            subprocess.run(['uci', '-q', '-c', directory, '-t', str(stage / 'delta'), *args],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=5, check=True)
        # Refuse to overwrite a concurrently replaced configuration.
        if CONFIG.is_symlink() or CONFIG.read_bytes() != original:
            raise ValueError('config_changed_try_again')
        staged.chmod(0o600)
        with staged.open('rb') as stream:
            os.fsync(stream.fileno())
        os.replace(staged, CONFIG)
        descriptor = os.open(CONFIG.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def uci(*args):
    p = subprocess.run(["uci", "-q", *args], capture_output=True, text=True, timeout=5)
    return p.stdout.strip() if p.returncode == 0 else ""


def webhook_command(command):
    if command == "AT+WEBHOOK?":
        enabled = "1" if uci("get", "c8_cellular.notify.enabled") == "1" else "0"
        url = uci("get", "c8_cellular.notify.url")
        return f'+WEBHOOK: {enabled},"{url}"\r\nOK\r\n'
    match = re.fullmatch(r'AT\+WEBHOOK=([01]),"([^"\r\n]{0,1024})"', command)
    if not match:
        raise ValueError("invalid_webhook")
    url = urllib.parse.urlsplit(match[2])
    if match[1] == "1" and (url.scheme != "https" or not url.hostname or url.username or url.password):
        raise ValueError("https_webhook_required")
    save_settings(match[1], match[2])
    return "OK\r\n"


def request(method, **params):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect("/var/run/c8-cellular/rpc.sock")
        client.sendall(json.dumps(dict(method=method, **params)).encode() + b"\n")
        return json.loads(client.makefile("rb").readline(1048577))


def sms_records(data):
    # Keep only hashes in RAM. The modem retains the original messages.
    return {hashlib.sha256((m[1] + m[2]).encode()).hexdigest() for m in
            re.finditer(r"\+CMGL:\s*(\d+),[^\r\n]*[\r\n]+([0-9A-Fa-f]+)", data)}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def monitor():
    known = None
    while True:
        try:
            if uci("get", "c8_cellular.notify.enabled") != "1":
                known = None
                time.sleep(60)
                continue
            job = request("submit", operation="at", value="AT+CMGL=4", request=secrets.token_hex(16))
            response = None
            for _ in range(20):
                time.sleep(1)
                result = request("result", id=job["id"])
                if result.get("state") == "done":
                    response = result.get("response")
                    break
            if response and response.get("success"):
                current = sms_records(response["data"])
                new = current - known if known is not None else set()
                # Commit the in-RAM observation before submitting HTTP. An
                # ambiguous timeout must not cause an automatic duplicate.
                known = current
                if new:
                    body = json.dumps({"msgtype": "text", "text": {
                        "content": f"C8-688 收到 {len(new)} 条新短信，请登录蜂窝设置查看。"}}).encode()
                    url = uci("get", "c8_cellular.notify.url")
                    parsed = urllib.parse.urlsplit(url)
                    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                        raise ValueError("invalid_webhook")
                    req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
                    with urllib.request.build_opener(NoRedirect()).open(req, timeout=8) as result:
                        result.read(4096)
                # A failed/ambiguous HTTP submission is not automatically sent
                # again, and notification failures never affect modem health.
        except Exception:
            pass
        time.sleep(60)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "command":
        try:
            print(webhook_command(sys.argv[2]), end="")
        except Exception:
            sys.exit(65)
    else:
        monitor()
