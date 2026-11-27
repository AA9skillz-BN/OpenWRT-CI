#!/usr/bin/env python3
"""Set credentials in the generator for new radios; leave saved UCI config alone."""
import argparse
import json
import os
from pathlib import Path
import re

GENERATOR = Path("package/network/config/wifi-scripts/files/lib/wifi/mac80211.uc")
FIELDS = ("ssid", "encryption", "key")
WIDTH_OLD = "\t\telse if (width > 80)\n\t\t\twidth = 80;"
COUNTRY_OLD = "set ${s}.country='${country || ''}'"
COUNTRY_AP3000M = "set ${s}.country='${country || 'CN'}'"


TR3000_IFACE = """			set_section(iface, 'wifi-iface', {
				device: name, network: 'lan', mode: 'ap',
				ssid: defaults?.ssid || (ap_defaults ? default_ssid(band_name, rband.default_channel ?? band.default_channel) : 'OWRT'),
				encryption: defaults?.encryption || (sae ? 'sae+ccmp' : 'psk2+ccmp'),
				ieee80211w: sae ? '2' : null,
				key: defaults?.key || (ap_defaults && preset ? preset.key : '12345678'), disabled: needs_country ? '1' : '0'
			});"""
TR3000_IFACE_DEFAULTS = """			set_section(iface, 'wifi-iface', {{
				device: name, network: 'lan', mode: 'ap',
				ssid: {ssid},
				encryption: band_name == '6G' ? 'sae+ccmp' : 'psk2+ccmp',
				ieee80211w: band_name == '6G' ? '2' : null,
				key: {key}, disabled: needs_country ? '1' : '0'
			}});"""


def uci_literal(value):
    # The ucode template emits UCI batch commands, so quote for UCI first,
    # then encode the complete value as an ordinary ucode string expression.
    quoted = "'" + value.replace("'", "'\\''") + "'"
    return "${" + json.dumps(quoted, ensure_ascii=True) + "}"


def patch_generator(source, ssid, password, ap3000m_radio_defaults=False):
    if not 1 <= len(ssid.encode("utf-8")) <= 32 or any(ord(char) < 32 or ord(char) == 127 for char in ssid):
        raise ValueError("Wi-Fi SSID must be 1..32 UTF-8 bytes without control characters")
    if not 8 <= len(password) <= 63 or any(not 32 <= ord(char) <= 126 for char in password):
        raise ValueError("Wi-Fi passphrase must be 8..63 printable ASCII characters")
    if not ap3000m_radio_defaults and "set_section(iface, 'wifi-iface', {" in source:
        replacement = TR3000_IFACE_DEFAULTS.format(ssid=json.dumps(ssid, ensure_ascii=False),
                                                  key=json.dumps(password, ensure_ascii=False))
        if source.count("set_section(iface, 'wifi-iface', {") != 1:
            raise ValueError("TR3000 Wi-Fi interface block duplicated; review upstream")
        if replacement in source:
            return source
        if source.count(TR3000_IFACE) != 1:
            raise ValueError("TR3000 Wi-Fi interface block changed; review upstream")
        return source.replace(TR3000_IFACE, replacement, 1)
    text = source
    if ap3000m_radio_defaults:
        # Keep upstream 80 MHz cap (HE80) and omit mu_beamformer to preserve
        # MT7981 WED + bridger stability; bss_transition must not be emitted.
        if text.count(WIDTH_OLD) != 1:
            raise ValueError("Wi-Fi generator bandwidth anchor changed; review upstream")
        if COUNTRY_AP3000M not in text:
            if text.count(COUNTRY_OLD) != 1:
                raise ValueError("Wi-Fi generator country anchor changed; review upstream")
            text = text.replace(COUNTRY_OLD, COUNTRY_AP3000M, 1)
    rows = {field: re.findall(r"^set \$\{si\}\." + field + r"=.*$", text, re.MULTILINE)
            for field in FIELDS}
    if any(len(lines) != 1 for lines in rows.values()):
        raise ValueError("Wi-Fi generator credential anchors changed; review upstream")
    block = "\n".join(rows[field][0] for field in FIELDS)
    if text.count(block) != 1:
        raise ValueError("Wi-Fi generator credential block changed; review upstream")
    ssid_value = uci_literal(ssid)
    encryption_expr = (
        "set ${si}.encryption='${band_name == \"6g\" ? \"sae\" : band_name == \"5g\" ? \"sae-mixed\" : \"psk2+ccmp\"}'"
        if ap3000m_radio_defaults else
        "set ${si}.encryption='${band_name == \"6g\" ? \"sae\" : \"psk2+ccmp\"}'"
    )
    replacement = "\n".join([
        "set ${si}.ssid=" + ssid_value,
        encryption_expr,
        "set ${si}.key=" + uci_literal(password),
    ])
    return text.replace(block, replacement, 1)


def configure(tree, ssid, password, ap3000m_radio_defaults=False):
    path = tree / GENERATOR
    source = path.read_text(encoding="utf-8")
    patched = patch_generator(source, ssid, password, ap3000m_radio_defaults)
    # All validation precedes writing; no key or passphrase is printed.
    path.write_text(patched, encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path, default=Path.cwd())
    parser.add_argument("--ap3000m-radio-defaults", action="store_true",
                        help="Default new AP3000M radios to CN, 5G HE80 and WPA2/WPA3 mixed mode")
    args = parser.parse_args()
    configure(args.tree, os.environ["WRT_SSID"], os.environ["WRT_WORD"],
              args.ap3000m_radio_defaults)
    print("New Wi-Fi radios: configured SSID and passphrase; existing radios preserved")


if __name__ == "__main__":
    main()
