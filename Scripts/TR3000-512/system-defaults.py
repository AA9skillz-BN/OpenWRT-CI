#!/usr/bin/env python3
"""Configure TR3000 fresh-install Dnsmasq defaults without touching saved upgrades."""
import argparse
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("ap_sys_defaults", REPO / "Scripts/AP3000M/system-defaults.py")
shared = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shared)

DHCP_CONF = shared.DHCP_CONF
patch_dhcp_conf = shared.patch_dhcp_conf


def configure(tree):
    dhcp_path = tree / DHCP_CONF
    dhcp_patched = patch_dhcp_conf(dhcp_path.read_text(encoding="utf-8"))
    dhcp_path.write_text(dhcp_patched, encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, default=Path.cwd())
    args = parser.parse_args()
    configure(args.tree)
    print("TR3000 fresh-install Dnsmasq defaults: all_servers=1, cachesize=10000, min_cache_ttl=300, nonegcache=0")


if __name__ == "__main__":
    main()
