#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Explicit, interface-bound diagnostics. Does not change network state."""
import json
from pathlib import Path
import re
import subprocess


def run(args, timeout=6):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 124, ""


def read(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def snapshot():
    code, text = run(["ubus", "call", "network.interface", "dump"])
    data = json.loads(text) if code == 0 else {}
    interfaces = []
    code, configuration = run(['uci', '-q', 'show', 'network'])
    ethernet_sections = set(re.findall(r"^network\.([A-Za-z0-9_]+)\.device='eth1'$",
                                       configuration, re.MULTILINE)) if code == 0 else set()
    for item in data.get("interface", []):
        name, device = item.get("interface", ""), item.get("l3_device", "")
        if device != 'eth1' and name not in ethernet_sections and name not in (
                "wan", "wan6", "cell", "cellv6", "MT5700M", "MT5700Mv6"):
            continue
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", device):
            continue
        interfaces.append({"interface": name, "device": device, "up": item.get("up") is True,
                           "ipv4_addresses": len(item.get("ipv4-address", [])),
                           "ipv6_addresses": len(item.get("ipv6-address", [])),
                           "dns_servers": len(item.get("dns-server", [])),
                           "carrier": read("/sys/class/net/" + device + "/carrier")})
    probes = []
    for device in sorted({i["device"] for i in interfaces if i["up"]}):
        for family in (4, 6):
            attempts = []
            for target in ("https://www.baidu.com", "https://www.qq.com"):
                rc, status = run(["curl", "-" + str(family), "--interface", device,
                                  "--connect-timeout", "2", "--max-time", "3", "-s",
                                  "-o", "/dev/null", "-w", "%{http_code}", target], 4)
                attempts.append({"endpoint": target, "result": "reachable" if rc == 0 and
                                 re.fullmatch(r"[1-5][0-9]{2}", status) else
                                 "dns_failed" if rc == 6 else "connection_failed",
                                 "curl_code": rc})
                if attempts[-1]["result"] == "reachable":
                    break
            probes.append({"device": device, "family": family, "attempts": attempts})
    return {"interfaces": interfaces, "probes": probes,
            "forwarding_tested": False, "addresses_and_sim_identifiers_redacted": True,
            "slot_gpio": {str(i): read(f"/sys/class/gpio/cpe-sel{i}/value") for i in (0, 1)}}


if __name__ == "__main__":
    print(json.dumps(snapshot()))
