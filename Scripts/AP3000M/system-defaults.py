#!/usr/bin/env python3
"""Configure AP3000M fresh-install system and Dnsmasq defaults without touching saved upgrades."""
import argparse
from pathlib import Path
import re

CONFIG_GENERATE = Path("package/base-files/files/bin/config_generate")
DHCP_CONF = Path("package/network/services/dnsmasq/files/dhcp.conf")

TZ_OLD = (
    "\t\tset system.@system[-1].timezone='GMT0'\n"
    "\t\tset system.@system[-1].zonename='UTC'\n"
)
TZ_NEW = (
    "\t\tset system.@system[-1].timezone='CST-8'\n"
    "\t\tset system.@system[-1].zonename='Asia/Shanghai'\n"
    "\t\tset system.@system[-1].cronloglevel='9'\n"
)

NTP_SERVERS = (
    "ntp.aliyun.com",
    "ntp.tencent.com",
    "cn.pool.ntp.org",
    "time.cloudflare.com",
)
NTP_NEW = (
    "\t\tset system.ntp.enable_server='0'\n"
    + "".join(f"\t\tadd_list system.ntp.server='{srv}'\n" for srv in NTP_SERVERS)
)
NTP_PATTERN = re.compile(
    r"^\t\tset system\.ntp\.enable_server='0'\n(?:\t\tadd_list system\.ntp\.server='[^']+'\n)+",
    re.MULTILINE,
)


def patch_config_generate(source):
    # config_generate only runs when /etc/config/system is absent (fresh flash
    # or factory reset), so config-preserving sysupgrades keep user settings.
    text = source
    if TZ_NEW not in text:
        if text.count(TZ_OLD) != 1:
            raise ValueError("config_generate timezone anchors changed; review upstream")
        text = text.replace(TZ_OLD, TZ_NEW, 1)

    matches = NTP_PATTERN.findall(text)
    if len(matches) != 1:
        raise ValueError("config_generate NTP server block changed; review upstream")
    text = NTP_PATTERN.sub(NTP_NEW, text, count=1)
    return text


def patch_dhcp_conf(source):
    # ImmortalWrt sets min_cache_ttl=3600, locking short-TTL CDN/DDNS records
    # for an hour; OpenWrt mainline omits min_cache_ttl. Normalize to 300s.
    if re.search(r"^\toption min_cache_ttl\t300[ \t]*$", source, re.MULTILINE):
        return source
    immortal_matches = re.findall(r"^\toption min_cache_ttl\t3600[ \t]*$", source, re.MULTILINE)
    if len(immortal_matches) == 1:
        return re.sub(
            r"^\toption min_cache_ttl\t3600[ \t]*$",
            "\toption min_cache_ttl\t300",
            source,
            count=1,
            flags=re.MULTILINE,
        )
    cachesize_matches = re.findall(r"^\toption cachesize\t[0-9]+[ \t]*$", source, re.MULTILINE)
    if len(cachesize_matches) == 1 and "min_cache_ttl" not in source:
        return re.sub(
            r"^(\toption cachesize\t[0-9]+)[ \t]*$",
            r"\1\n\toption min_cache_ttl\t300",
            source,
            count=1,
            flags=re.MULTILINE,
        )
    raise ValueError("dhcp.conf cache anchors changed; review upstream")


def configure(tree):
    cg_path = tree / CONFIG_GENERATE
    dhcp_path = tree / DHCP_CONF
    cg_patched = patch_config_generate(cg_path.read_text(encoding="utf-8"))
    dhcp_patched = patch_dhcp_conf(dhcp_path.read_text(encoding="utf-8"))
    cg_path.write_text(cg_patched, encoding="utf-8", newline="\n")
    dhcp_path.write_text(dhcp_patched, encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, default=Path.cwd())
    args = parser.parse_args()
    configure(args.tree)
    print("AP3000M fresh-install defaults: CST-8/Asia/Shanghai, domestic NTP, cronloglevel=9, min_cache_ttl=300")


if __name__ == "__main__":
    main()
