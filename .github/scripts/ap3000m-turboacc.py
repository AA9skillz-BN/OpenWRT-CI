#!/usr/bin/env python3
"""Reuse the reviewed Arthur package adaptation with AP-specific first-install import."""
import hashlib
import importlib.util
from pathlib import Path
import sys

SPEC = importlib.util.spec_from_file_location("reviewed_turboacc", Path(__file__).with_name("tr3000-defaults.py"))
shared = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shared)
REPO = shared.REPO
HASHES = shared.HASHES
CORE, CONFIG, DEFAULTS = shared.CORE, shared.CONFIG, shared.DEFAULTS


def patched(originals):
    updated = shared.patched(originals)
    tr = REPO / "Scripts/TR3000-512/turboacc"
    ap = REPO / "Scripts/AP3000M/turboacc"
    migration = (tr / "migrate.uc").read_text(encoding="utf8")
    ap_migration = migration.replace("// TR3000 migration", "// AP3000M migration", 1)
    ap_migration = shared.replace_once(ap_migration, "\tlet changed = false;\n",
                                       "\tlet changed = import_ap_defaults(cursor);\n")
    core = updated[CORE].decode("utf8")
    core = shared.replace_once(core, migration, (ap / "import-defaults.uc").read_text(encoding="utf8") + "\n" + ap_migration)
    updated[CORE] = core.encode("utf8")
    config = updated[CONFIG].decode("utf8")
    config = shared.replace_once(config, "option fullcone '2'", "option fullcone '1'")
    # Adopt WED from the actual driver on first install, rather than enabling
    # it universally. Saved modern/legacy choices bypass this import.
    config = shared.replace_once(config, "option fastpath_mh_eth_hnat_wed '1'", "option fastpath_mh_eth_hnat_wed '0'")
    config = shared.replace_once(config, "config turboacc 'config'\n",
                                  "config turboacc 'config'\n\toption ap3000m_import_firewall '1'\n")
    updated[CONFIG] = config.encode("utf8")
    defaults = shared.replace_once(updated[DEFAULTS].decode("utf8"),
        (tr / "cleanup-legacy.sh").read_text(encoding="utf8"),
        (ap / "cleanup-legacy.sh").read_text(encoding="utf8"))
    updated[DEFAULTS] = defaults.encode("utf8")
    return updated


def configure(package):
    backup = package / ".ap3000m-reviewed-originals"
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
            raise ValueError(f"AP3000M TurboACC adapted file drift: {name}")
    for name in HASHES:
        saved = backup / (name + ".original")
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(originals[name])
        (package / name).write_bytes(updated[name])
    print("AP3000M Arthur TurboACC: reviewed defaults import, migration and cleanup installed")


if __name__ == "__main__":
    configure(Path(sys.argv[1]) if len(sys.argv) == 2 else Path("package/luci-app-turboacc"))
