#!/usr/bin/env python3
"""Check TR3000's resolved configuration and optional image build outputs."""

import argparse
import json
from pathlib import Path


# Keep one checklist for both defconfig and the packages installed in the image.
REQUIRED_PACKAGES = (
    "luci", "luci-ssl", "luci-theme-aurora", "luci-app-aurora-config",
    "luci-app-openclash", "luci-app-turboacc", "luci-compat",
    "ttyd", "luci-app-ttyd",
    "luci-i18n-base-zh-cn", "luci-i18n-firewall-zh-cn",
    "luci-i18n-package-manager-zh-cn", "luci-i18n-aurora-config-zh-cn",
    "luci-i18n-turboacc-zh-cn", "luci-i18n-ttyd-zh-cn",
    "kmod-mt7915e", "kmod-mt7981-firmware", "mt7981-wo-firmware",
    "kmod-nft-offload", "kmod-usb-net-rndis", "kmod-usb-net-cdc-ether",
    "kmod-usb-net-cdc-ncm", "usbutils",
    "bash", "curl", "ca-bundle", "ip-full", "kmod-tun", "kmod-inet-diag",
    "unzip", "ruby", "ruby-yaml", "kmod-nft-tproxy", "kmod-nft-socket",
    "kmod-nft-nat", "dnsmasq-full",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--metadata", type=Path)
    args = parser.parse_args()

    config = set(args.config.read_text(encoding="utf-8").splitlines())
    required = [f"CONFIG_PACKAGE_{package}=y" for package in REQUIRED_PACKAGES]
    required.extend((
        "CONFIG_TARGET_mediatek_filogic_DEVICE_cudy_tr3000-v1=y",
        "CONFIG_LUCI_LANG_zh_Hans=y",
        "CONFIG_PACKAGE_luci-app-turboacc_INCLUDE_OFFLOADING=y",
        "CONFIG_CCACHE=y",
    ))
    missing = [symbol for symbol in required if symbol not in config]
    if missing:
        parser.exit(1, "Required configuration missing after defconfig:\n" + "\n".join(missing) + "\n")
    print("TR3000 configuration checks passed.")

    if args.manifest:
        installed = {
            line.split(" - ", 1)[0]
            for line in args.manifest.read_text(encoding="utf-8").splitlines()
            if " - " in line
        }
        missing = sorted(set(REQUIRED_PACKAGES) - installed)
        if missing:
            parser.exit(1, "Required packages missing from image:\n" + "\n".join(missing) + "\n")
        print(f"TR3000 image contains all {len(REQUIRED_PACKAGES)} required packages.")

    if args.metadata:
        metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
        if "cudy,tr3000-v1" not in metadata.get("supported_devices", []):
            parser.exit(1, "Image metadata does not support cudy,tr3000-v1.\n")
        print("TR3000 sysupgrade metadata checks passed.")


if __name__ == "__main__":
    main()
