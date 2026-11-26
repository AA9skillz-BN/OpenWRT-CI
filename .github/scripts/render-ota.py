#!/usr/bin/env python3
"""Render the standalone device OTA clients from shared shell functions."""
import argparse
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
DEVICES = {"AP3000M": "ap3000m-ota", "TR3000-512": "tr3000-ota"}
MARKER = r"^# @ota ([a-z_]+)\n"


def render():
    shared = (ROOT / "Scripts/ota/common.sh.in").read_text(encoding="utf-8")
    header, *sections = re.split(MARKER, shared, flags=re.MULTILINE)
    if header.strip() or len(sections) % 2 or len(set(sections[::2])) != len(sections) // 2:
        raise ValueError("Invalid shared OTA section markers")
    fragments = dict(zip(sections[::2], sections[1::2]))
    for device, cli in DEVICES.items():
        output = ROOT / "Scripts" / device / "ota" / cli
        template = output.with_suffix(".in").read_text(encoding="utf-8")
        names = re.findall(MARKER, template, flags=re.MULTILINE)
        if len(names) != len(fragments) or set(names) != fragments.keys():
            raise ValueError(f"{device}: template must include each shared section once")
        shell = re.sub(MARKER, lambda match: fragments[match[1]], template, flags=re.MULTILINE)
        yield output, shell.replace("@OTA_CLI@", cli).replace("@OTA_UCI@", cli.replace("-", "_")).encode("utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Check that tracked standalone clients match their sources")
    args = parser.parse_args()
    for path, content in render():
        if args.check:
            if content != path.read_bytes():
                raise SystemExit(f"{path.relative_to(ROOT)} differs from its source. Run python3 .github/scripts/render-ota.py")
        else:
            path.write_bytes(content)


if __name__ == "__main__":
    main()
