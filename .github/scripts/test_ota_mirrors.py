"""Exercise both OTA downloaders with the locked OpenWrt shell/UCI libraries."""
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import urllib.request

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else None)
JQ = os.environ.get("OTA_TEST_JQ") or shutil.which("jq")


@lru_cache(maxsize=1)
def libraries():
    lock = json.loads((REPO / "Profiles/sources.lock.json").read_text(encoding="utf-8"))["C8-688"]
    slug = lock["url"].removeprefix("https://github.com/").removesuffix(".git")
    base = f"https://raw.githubusercontent.com/{slug}/{lock['commit']}/"
    data = {}
    for name, package in (("lib/functions.sh", "base-files"), ("lib/config/uci.sh", "system/uci")):
        with urllib.request.urlopen(base + f"package/{package}/files/" + name, timeout=60) as response:
            data[name] = response.read().decode("utf-8")
    return data


@unittest.skipUnless(BASH and JQ, "Bash and jq required")
class OtaMirrorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        libraries()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_download(self, model, *, mode="mirrors", broken=False, corrupt=False, shell=None):
        device = "c8-688" if model == "C8-688" else "ap3000m"
        config = "c8_688_ota" if model == "C8-688" else "ap3000m_ota"
        source = (REPO / f"Scripts/{model}/ota/{device}-ota").read_text(encoding="utf-8")
        source = source.rsplit('\nmain "$@"', 1)[0].replace('. /lib/functions/c8-system.sh || exit 1', '')
        # Redirect only filesystem boundaries; execute the actual upstream helpers.
        source = source.replace('/lib/functions.sh', '"$TASK_TMP/functions.sh"')
        if broken:
            start = source.index("mirror_list() (")
            end = source.index("\nhuman_mb()", start)
            source = source[:start] + f'''mirror_list() {{
    [ "$(use_mirror)" = 1 ] || return 0
    . "$TASK_TMP/functions.sh"
    config_load {config} 2>/dev/null || return 0
    config_list_foreach main mirror echo
}}
''' + source[end:]
        lib = libraries()["lib/functions.sh"].replace('/lib/config/uci.sh', '"$TASK_TMP/uci.sh"')
        (self.root / "functions.sh").write_text(lib, encoding="utf-8", newline="\n")
        uci = libraries()["lib/config/uci.sh"].replace('/sbin/uci', 'uci_fixture')
        (self.root / "uci.sh").write_text(uci, encoding="utf-8", newline="\n")
        tag = model + "-immortalwrt-master-test"
        name = "immortalwrt-nradio_c8-688-squashfs-sysupgrade.bin" if model == "C8-688" else "ap3000m-sysupgrade.bin"
        api = [{"tag_name": tag, "draft": False, "prerelease": False, "assets": [
            {"name": name, "size": 4, "digest": "sha256:" + hashlib.sha256(b"good").hexdigest()}]}]
        (self.root / "api.json").write_text(json.dumps(api), encoding="utf-8")
        fixture = '''
unset IPKG_INSTROOT CONFIG_LIST_STATE NO_CALLBACK
jq() { "$OTA_TEST_JQ" "$@"; }
use_mirror() { if [ "$TEST_MODE" = disabled ]; then echo 0; else echo 1; fi; }
cfg() { return 1; }
need_tools() { :; }
work_dir() { printf '%s' "$TASK_TMP"; }
free_kb() { echo 999999; }
fetch_api() { cat "$TASK_TMP/api.json"; }
uci_fixture() {
    printf '%s\\n' "$*" >> "$TASK_TMP/uci-calls"
    [ "$TEST_MODE" != missing ] || return 1
    [ "$TEST_MODE" != empty ] || { printf "package test\\nconfig main 'main'\\n"; return; }
    printf "package test\\nconfig main 'main'\\n list mirror 'https://one.invalid/'\\n list mirror 'https://two.invalid/'\\n"
}
curl() {
    output=''
    while [ "$#" -gt 0 ]; do
        if [ "$1" = -o ]; then shift; output="$1"; else url="$1"; fi
        shift
    done
    printf '%s\\n' "$url" >> "$TASK_TMP/download-urls"
    case "$url" in https://one.invalid/*) return 22;; esac
    if [ "$TEST_CORRUPT" = 1 ]; then printf evil > "$output"; else printf good > "$output"; fi
}
DIR_DEFAULT="$TASK_TMP"
DL_TAG_FILE="$TASK_TMP/dl.tag"; DL_RESULT_FILE="$TASK_TMP/dl.result"
OP_FILE="$TASK_TMP/op.json"; OP_LOG="$TASK_TMP/op.log"
'''
        trailer = '''
sentinel=caller
CONFIG_SECTION=caller
cmd_download
[ "$CONFIG_SECTION" = caller ] || exit 91
case "$-" in *u*) ;; *) exit 92;; esac
[ "$sentinel" = caller ] || exit 93
'''
        script = self.root / "test.sh"
        prefix = 'TASK_TMP=$(cygpath -u "$TASK_TMP")\n' if os.name == "nt" else ""
        script.write_text(prefix + source + fixture + trailer, encoding="utf-8", newline="\n")
        environment = dict(os.environ, TASK_TMP=self.root.as_posix(), OTA_TEST_JQ=Path(JQ).as_posix(),
                           TEST_MODE=mode, TEST_CORRUPT="1" if corrupt else "0")
        return subprocess.run((shell or [BASH]) + [script.as_posix()], env=environment,
                              capture_output=True, text=True, encoding="utf-8", timeout=30)

    def test_old_helper_reproduces_real_library_abort_before_any_download(self):
        for model in ("C8-688", "AP3000M"):
            with self.subTest(model=model):
                result = self.run_download(model, broken=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "download-urls").exists())
                self.assertFalse((self.root / "uci-calls").exists())

    def test_both_downloaders_try_configured_mirrors_and_retain_strict_caller(self):
        for model in ("C8-688", "AP3000M"):
            with self.subTest(model=model):
                (self.root / "download-urls").unlink(missing_ok=True)
                result = self.run_download(model)
                self.assertEqual(result.returncode, 0, result.stderr)
                urls = (self.root / "download-urls").read_text().splitlines()
                self.assertEqual(len(urls), 2)
                self.assertTrue(urls[0].startswith("https://one.invalid/https://github.com/"))
                self.assertTrue(urls[1].startswith("https://two.invalid/https://github.com/"))
                self.assertIn("sha256 校验通过", result.stderr)

    def test_disabled_missing_or_empty_mirror_config_falls_back_to_direct(self):
        for model in ("C8-688", "AP3000M"):
            for mode in ("disabled", "missing", "empty"):
                with self.subTest(model=model, mode=mode):
                    for pattern in ("*.bin*", "download-urls", "uci-calls"):
                        for path in self.root.glob(pattern):
                            path.unlink()
                    result = self.run_download(model, mode=mode)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    urls = (self.root / "download-urls").read_text().splitlines()
                    self.assertEqual(len(urls), 1)
                    self.assertTrue(urls[0].startswith("https://github.com/"))
                    if mode == "disabled":
                        self.assertFalse((self.root / "uci-calls").exists())

    def test_mirror_bytes_still_require_the_release_digest(self):
        for model in ("C8-688", "AP3000M"):
            with self.subTest(model=model):
                result = self.run_download(model, corrupt=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("sha256 校验失败", result.stderr)
                self.assertFalse((self.root / (model + "-immortalwrt-master-test.bin")).exists())

    @unittest.skipIf(os.name == "nt", "BusyBox ash executes on Linux")
    def test_real_busybox_ash_download_with_openwrt_config_helpers(self):
        busybox = shutil.which("busybox")
        self.assertIsNotNone(busybox, "busybox required to verify the router shell")
        for model in ("C8-688", "AP3000M"):
            with self.subTest(model=model):
                result = self.run_download(model, shell=[busybox, "ash"])
                self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
