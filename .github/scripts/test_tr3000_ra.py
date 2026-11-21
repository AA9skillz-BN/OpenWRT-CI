"""Check the installed RA rule against RFC 1624 and real Linux packet output."""
import ipaddress
import json
import os
from pathlib import Path
import re
import select
import shutil
import socket
import struct
import subprocess
import sys
import time
import unittest

REPO = Path(__file__).resolve().parents[2]
RULE = REPO / "Files/TR3000-512/etc/nftables.d/90-tr3000-ra-medium.nft"


def checksum(data):
    total = sum(struct.unpack(f"!{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xffff) + (total >> 16)
    return (~total) & 0xffff


def ra_packet(old_checksum, flags=0xc8, source="fe80::1", hoplimit=255, kind=134, code=0):
    src, dst = (ipaddress.IPv6Address(address).packed for address in (source, "ff02::1"))
    # Prefix, DNS, MTU and unrelated flags must survive byte-for-byte.
    payload = bytearray(struct.pack("!BBHBBHII", kind, code, 0, 63, flags, 1800, 0, 0)
        + struct.pack("!BBBBIII16s", 3, 4, 64, 0xc0, 3600, 1800, 0,
                      ipaddress.IPv6Address("2001:db8:6::").packed)
        + struct.pack("!BBHI16s", 25, 3, 0, 3600, ipaddress.IPv6Address("2001:db8::53").packed)
        + struct.pack("!BBHI", 5, 1, 0, 1500)
        + struct.pack("!BBHI", 254, 1, 0, 0x91aabbcc))
    pseudo = src + dst + struct.pack("!I3xB", len(payload), 58)
    adjustment = (checksum(pseudo + payload) - old_checksum) % 65535
    struct.pack_into("!H", payload, len(payload) - 6, adjustment)
    struct.pack_into("!H", payload, 2, old_checksum)
    assert checksum(pseudo + payload) == 0
    header = struct.pack("!IHBB16s16s", 6 << 28, len(payload), 58, hoplimit, src, dst)
    return header + payload


class RaRuleTests(unittest.TestCase):
    def test_actual_rule_covers_every_checksum_and_matches_rfc1624(self):
        source = RULE.read_text(encoding="ascii")
        rules = source.split("chain tr3000_ra_medium {\n", 1)[1].split("\n}", 1)[0].splitlines()
        parsed = []
        for line in rules:
            body = line.strip().removesuffix(" @th,40,8 set @th,40,8 & 0xf7 counter return")
            self.assertNotEqual(body, line.strip(), "unexpected flag write or control flow")
            if " map " in body:
                match = re.fullmatch(r"icmpv6 checksum >= (0x[0-9a-f]+) icmpv6 checksum set icmpv6 checksum map \{ (.+) \}", body)
                self.assertIsNotNone(match)
                values = {int(k, 16): int(v, 16) for k, v in re.findall(r"(0x[0-9a-f]+) : (0x[0-9a-f]+)", match[2])}
                parsed.append(("map", int(match[1], 16), values))
            else:
                match = re.fullmatch(r"icmpv6 checksum & (0x[0-9a-f]+) == (0x[0-9a-f]+) icmpv6 checksum set icmpv6 checksum \^ (0x[0-9a-f]+)", body)
                self.assertIsNotNone(match)
                parsed.append(("xor", *(int(value, 16) for value in match.groups())))
        for old in range(65536):
            matches = []
            for rule in parsed:
                if rule[0] == "map" and old >= rule[1]:
                    matches.append(rule[2][old])
                if rule[0] == "xor" and old & rule[1] == rule[2]:
                    matches.append(old ^ rule[3])
            # The special zero/carry map intentionally precedes the general rules.
            self.assertTrue(matches, hex(old))
            total = ((~old) & 0xffff) + ((~0x3fc8) & 0xffff) + 0x3fc0
            while total >> 16:
                total = (total & 0xffff) + (total >> 16)
            self.assertEqual(matches[0], (~total) & 0xffff, hex(old))

    def test_overlay_has_an_exact_final_image_gate_and_no_other_device_copy(self):
        profile = json.loads((REPO / "Profiles/TR3000-512.json").read_bytes())
        self.assertIn({"path": "/etc/nftables.d/90-tr3000-ra-medium.nft",
                       "source": RULE.relative_to(REPO).as_posix()}, profile["required_files"])
        self.assertIn('cp -a "$WRT_REPO_ROOT/Files/TR3000-512/." files/',
                      (REPO / "Scripts/TR3000-512/private.sh").read_text(encoding="utf8"))
        self.assertEqual(list((REPO / "Files").rglob(RULE.name)), [RULE])

    @unittest.skipUnless(sys.platform == "linux", "Linux network namespace and nftables required")
    def test_real_kernel_output_checksums_scope_and_reload(self):
        for tool in ("nft", "ip", "unshare"):
            self.assertIsNotNone(shutil.which(tool), tool + " required for Linux RA test")
        command = ["unshare", "--net", sys.executable, str(Path(__file__).resolve()), "--kernel-worker"]
        if os.geteuid() != 0:
            command = ["sudo", "-n", *command]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("actual kernel RA checks passed", result.stdout)
        print(result.stdout.strip())


def kernel_worker():
    """Runs as root inside an ephemeral network namespace, never on the host LAN."""
    def run(*args, **kwargs):
        return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs)

    run("ip", "link", "set", "lo", "up")
    for device, peer in (("br-lan", "client0"), ("wan0", "wan-peer")):
        run("ip", "link", "add", device, "type", "veth", "peer", "name", peer)
        for name in (device, peer):
            run("ip", "link", "set", name, "addrgenmode", "none")
            run("ip", "link", "set", name, "up")
        run("ip", "-6", "addr", "add", "fe80::1/64", "dev", device, "nodad")
        run("ip", "-6", "addr", "add", "fe80::2/64", "dev", peer, "nodad")

    installed = "table inet fw4 {\n" + RULE.read_text(encoding="ascii") + "}\n"
    run("nft", "-c", "-f", "-", input=installed)
    run("nft", "-f", "-", input=installed)
    captures, senders = {}, {}
    for device, peer in (("br-lan", "client0"), ("wan0", "wan-peer")):
        capture = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x86dd))
        capture.bind((peer, 0))
        captures[device] = capture
        sender = socket.socket(socket.AF_INET6, socket.SOCK_RAW, socket.IPPROTO_RAW)
        sender.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, socket.if_nametoindex(device))
        senders[device] = sender

    count = 0

    def check(old, flags=0xc8, device="br-lan", source="fe80::1", hoplimit=255, kind=134, code=0):
        nonlocal count
        before = ra_packet(old, flags, source, hoplimit, kind, code)
        expected = bytearray(before)
        applies = device == "br-lan" and source.startswith("fe80:") and hoplimit == 255 and kind == 134 and code == 0 and flags & 0x18 == 8
        if applies:
            expected[45] &= 0xf7
            expected[42:44] = b"\0\0"
            pseudo = expected[8:40] + struct.pack("!I3xB", len(expected) - 40, 58)
            struct.pack_into("!H", expected, 42, checksum(pseudo + expected[40:]))
        capture = captures[device]
        while select.select([capture], [], [], 0)[0]:
            capture.recv(65535)
        senders[device].sendto(before, ("ff02::1", 0, 0, socket.if_nametoindex(device)))
        deadline = time.monotonic() + 2
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([capture], [], [], remaining)[0]:
                raise AssertionError("No matching packet reached the peer")
            packet = capture.recv(65535)[14:]
            if len(packet) == len(before) and packet[-4:] == before[-4:] and packet[40] == kind:
                break
        assert packet == expected, (hex(old), hex(flags), device, source, hoplimit, kind, code, packet.hex(), expected.hex())
        pseudo = packet[8:40] + struct.pack("!I3xB", len(packet) - 40, 58)
        assert checksum(pseudo + packet[40:]) == 0
        count += 1

    # Exercise every XOR carry branch, all exceptional map values and preserved
    # M/O/proxy flags through the actual kernel expressions and on-wire checksum.
    samples = [0, 1, 7, 8, 0x1234, 0xff00, *range(0xfff7, 0x10000)]
    samples += [(1 << bit) - 8 for bit in range(3, 16)]
    for flags in (0x08, 0x48, 0xc8, 0xec):
        for old in samples:
            check(old, flags)
    for flags in (0x00, 0x10, 0x18, 0xc0, 0xd0, 0xd8):
        check(0x1234, flags)
    for kwargs in ({"device": "wan0"}, {"source": "2001:db8::1"}, {"hoplimit": 64}, {"kind": 128}, {"code": 1}):
        check(0x1234, **kwargs)
    # fw4 reload replaces its table; the same persisted include must load again.
    run("nft", "delete", "table", "inet", "fw4")
    run("nft", "-f", "-", input=installed)
    check(0xfff7)
    check(0xfffd, 0xec)
    print(f"{count} actual kernel RA checks passed; checksum, scope and reload verified")


if __name__ == "__main__":
    if sys.argv[1:] == ["--kernel-worker"]:
        kernel_worker()
    else:
        unittest.main()
