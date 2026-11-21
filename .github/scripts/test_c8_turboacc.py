"""C8 Arthur package adaptation, preserved settings and actual LuCI installation."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import unittest
import urllib.request

import test_ap_turboacc as ap_tests
import test_turboacc as package_tests

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('c8_turboacc', Path(__file__).with_name('c8-turboacc.py'))
adapt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapt)


class C8TurboAccTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # C8 and AP use the same official-source firewall capability fixture.
        ap_tests.ApTurboAccTests.setUpClass.__func__(cls)

    setUp = package_tests.TurboAccTests.setUp

    def test_reviewed_package_is_repeatable_and_rejects_drift(self):
        adapt.configure(self.package)
        first = {name: (self.package / name).read_bytes() for name in adapt.HASHES}
        adapt.configure(self.package)
        self.assertEqual(first, {name: (self.package / name).read_bytes() for name in adapt.HASHES})
        self.assertNotIn(b'ap3000m', first[adapt.CONFIG] + first[adapt.CORE] + first[adapt.DEFAULTS])
        self.assertNotIn(b'tr3000-bbr', first[adapt.DEFAULTS])
        (self.package / adapt.CORE).write_bytes(first[adapt.CORE] + b'\n')
        before = {name: (self.package / name).read_bytes() for name in adapt.HASHES}
        with self.assertRaisesRegex(ValueError, 'drift'):
            adapt.configure(self.package)
        self.assertEqual(before, {name: (self.package / name).read_bytes() for name in adapt.HASHES})
        self.assertFalse(list((self.package / '.c8-reviewed-originals').rglob('Makefile')))

    def test_real_ucode_imports_c8_firewall_and_preserves_saved_off_choices(self):
        adapt.configure(self.package)
        template = dict(re.findall(r"option ([a-z0-9_]+) '([^']*)'", (self.package / adapt.CONFIG).read_text()))
        setup = "files['/proc/device-tree/compatible'] = 'nradio,c8-688\\u0000mediatek,mt7981\\u0000';\n"
        setup += "files['/sys/module/mt7915e/parameters/wed_enable'] = 'N';\n"
        setup += "store.firewall['@defaults[0]'] = {flow_offloading:'0', flow_offloading_hw:'0', fullcone:'1', fullcone6:'0', tcpcca:'reno'};\n"
        for settings in (template, dict(template, initialized='1', fullcone='0', fastpath='disabled', tcpcca='cubic')):
            actual = package_tests.TurboAccTests.run_core(self, settings, setup + 'defaults(); apply(); defaults(); apply();', original=True)
            cfg = actual['store']['turboacc']['config']
            self.assertEqual(cfg['fastpath'], 'disabled')
            self.assertEqual(cfg['tcpcca'], 'cubic' if settings.get('initialized') else 'reno')
            self.assertEqual(cfg['fullcone'], '0' if settings.get('initialized') else '1')
            self.assertNotIn('c8_import_firewall', cfg)
            self.assertFalse(any('modprobe tcp_bbr' in cmd for cmd in actual['commands']))

    @unittest.skipIf(os.name == 'nt', 'actual LuCI Make installation runs on Linux')
    def test_actual_c8_luci_install_and_required_image_assets(self):
        adapt.configure(self.package)
        lock = json.loads((REPO / 'Profiles/sources.lock.json').read_bytes())['C8-688']['feeds']['luci']
        raw = lock['url'].removesuffix('.git').replace('https://github.com/', 'https://raw.githubusercontent.com/') + '/' + lock['commit'] + '/luci.mk'
        with urllib.request.urlopen(raw, timeout=60) as response:
            luci = response.read()
        (self.top / 'feeds/luci').mkdir(parents=True)
        (self.top / 'feeds/luci/luci.mk').write_bytes(luci)
        (self.top / 'include').mkdir()
        (self.top / 'include/package.mk').touch()
        (self.top / 'rules.mk').write_text('INCLUDE_DIR:=$(TOPDIR)/include\nBUILD_DIR:=$(TOPDIR)/build\nCP:=cp -fpR\nINSTALL_DIR:=install -d\nPKG_PO_VERSION:=test\nPKG_SRC_VERSION:=test\nPKG_GITBRANCH:=test\n')
        (self.package / 'probe.mk').write_text('include Makefile\ndefine BuildPackage\nendef\n.PHONY: prepare install\ndefine Probe\nprepare:\n\t$(Build/Prepare)\ninstall:\n\t$(call Package/luci-app-turboacc/install,$(TOPDIR)/rootfs)\nendef\n$(eval $(Probe))\n')
        result = subprocess.run(['make', '--no-print-directory', '-f', 'probe.mk', f'TOPDIR={self.top}', 'prepare', 'install'], cwd=self.package, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        profile = json.loads((REPO / 'Profiles/C8-688.json').read_bytes())
        self.assertIn('turboacc', profile['packages'])
        required = {item['path']: item for item in profile['required_files']}
        for folder, prefix in (('root', '/'), ('htdocs', '/www/')):
            for file in (self.package / folder).rglob('*'):
                if file.is_file():
                    path = prefix + file.relative_to(self.package / folder).as_posix()
                    self.assertEqual((self.top / 'rootfs' / path.lstrip('/')).read_bytes(), file.read_bytes())
                    self.assertEqual(required[path]['build_source'], 'package/luci-app-turboacc/' + file.relative_to(self.package).as_posix())
        config = (REPO / 'Config/C8-688.txt').read_text() + (REPO / 'Config/BASE.txt').read_text()
        for name in ('luci-app-turboacc', 'luci-i18n-turboacc-zh-cn', 'firewall4', 'nftables-json', 'ucode', 'ucode-mod-fs', 'ucode-mod-uci', 'rpcd-mod-ucode', 'luci-base', 'kmod-tcp-bbr', 'kmod-nft-offload'):
            self.assertIn(name, profile['required_packages'])
            self.assertIn('CONFIG_PACKAGE_' + name + '=y', config)
        for path in profile['forbidden_files']:
            self.assertFalse((self.top / 'rootfs' / path.lstrip('/')).exists())
        self.assertIn('c8-turboacc.py', (REPO / 'Scripts/C8-688/private.sh').read_text(encoding='utf8'))
