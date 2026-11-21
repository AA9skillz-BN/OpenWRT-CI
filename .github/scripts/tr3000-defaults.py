#!/usr/bin/env python3
"""Adapt the reviewed Arthur TurboACC package without replacing firewall/kernel code."""
import hashlib
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
CORE = "root/usr/share/ucode/turboacc/core.uc"
CONFIG = "root/etc/config/turboacc"
DEFAULTS = "root/etc/uci-defaults/90-turboacc"
HASHES = {
    "Makefile": "32b53277ae839618528307ebd2e25a00d6067d5ee1e63361020931874425b6ea",
    CORE: "622546eca4e39b7de1fc0dfd2a39ac941a39d44c5ace8d7a234cc94bd4bbaaa3",
    CONFIG: "b656aea9f7f901bf1bc495c764b3ab056bdd62a235d620cd8a603c9b5461f83a",
    DEFAULTS: "e1ffb7e2f9b9a5aadf288a4785a1a01e3ccba3f6806e904668657a8e46c3c16e",
}


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError("review Arthur TurboACC adaptation anchors")
    return text.replace(old, new, 1)


def patched(originals):
    result = dict(originals)
    recipe = originals["Makefile"].decode("utf8")
    recipe = replace_once(recipe, "PKG_RELEASE:=1\n", "PKG_RELEASE:=2\n")
    recipe = replace_once(recipe, "LUCI_PKGARCH:=all\n",
        "LUCI_PKGARCH:=all\n# Keep installed assets exact for the final squashfs gate.\n"
        "LUCI_MINIFY_JS:=0\nLUCI_MINIFY_CSS:=0\n")
    result["Makefile"] = recipe.encode("utf8")
    core = originals[CORE].decode("utf8")
    # VIKINGYFY's SONiC backend uses a boolean fullcone option, whereas
    # Arthur stores 1=nft_fullcone / 2=masquerade-fullcone in its own config.
    core = replace_once(core, "\t\ttcpcca: false,\n", "\t\tfullcone_boolean: false,\n\t\ttcpcca: false,\n")
    core = replace_once(core, "\tcaps.fullcone6 =", "\tcaps.fullcone_boolean = match(src, /fullcone\\s*:\\s*\\[\\s*\"bool\"/) != null;\n\tcaps.fullcone6 =")
    core = replace_once(core, "\t\tdesired.fullcone = mode;", "\t\tdesired.fullcone = caps.fullcone_boolean && mode == '2' ? '1' : mode;")
    migration = (REPO / "Scripts/TR3000-512/turboacc/migrate.uc").read_text(encoding="utf8")
    core = replace_once(core, "function apply() {\n", migration + "\nfunction apply() {\n")
    core = replace_once(core, "function apply() {\n\tlet cursor = uci.cursor();\n",
        "function apply() {\n\tlet cursor = uci.cursor();\n"
        "\tmigrate_settings(cursor);\n"
        "\t// Load the selected module before discovering available algorithms.\n"
        "\tif (cursor.get(CONFIG, SECTION, 'tcpcca') == 'bbr' && index(read_cca(), 'bbr') < 0)\n"
        "\t\tsystem('modprobe tcp_bbr 2>/dev/null');\n")
    core = replace_once(core, "function defaults() {\n\tlet cursor = uci.cursor();\n",
        "function defaults() {\n\tlet cursor = uci.cursor();\n"
        "\tmigrate_settings(cursor);\n"
        "\tif (boolval(cursor.get(CONFIG, SECTION, 'initialized')))\n\t\treturn 0;\n")
    result[CORE] = core.encode("utf8")
    config = originals[CONFIG].decode("utf8")
    for old, new in (("option fastpath 'disabled'", "option fastpath 'flow_offloading'"),
                     ("option fastpath_fo_hw '0'", "option fastpath_fo_hw '1'"),
                     ("option fastpath_mh_eth_hnat_wed '0'", "option fastpath_mh_eth_hnat_wed '1'"),
                     ("option fullcone '0'", "option fullcone '2'"),
                     ("option tcpcca 'cubic'", "option tcpcca 'bbr'")):
        config = replace_once(config, old, new)
    result[CONFIG] = config.encode("utf8")
    defaults = originals[DEFAULTS].decode("utf8")
    cleanup = (REPO / "Scripts/TR3000-512/turboacc/cleanup-legacy.sh").read_text(encoding="utf8")
    defaults = replace_once(defaults, "# First install only:", cleanup + "\n# First install only:")
    result[DEFAULTS] = defaults.encode("utf8")
    return result


def configure(package):
    backup = package / ".tr3000-reviewed-originals"
    originals, current = {}, {}
    for name, expected in HASHES.items():
        saved = backup / (name + ".original")
        current[name] = (package / name).read_bytes()
        originals[name] = saved.read_bytes() if saved.exists() else current[name]
        if hashlib.sha256(originals[name]).hexdigest() != expected:
            raise ValueError(f"Arthur TurboACC source drift; review locked/latest source: {name}")
    updated = patched(originals)
    for name in HASHES:
        if current[name] not in (originals[name], updated[name]):
            raise ValueError(f"Arthur TurboACC adapted file drift: {name}")
    for name in HASHES:
        saved = backup / (name + ".original")
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(originals[name])
        (package / name).write_bytes(updated[name])
    print("Arthur TurboACC: preserved settings migration and on-demand BBR load installed")


if __name__ == "__main__":
    configure(Path(sys.argv[1]) if len(sys.argv) == 2 else Path("package/luci-app-turboacc"))
