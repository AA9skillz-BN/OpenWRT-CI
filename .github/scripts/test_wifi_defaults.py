"""Run the actual locked Wi-Fi generators and the shared Settings.sh entry point."""
from functools import lru_cache
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request

import yaml

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("wifi_defaults", Path(__file__).with_name("wifi-defaults.py"))
wifi = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(wifi)
BASH = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else None)
UCODE = os.environ.get("UCODE_TEST_BIN") or shutil.which("ucode")


@lru_cache(maxsize=1)
def upstreams():
    locks = json.loads((REPO / "Profiles/sources.lock.json").read_text(encoding="utf-8"))
    sources = {}
    for name in ("AP3000M", "TR3000-512"):
        item = locks[name]
        slug = item["url"].removeprefix("https://github.com/").removesuffix(".git")
        url = f"https://raw.githubusercontent.com/{slug}/{item['commit']}/{wifi.GENERATOR.as_posix()}"
        with urllib.request.urlopen(url, timeout=60) as response:
            sources[name] = response.read().decode("utf-8")
    return sources


def board():
    wlan = {}
    for index, band in enumerate(("2G", "5G")):
        wlan[f"phy{index}"] = {"path": f"platform/test/radio{index}", "info": {
            "bands": {band: {"max_width": 80, "he": True}}}}
    return {"wlan": wlan}


def assignments(output):
    values = {}
    for line in output.splitlines():
        if not line.startswith("set "):
            continue
        fields = shlex.split(line, posix=True)
        if len(fields) != 2:
            raise ValueError("Malformed UCI batch assignment: " + line)
        name, value = fields[1].split("=", 1)
        values[name] = value
    return values


class WifiPatchTests(unittest.TestCase):
    def test_both_locked_sources_patch_repeatably_without_changing_radio_detection(self):
        for name, source in upstreams().items():
            with self.subTest(source=name):
                updated = wifi.patch_generator(source, "Test WiFi", "12345678")
                self.assertEqual(updated, wifi.patch_generator(updated, "Test WiFi", "12345678"))
                # Only the three credential assignments change; detection,
                # regulatory settings and existing-radio skip logic stay intact.
                strip = lambda text: re.sub(r"^set \$\{si\}\.(ssid|encryption|key)=.*$", "", text, flags=re.M)
                self.assertEqual(strip(source), strip(updated))

    def test_invalid_credentials_preserve_original_file(self):
        source = upstreams()["AP3000M"]
        with tempfile.TemporaryDirectory() as directory:
            tree = Path(directory)
            file = tree / wifi.GENERATOR
            file.parent.mkdir(parents=True)
            file.write_text(source, encoding="utf-8", newline="\n")
            for ssid, password in (("", "12345678"), ("x" * 33, "12345678"),
                                   ("\ncommand", "12345678"), ("中" * 11, "12345678"),
                                   ("Test", ""), ("Test", "short"), ("Test", "x" * 64),
                                   ("Test", "pass\nword"), ("Test", "密码12345678")):
                with self.subTest(ssid=ssid, length=len(password)), self.assertRaises(ValueError):
                    wifi.configure(tree, ssid, password)
                self.assertEqual(file.read_text(encoding="utf-8"), source)

    def test_missing_duplicate_or_reordered_anchors_fail_before_write(self):
        source = upstreams()["AP3000M"]
        for broken in (source.replace("set ${si}.key=", "set ${si}.passphrase="),
                       source + "\nset ${si}.key='duplicate'\n",
                       source.replace("set ${si}.encryption=", "# relocated\nset ${si}.encryption=")):
            with self.subTest(source_length=len(broken)), self.assertRaisesRegex(ValueError, "review upstream"):
                wifi.patch_generator(broken, "Test", "12345678")

    def test_ap3000m_radio_defaults_patch_repeatably_and_reject_anchor_drift(self):
        source = upstreams()["AP3000M"]
        updated = wifi.patch_generator(source, "AP3000M", "12345678", ap3000m_radio_defaults=True)
        self.assertEqual(updated, wifi.patch_generator(updated, "AP3000M", "12345678", ap3000m_radio_defaults=True))
        self.assertNotIn("bss_transition", updated)
        self.assertIn("sae-mixed", updated)
        self.assertIn("mu_beamformer='1'", updated)
        self.assertIn("country || 'CN'", updated)
        for broken in (source.replace("width > 80", "width > 40"),
                       source.replace("set ${s}.country='${country || ''}'", "set ${s}.country='00'")):
            with self.assertRaisesRegex(ValueError, "review upstream"):
                wifi.patch_generator(broken, "AP3000M", "12345678", ap3000m_radio_defaults=True)

    def test_every_device_image_checks_the_installed_generator_bytes(self):
        for name in ("AP3000M", "C8-688", "TR3000-512"):
            data = json.loads((REPO / f"Profiles/{name}.json").read_text(encoding="utf-8"))
            items = [item for item in data["required_files"] if item["path"] == "/lib/wifi/mac80211.uc"]
            self.assertEqual(items, [{"path": "/lib/wifi/mac80211.uc", "build_source": wifi.GENERATOR.as_posix()}])

    @unittest.skipUnless(BASH, "Bash required")
    def test_settings_entry_point_uses_each_workflows_credentials_and_propagates_failure(self):
        for workflow in ("AP3000M", "C8-688", "TR3000"):
            data = yaml.safe_load((REPO / f".github/workflows/{workflow}.yml").read_text(encoding="utf-8"))
            options = next(iter(data["jobs"].values()))["with"]
            profile = options["WRT_PROFILE"]
            source = upstreams()["TR3000-512" if profile == "TR3000-512" else "AP3000M"]
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as directory:
                tree = Path(directory)
                generator = tree / wifi.GENERATOR
                generator.parent.mkdir(parents=True)
                generator.write_text(source, encoding="utf-8", newline="\n")
                for folder in ("feeds/luci/collections", "feeds/luci/modules/luci-mod-system",
                               "feeds/luci/modules/luci-mod-status", "package/base-files/files/bin"):
                    (tree / folder).mkdir(parents=True)
                (tree / "package/base-files/files/bin/config_generate").write_text("hostname='OpenWrt'\n192.168.1.1\n")
                (tree / ".config").touch()
                env = dict(os.environ, GITHUB_WORKSPACE=REPO.as_posix(), WRT_PROFILE=profile,
                           WRT_SSID=options["WRT_SSID"], WRT_WORD=str(options["WRT_WORD"]),
                           WRT_IP=options["WRT_IP"], WRT_NAME=options["WRT_NAME"],
                           WRT_THEME=options["WRT_THEME"], WRT_MARK="fixture", WRT_DATE="fixture",
                           WRT_PACKAGE="", WRT_TARGET="mediatek", WRT_WIFI="none",
                           WIFI_TEST_PYTHON=Path(sys.executable).as_posix(),
                           WIFI_TEST_SETTINGS=(REPO / "Scripts/Settings.sh").as_posix())
                command = 'python3() { "$WIFI_TEST_PYTHON" "$@"; }; source "$WIFI_TEST_SETTINGS"'
                result = subprocess.run([BASH, "-ec", command], cwd=tree, env=env,
                                        text=True, capture_output=True, encoding="utf-8")
                self.assertEqual(result.returncode, 0, result.stderr)
                expected = wifi.patch_generator(
                    source,
                    options["WRT_SSID"],
                    str(options["WRT_WORD"]),
                    profile == "C8-688",
                    ap3000m_radio_defaults=(profile == "AP3000M"),
                )
                self.assertEqual(generator.read_text(encoding="utf-8"), expected)
                # Settings.sh must not hide a missing/changed upstream generator.
                config_before_failure = (tree / ".config").read_bytes()
                generator.write_text("unknown upstream layout\n")
                result = subprocess.run([BASH, "-ec", command], cwd=tree, env=env,
                                        text=True, capture_output=True, encoding="utf-8")
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((tree / ".config").read_bytes(), config_before_failure)


@unittest.skipUnless(UCODE, "ucode runtime required (installed by Linux CI)")
class WifiGeneratorTests(unittest.TestCase):
    def test_c8_reference_band_names_and_existing_settings(self):
        data = yaml.safe_load((REPO / '.github/workflows/C8-688.yml').read_text(encoding='utf-8'))
        options = next(iter(data['jobs'].values()))['with']
        source = wifi.patch_generator(upstreams()['AP3000M'], options['WRT_SSID'], str(options['WRT_WORD']), True)
        generated = assignments(self.execute(source))
        self.assertEqual(generated['wireless.default_radio0.ssid'], 'NRadio-C8-688-2.4G')
        self.assertEqual(generated['wireless.default_radio1.ssid'], 'NRadio-C8-688-5G')
        saved = {f'radio{i}': {'.type': 'wifi-device', 'path': f'platform/test/radio{i}'} for i in (0, 1)}
        self.assertEqual(self.execute(source, saved), '')

    def execute(self, source, saved=None, hardware=None):
        # Replace the actual fs/UCI reads and remove the loader's shebang before
        # prepending fixtures. Execute all upstream discovery and UCI rendering.
        hardware = hardware or board()
        harness = """
function readfile(path) {
 if (path == '/etc/board.json') return HARDWARE;
 if (index(path, '/sys/class/ieee80211/') == 0) return '02:11:22:33:44:55';
 die('Unexpected file read: ' + path);
}
const uci = { cursor: function() { return { get_all: function() { return SAVED; } }; } };
""".replace("HARDWARE", json.dumps(json.dumps(hardware))).replace("SAVED", json.dumps(saved or {}))
        source = source.replace('import { readfile } from "fs";', "").replace("import * as uci from 'uci';", "")
        if source.startswith("#!"):
            source = source.split("\n", 1)[1]
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "generate.uc"
            file.write_text(harness + source, encoding="utf-8", newline="\n")
            result = subprocess.run([UCODE, str(file)], text=True, capture_output=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_original_official_source_and_old_sed_reproduce_the_open_wifi_bug(self):
        source = upstreams()["AP3000M"]
        old = re.sub(r"ssid '.*'", "ssid 'AP3000M'", source)
        old = re.sub(r"key '.*'", "key '12345678'", old)
        self.assertEqual(old, source)
        generated = assignments(self.execute(old))
        for index in (0, 1):
            self.assertEqual(generated[f"wireless.default_radio{index}.encryption"], "none")
            self.assertEqual(generated[f"wireless.default_radio{index}.key"], "")

    def test_new_24_and_5ghz_networks_use_the_requested_credentials(self):
        for name in ("AP3000M", "C8-688", "TR3000-512"):
            source = upstreams()["TR3000-512" if name == "TR3000-512" else "AP3000M"]
            with self.subTest(profile=name):
                generated = assignments(self.execute(wifi.patch_generator(source, name, "12345678")))
                for index, band in ((0, "2g"), (1, "5g")):
                    self.assertEqual(generated[f"wireless.default_radio{index}.ssid"], name)
                    self.assertEqual(generated[f"wireless.default_radio{index}.key"], "12345678")
                    self.assertEqual(generated[f"wireless.default_radio{index}.encryption"], "psk2+ccmp")
                    self.assertEqual(generated[f"wireless.radio{index}.band"], band)
                    self.assertEqual(generated[f"wireless.default_radio{index}.disabled"], "0")

    def test_quoted_values_are_data_not_ucode_or_uci_commands(self):
        ssid = "路由器 ' \\ ${1} `test`"
        password = "a'\"\\${1}`test`&pass"
        updated = wifi.patch_generator(upstreams()["AP3000M"], ssid, password)
        generated = assignments(self.execute(updated))
        self.assertEqual(generated["wireless.default_radio0.ssid"], ssid)
        self.assertEqual(generated["wireless.default_radio0.key"], password)

    def test_saved_radios_and_security_are_not_rewritten(self):
        saved = {}
        for index in (0, 1):
            saved[f"radio{index}"] = {".type": "wifi-device", "path": f"platform/test/radio{index}"}
            saved[f"custom{index}"] = {".type": "wifi-iface", "ssid": "User network",
                                      "encryption": "sae-mixed", "key": "Existing user password"}
        original = json.dumps(saved, sort_keys=True)
        self.assertEqual(self.execute(wifi.patch_generator(upstreams()["AP3000M"], "New", "12345678"), saved), "")
        self.assertEqual(json.dumps(saved, sort_keys=True), original)
        # Even a deliberately saved open radio is not silently changed on upgrade.
        saved["custom0"]["encryption"] = "none"
        self.assertEqual(self.execute(wifi.patch_generator(upstreams()["AP3000M"], "New", "12345678"), saved), "")

    def test_board_defaults_cannot_restore_open_security_on_new_radios(self):
        hardware = board()
        hardware["wlan"]["defaults"] = {"country": "CN", "ssids": {
            "all": {"ssid": "Open board default", "encryption": "none", "key": ""}}}
        generated = assignments(self.execute(wifi.patch_generator(upstreams()["AP3000M"], "New", "12345678"), hardware=hardware))
        for index in (0, 1):
            self.assertEqual(generated[f"wireless.default_radio{index}.encryption"], "psk2+ccmp")
            self.assertEqual(generated[f"wireless.default_radio{index}.ssid"], "New")
            self.assertEqual(generated[f"wireless.radio{index}.country"], "CN")

    def test_6ghz_never_receives_wpa2_security(self):
        hardware = {"wlan": {"phy0": {"path": "platform/test/radio0", "info": {
            "bands": {"6G": {"max_width": 160, "he": True}}}}}}
        generated = assignments(self.execute(wifi.patch_generator(upstreams()["AP3000M"], "New", "12345678"), hardware=hardware))
        self.assertEqual(generated["wireless.default_radio0.encryption"], "sae")

    def test_ap3000m_new_radios_enable_cn_he160_beamformer_and_sae_mixed_without_touching_saved(self):
        hardware = {
            "wlan": {
                "phy0": {"path": "platform/soc/18000000.wifi", "info": {"bands": {"2G": {"max_width": 40, "he": True}}}},
                "phy1": {"path": "platform/soc/18000000.wifi+1", "info": {"bands": {"5G": {"max_width": 160, "he": True}}}},
            }
        }
        patched = wifi.patch_generator(upstreams()["AP3000M"], "AP3000M", "12345678", ap3000m_radio_defaults=True)
        generated = assignments(self.execute(patched, hardware=hardware))
        self.assertEqual(generated["wireless.radio0.country"], "CN")
        self.assertEqual(generated["wireless.radio0.htmode"], "HE20")
        self.assertEqual(generated["wireless.default_radio0.encryption"], "psk2+ccmp")
        self.assertNotIn("wireless.radio0.mu_beamformer", generated)
        self.assertEqual(generated["wireless.radio1.country"], "CN")
        self.assertEqual(generated["wireless.radio1.htmode"], "HE160")
        self.assertEqual(generated["wireless.radio1.mu_beamformer"], "1")
        self.assertEqual(generated["wireless.default_radio1.encryption"], "sae-mixed")
        self.assertFalse(any("bss_transition" in key for key in generated))
        saved = {
            "radio0": {".type": "wifi-device", "path": "platform/soc/18000000.wifi"},
            "radio1": {".type": "wifi-device", "path": "platform/soc/18000000.wifi+1"},
        }
        self.assertEqual(self.execute(patched, saved=saved, hardware=hardware), "")


if __name__ == "__main__":
    unittest.main()
