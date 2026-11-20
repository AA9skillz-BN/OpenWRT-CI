#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Authenticated LuCI jobs -> existing, locked native MT5700M transport.

Only a root-owned Unix socket is opened. No HTTP/WebSocket AT listener exists.
Jobs survive a lost browser request; writes are never replayed automatically.
"""
import asyncio
from collections import OrderedDict
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import struct
import sys
import time

SOCKET = "/var/run/c8-cellular/rpc.sock"
ACTION = "/usr/libexec/c8-cellular/action"
LIMIT = 1024 * 1024


def normalize(command):
    if not isinstance(command, str):
        raise ValueError("invalid_command")
    command = command.rstrip("\r\n")
    # MT5700M manual's single-frame PDU syntax is literal backslash-r.
    # Accept the alternate browser's interactive representation, convert once,
    # then send through the same native transport. Never issue a second submit.
    match = re.fullmatch(r"AT\+CMGS=(\d+)(?:\\r|\r)([0-9A-Fa-f]+)\x1a?", command)
    if match:
        payload = bytes.fromhex(match[2])
        if not payload or len(payload) <= payload[0] + 1 or len(payload) - payload[0] - 1 != int(match[1]):
            raise ValueError("invalid_sms_pdu_length")
        command = "AT+CMGS=" + match[1] + "\\r" + match[2].upper()
    if not re.fullmatch(r"AT[ -~]{0,4094}", command):
        raise ValueError("invalid_command")
    if command.startswith("AT+CMGS=") and not match:
        raise ValueError("invalid_sms_frame")
    if re.search(r'\^SCICHG\s*=', command, re.IGNORECASE):
        raise ValueError("use_c8_slot_control")
    command = re.sub(r'^AT\^SETAUTODIAL\s*=', 'AT^SETAUTODIAL=', command, flags=re.IGNORECASE)
    return command


def result_for(command, code, stdout):
    lines = [s.strip() for s in stdout.splitlines() if s.strip() and s.strip() != command]
    terminal = lines[-1] if lines else ""
    success = code == 0 and terminal == "OK"
    if command.startswith("AT+CMGS="):
        success = success and any(re.fullmatch(r"\+CMGS:\s*\d+(?:,.*)?", s) for s in lines)
    if success:
        return {"success": True, "data": "\r\n".join(lines), "error": None}
    # No echoed PIN/APN/password/recipient/PDU is copied into an error or log.
    error = next((s for s in lines if re.fullmatch(r"\+(?:CME|CMS) ERROR:\s*\d+", s)), None)
    return {"success": False, "data": None,
            "error": error or ("submission_unconfirmed_do_not_resend" if command.startswith("AT+CMGS=")
                               else f"operation_failed_{code}" if code else "missing_final_ok")}


async def run_action(operation, value="", executable=ACTION, timeout=190):
    process = await asyncio.create_subprocess_exec(
        executable, operation, value, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, start_new_session=True)
    async def bounded_read():
        output = bytearray()
        while True:
            chunk = await process.stdout.read(8192)
            if not chunk:
                return bytes(output)
            output.extend(chunk)
            if len(output) > LIMIT:
                raise ValueError("response_too_large")
    try:
        output = await asyncio.wait_for(bounded_read(), timeout)
        await asyncio.wait_for(process.wait(), 5)
        return process.returncode, output.decode("utf-8", errors="replace")
    except (asyncio.TimeoutError, ValueError, asyncio.CancelledError):
        # Kill the whole process group, including a stuck native transport. Its
        # flock closes; the native transport fences/quarantines the next query.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), 3)
        except asyncio.TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
        return 124, ""


class Broker:
    def __init__(self, runner=run_action, clock=time.monotonic):
        self.runner, self.clock = runner, clock
        self.jobs = OrderedDict()
        self.nonces = OrderedDict()
        self.queue = asyncio.Queue(maxsize=4)

    def prune(self):
        for key, job in list(self.jobs.items()):
            if job["state"] == "done" and self.clock() - job["updated"] > 60:
                del self.jobs[key]
        for nonce, entry in list(self.nonces.items()):
            if self.clock() - entry["created"] > 600 and entry["id"] not in self.jobs:
                del self.nonces[nonce]

    def submit(self, request):
        operation = request.get("operation", "at")
        value = request.get("value", "")
        if operation == "at":
            value = normalize(value)
        elif operation == "slot":
            if value not in ("0", "1", "6"):
                raise ValueError("invalid_slot")
        elif operation == "diagnostics":
            value = ""
        else:
            raise ValueError("invalid_operation")
        nonce = request.get("request", "")
        if not re.fullmatch(r"[a-f0-9]{32}", nonce):
            raise ValueError("invalid_request_id")
        self.prune()
        fingerprint = hashlib.sha256((operation + "\0" + value).encode()).hexdigest()
        previous = self.nonces.get(nonce)
        if previous:
            if fingerprint != previous["fingerprint"]:
                raise ValueError("request_id_reused")
            if previous["id"] not in self.jobs:
                raise ValueError("result_expired_do_not_resubmit")
            return {"id": previous["id"], "state": self.jobs[previous["id"]]["state"]}
        # Bound response RAM as well as job count; retained nonce tombstones
        # prevent an evicted result from accidentally replaying a write.
        size = sum(len(json.dumps(j.get("response", "")).encode()) for j in self.jobs.values())
        if self.queue.full() or len(self.jobs) >= 128 or len(self.nonces) >= 4096 or size >= 8 * LIMIT:
            raise ValueError("busy")
        key = secrets.token_hex(24)
        self.jobs[key] = {"state": "queued", "updated": self.clock(),
                          "created": self.clock()}
        self.nonces[nonce] = {"id": key, "fingerprint": fingerprint, "created": self.clock()}
        self.queue.put_nowait((key, operation, value))
        return {"id": key, "state": "queued"}

    def result(self, key):
        self.prune()
        if not isinstance(key, str) or not re.fullmatch(r"[a-f0-9]{48}", key) or key not in self.jobs:
            raise ValueError("job_not_found")
        job = self.jobs[key]
        return {"state": job["state"], "response": job.get("response")}

    async def worker(self):
        while True:
            key, operation, value = await self.queue.get()
            job = self.jobs[key]
            job["state"] = "running"
            try:
                if self.clock() - job["created"] > 15:
                    response = {"success": False, "data": None, "error": "queue_expired_not_executed"}
                    job.update(state="done", response=response, updated=self.clock())
                    continue
                code, output = await self.runner(operation, value)
                if operation == "at":
                    response = result_for(value, code, output)
                else:
                    response = {"success": code == 0,
                                "data": json.loads(output) if code == 0 else None,
                                "error": None if code == 0 else "operation_unconfirmed_check_recovery"}
            except Exception:
                response = {"success": False, "data": None, "error": "operation_unconfirmed_no_retry"}
            finally:
                self.queue.task_done()
            job.update(state="done", response=response, updated=self.clock())

    async def handle(self, reader, writer):
        try:
            # rpcd's executable runs as root; no other local uid can submit.
            peer = writer.get_extra_info("socket")
            _, uid, _ = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != 0:
                raise ValueError("permission_denied")
            raw = await asyncio.wait_for(reader.readline(), 3)
            if len(raw) > 8192:
                raise ValueError("request_too_large")
            request = json.loads(raw)
            if request.get("method") == "submit":
                result = self.submit(request)
            elif request.get("method") == "result":
                result = self.result(request.get("id"))
            else:
                raise ValueError("invalid_method")
        except Exception as exc:
            result = {"error": str(exc) if isinstance(exc, ValueError) else "service_unavailable"}
        writer.write(json.dumps(result, ensure_ascii=False).encode() + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()


async def main():
    os.umask(0o077)
    path = Path(SOCKET)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.parent.stat().st_uid != 0:
        raise RuntimeError("invalid_socket_directory")
    path.parent.chmod(0o700)
    path.unlink(missing_ok=True)
    broker = Broker()
    server = await asyncio.start_unix_server(broker.handle, path=str(path), limit=8193)
    path.chmod(0o600)
    async with server:
        await asyncio.gather(server.serve_forever(), broker.worker())


if __name__ == "__main__":
    asyncio.run(main())
