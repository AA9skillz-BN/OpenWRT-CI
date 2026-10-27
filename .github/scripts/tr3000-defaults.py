#!/usr/bin/env python3
"""Set image defaults at build time; restored user UCI files take precedence."""
from pathlib import Path
import re


def configure(path):
    text = path.read_text()
    for option, value in {"sw_flow": "1", "hw_flow": "1", "sfe_flow": "0",
                          "hw_wed": "1", "bbr_cca": "1", "fullcone_nat": "1"}.items():
        pattern = rf"(?m)^(\s*option {option}\s+)'[^']*'\s*$"
        text, count = re.subn(pattern, rf"\g<1>'{value}'", text)
        if count != 1:
            raise ValueError(f"unexpected TurboACC default layout for {option}")
    path.write_text(text, newline="\n")


if __name__ == "__main__":
    configure(Path("package/luci-app-turboacc/root/etc/config/turboacc"))
