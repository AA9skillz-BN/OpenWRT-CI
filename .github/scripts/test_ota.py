"""Exercise real OTA shell functions with isolated files and mocked networking."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
SOURCE = (REPO / "Scripts/AP3000M/ota/ap3000m-ota").read_text(encoding="utf-8").rsplit('\nmain "$@"', 1)[0]
BASH = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else None)
JQ = os.environ.get("OTA_TEST_JQ") or shutil.which("jq")


@unittest.skipUnless(BASH and JQ, "bash and jq are required")
class OtaTests(unittest.TestCase):
    def test_legacy_broad_prefix_is_migrated_to_the_official_source(self):
        script = self.root / "prefix.sh"
        script.write_text(SOURCE + '\ncfg() { printf "AP3000M-"; }\ntag_prefix\n', encoding="utf-8", newline="\n")
        result = subprocess.run([BASH, script.as_posix()],
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "AP3000M-immortalwrt-master-")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def shell(self, body, **environment):
        script = self.root / "test.sh"
        script.write_text(SOURCE + '''
jq() { "$OTA_TEST_JQ" "$@"; }
tag_prefix() { printf 'AP3000M-'; }
work_dir() { printf '%s' "$TASK_TMP"; }
need_tools() { :; }
ensure_dir() { :; }
DL_TAG_FILE="$TASK_TMP/dl.tag"
DL_RESULT_FILE="$TASK_TMP/dl.result"
PENDING_FILE="$TASK_TMP/pending"
fetch_api() { printf '%s' "$API_JSON"; }
free_kb() { printf '999999'; }
mirror_list() { :; }
curl() {
    printf 'downloaded' > "$TASK_TMP/network-used"
    while [ "$#" -gt 0 ]; do
        if [ "$1" = '-o' ]; then shift; printf 'good' > "$1"; return 0; fi
        shift
    done
    return 1
}
''' + body, encoding="utf-8", newline="\n")
        env = dict(os.environ, TASK_TMP=self.root.as_posix(), OTA_TEST_JQ=Path(JQ).as_posix(), **environment)
        return subprocess.run([BASH, script.as_posix()], env=env, capture_output=True, text=True, encoding="utf-8")

    def release(self, tag="AP3000M-test", **overrides):
        release = {"id": 1, "tag_name": tag, "draft": False, "prerelease": False,
                   "published_at": "2026-10-27T00:00:00Z", "assets": [
                       {"name": "ap3000m-sysupgrade.bin", "size": 4,
                        "digest": "sha256:" + hashlib.sha256(b"good").hexdigest()}]}
        release.update(overrides)
        return release

    def test_latest_uses_publication_time_not_source_name_in_tag(self):
        old = self.release("AP3000M-z-old", published_at="2026-10-26T00:00:00Z")
        new = self.release("AP3000M-a-new")
        result = self.shell('fetch_api | parse_tag ""\n', API_JSON=json.dumps([old, new]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("AP3000M-a-new\t"))

    def test_drafts_prereleases_and_config_only_releases_are_ignored(self):
        releases = [self.release(), self.release("AP3000M-draft", draft=True),
                    self.release("AP3000M-preview", prerelease=True), self.release("AP3000M-config", assets=[])]
        result = self.shell('fetch_api | parse_tag ""\n', API_JSON=json.dumps(releases))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("AP3000M-test\t"))

    def test_missing_digest_stops_before_network(self):
        release = self.release()
        release["assets"][0].pop("digest")
        result = self.shell('cmd_download\n', API_JSON=json.dumps([release]))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "network-used").exists())

    def test_same_size_corrupt_cached_image_is_replaced(self):
        image = self.root / "AP3000M-test.bin"
        image.write_bytes(b"evil")
        result = self.shell('cmd_download\n', API_JSON=json.dumps([self.release()]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "network-used").exists())
        self.assertEqual(image.read_bytes(), b"good")

    def test_verified_cached_image_does_not_download(self):
        image = self.root / "AP3000M-test.bin"
        image.write_bytes(b"good")
        result = self.shell('cmd_download\n', API_JSON=json.dumps([self.release()]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "network-used").exists())

    def test_failed_device_check_never_sets_pending_or_flashes(self):
        (self.root / "AP3000M-test.bin").write_bytes(b"good")
        result = self.shell('''
keep_default() { printf '1'; }
cmd_download() { printf '%s/AP3000M-test.bin' "$TASK_TMP"; }
sysupgrade() {
    [ "$1" = '-T' ] && return 1
    touch "$TASK_TMP/flashed"
}
cmd_upgrade
''', API_JSON="[]")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "pending").exists())
        self.assertFalse((self.root / "flashed").exists())


if __name__ == "__main__":
    unittest.main()
