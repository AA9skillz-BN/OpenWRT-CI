#!/usr/bin/env python3
"""C8-scoped Arthur adaptation using the reviewed official-source importer."""
import hashlib
import importlib.util
from pathlib import Path
import sys

SPEC = importlib.util.spec_from_file_location("official_turboacc", Path(__file__).with_name("ap3000m-turboacc.py"))
official = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(official)
REPO, HASHES = official.REPO, official.HASHES
CORE, CONFIG, DEFAULTS = official.CORE, official.CONFIG, official.DEFAULTS


def patched(originals):
    updated = official.patched(originals)
    for name in (CORE, CONFIG):
        updated[name] = updated[name].replace(b'ap3000m_import_firewall', b'c8_import_firewall').replace(b'AP3000M migration', b'C8-688 migration')
    # No AP/TR tuning files belong to C8. Retire only old TurboACC UI files.
    cleanup = (REPO / 'Scripts/AP3000M/turboacc/cleanup-legacy.sh').read_text(encoding='utf8')
    c8_cleanup = '''# Retire exact legacy TurboACC UI paths without calling its stop action.
rm -f /etc/uci-defaults/luci-turboacc
rm -f /usr/lib/lua/luci/controller/turboacc.lua
rm -f /usr/lib/lua/luci/model/cbi/turboacc.lua
rm -f /usr/lib/lua/luci/view/turboacc/turboacc_status.htm
rm -f /tmp/luci-indexcache /tmp/luci-indexcache.*
rm -rf /tmp/luci-modulecache
'''
    updated[DEFAULTS] = official.shared.replace_once(updated[DEFAULTS].decode('utf8'), cleanup, c8_cleanup).encode('utf8')
    return updated


def configure(package):
    backup = package / '.c8-reviewed-originals'
    originals, current = {}, {}
    for name, expected in HASHES.items():
        saved = backup / (name + '.original')
        current[name] = (package / name).read_bytes()
        originals[name] = saved.read_bytes() if saved.exists() else current[name]
        if hashlib.sha256(originals[name]).hexdigest() != expected:
            raise ValueError(f'Arthur TurboACC source drift; review locked/latest source: {name}')
    updated = patched(originals)
    for name in HASHES:
        if current[name] not in (originals[name], updated[name]):
            raise ValueError(f'C8 TurboACC adapted file drift: {name}')
    for name in HASHES:
        saved = backup / (name + '.original')
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_bytes(originals[name])
        (package / name).write_bytes(updated[name])
    print('C8 Arthur TurboACC: reviewed settings import and migration installed')


if __name__ == '__main__':
    configure(Path(sys.argv[1]) if len(sys.argv) == 2 else Path('package/luci-app-turboacc'))
