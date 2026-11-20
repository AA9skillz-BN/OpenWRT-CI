#!/usr/bin/env python3
"""Set credentials in the generator for new radios; leave saved UCI config alone."""
import argparse
import json
import os
from pathlib import Path
import re

GENERATOR = Path("package/network/config/wifi-scripts/files/lib/wifi/mac80211.uc")
FIELDS = ("ssid", "encryption", "key")


def uci_literal(value):
    # The ucode template emits UCI batch commands, so quote for UCI first,
    # then encode the complete value as an ordinary ucode string expression.
    quoted = "'" + value.replace("'", "'\\''") + "'"
    return "${" + json.dumps(quoted, ensure_ascii=True) + "}"


def patch_generator(source, ssid, password, band_suffix=False):
    if not 1 <= len(ssid.encode("utf-8")) <= (27 if band_suffix else 32) or any(ord(char) < 32 or ord(char) == 127 for char in ssid):
        raise ValueError("Wi-Fi SSID must be 1..32 UTF-8 bytes without control characters")
    if not 8 <= len(password) <= 63 or any(not 32 <= ord(char) <= 126 for char in password):
        raise ValueError("Wi-Fi passphrase must be 8..63 printable ASCII characters")
    rows = {field: re.findall(r"^set \$\{si\}\." + field + r"=.*$", source, re.MULTILINE)
            for field in FIELDS}
    if any(len(lines) != 1 for lines in rows.values()):
        raise ValueError("Wi-Fi generator credential anchors changed; review upstream")
    block = "\n".join(rows[field][0] for field in FIELDS)
    if source.count(block) != 1:
        raise ValueError("Wi-Fi generator credential block changed; review upstream")
    ssid_value = uci_literal(ssid)
    if band_suffix:
        # Reuse UCI quoting for each complete name before selecting its band.
        names = [uci_literal(ssid + suffix)[2:-1] for suffix in ("-2.4G", "-5G", "-6G")]
        ssid_value = '${band_name == "2g" ? %s : band_name == "5g" ? %s : %s}' % tuple(names)
    replacement = "\n".join([
        "set ${si}.ssid=" + ssid_value,
        "set ${si}.encryption='${band_name == \"6g\" ? \"sae\" : \"psk2+ccmp\"}'",
        "set ${si}.key=" + uci_literal(password),
    ])
    return source.replace(block, replacement, 1)


def configure(tree, ssid, password, band_suffix=False):
    path = tree / GENERATOR
    source = path.read_text(encoding="utf-8")
    patched = patch_generator(source, ssid, password, band_suffix)
    # All validation precedes writing; no key or passphrase is printed.
    path.write_text(patched, encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, default=Path.cwd())
    parser.add_argument("--band-suffix", action="store_true", help="Append -2.4G/-5G/-6G to new network names")
    args = parser.parse_args()
    configure(args.tree, os.environ["WRT_SSID"], os.environ["WRT_WORD"], args.band_suffix)
    print("New Wi-Fi radios: configured SSID and passphrase; WPA2-PSK/CCMP (SAE on 6 GHz); existing radios preserved")


if __name__ == "__main__":
    main()
