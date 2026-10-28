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
CACHE_FILE="$TASK_TMP/check.json"
OP_FILE="$TASK_TMP/operation.json"
OP_LOG="$TASK_TMP/operation.log"
LOCK="$TASK_TMP/ota.lock"
current_tag() { printf 'AP3000M-current'; }
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

    def seed_local_image(self):
        result = self.shell('cmd_download\n', API_JSON=json.dumps([self.release()]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / "AP3000M-test.bin.json").is_file())
        (self.root / "network-used").unlink()

    def test_offline_rollback_rechecks_saved_metadata_before_flashing(self):
        self.seed_local_image()
        result = self.shell('''
fetch_api() { touch "$TASK_TMP/api-used"; return 1; }
sysupgrade() {
    case "$1" in -T|-b) return 0;; *) printf '%s' "$*" > "$TASK_TMP/flashed";; esac
}
cmd_upgrade --keep --yes --tag AP3000M-test
''', API_JSON="[]")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "api-used").exists())
        self.assertTrue((self.root / "flashed").exists())

    def test_corrupt_offline_image_cannot_flash_or_create_pending(self):
        self.seed_local_image()
        (self.root / "AP3000M-test.bin").write_bytes(b"evil")
        result = self.shell('''
fetch_api() { touch "$TASK_TMP/api-used"; return 1; }
sysupgrade() { touch "$TASK_TMP/flashed"; }
cmd_upgrade --keep --yes --tag AP3000M-test
''', API_JSON="[]")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "api-used").exists())
        self.assertFalse((self.root / "pending").exists())
        self.assertFalse((self.root / "flashed").exists())

    def test_local_metadata_from_another_repository_is_not_trusted(self):
        self.seed_local_image()
        metadata = self.root / "AP3000M-test.bin.json"
        data = json.loads(metadata.read_text())
        data["repo"] = "other/repository"
        metadata.write_text(json.dumps(data))
        result = self.shell('verified_local AP3000M-test\n', API_JSON="[]")
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_legacy_local_image_requires_remote_metadata_before_use(self):
        (self.root / "AP3000M-test.bin").write_bytes(b"good")
        result = self.shell('''
fetch_api() { touch "$TASK_TMP/api-used"; return 1; }
sysupgrade() { touch "$TASK_TMP/flashed"; }
cmd_upgrade --keep --yes --tag AP3000M-test
''', API_JSON="[]")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.root / "api-used").exists())
        self.assertFalse((self.root / "flashed").exists())

    def test_auto_check_switches_prevent_network_but_allow_manual_checks(self):
        for off in ("enabled", "auto_check"):
            (self.root / "api-used").unlink(missing_ok=True)
            result = self.shell('''
cfg() { [ "$1" != ''' + off + ''' ]; }
cfg_or() { if [ "$1" = ''' + off + ''' ]; then echo 0; else echo 1; fi; }
fetch_api() { touch "$TASK_TMP/api-used"; printf '%s' "$API_JSON"; }
cmd_check --cache
[ ! -f "$TASK_TMP/api-used" ] || exit 90
cmd_check --json
''', API_JSON=json.dumps([self.release()]))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["latest"], "AP3000M-test")

    def test_requested_release_uses_tag_endpoint_and_latest_reads_all_pages(self):
        # Exercise fetch_api itself, overriding only curl to return API fixtures.
        actual = SOURCE[SOURCE.index("fetch_api() ("):SOURCE.index("\nvalidate_metadata()")]
        self.root.joinpath("page1.json").write_text(json.dumps([self.release("AP3000M-old")] * 100))
        self.root.joinpath("page2.json").write_text(json.dumps([self.release("AP3000M-new")]))
        result = self.shell(actual + '''
curl() {
    for url do :; done
    printf '%s\n' "$url" >> "$TASK_TMP/api-urls"
    case "$url" in
        */tags/*) printf '%s' "$API_JSON";;
        *page=1) cat "$TASK_TMP/page1.json";;
        *page=2) cat "$TASK_TMP/page2.json";;
        *) return 1;;
    esac
}
fetch_api AP3000M-test | parse_tag AP3000M-test
fetch_api | jq length
''', API_JSON=json.dumps(self.release()))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[-1], "101")
        self.assertIn("/releases/tags/AP3000M-test", (self.root / "api-urls").read_text())

    @unittest.skipIf(os.name == "nt" or not shutil.which("flock"), "Linux flock required")
    def test_background_upgrade_returns_promptly_and_exposes_validation_failure(self):
        image = self.root / "AP3000M-test.bin"
        image.write_bytes(b"good")
        result = self.shell('''
sysupgrade() { return 1; }
background_operation upgrade --image "$TASK_TMP/AP3000M-test.bin" --keep --yes
wait
cat "$OP_FILE"
''', API_JSON="[]")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines()[0], "started")
        state = json.loads(result.stdout[result.stdout.index("{"):])
        self.assertEqual(state["state"], "failed")
        self.assertIn("未提交升级", state["detail"])
        self.assertFalse((self.root / "pending").exists())

    @unittest.skipIf(os.name == "nt" or not shutil.which("flock"), "Linux flock required")
    def test_background_download_and_upgrade_cannot_overlap(self):
        result = self.shell('''
cmd_download() { while [ ! -f "$TASK_TMP/finish" ]; do sleep .1; done; }
background_operation download
( background_operation upgrade --keep --yes ) && exit 90
touch "$TASK_TMP/finish"
wait
''', API_JSON="[]")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "started")
        self.assertIn("已有下载或升级任务在运行", result.stderr)


if __name__ == "__main__":
    unittest.main()
