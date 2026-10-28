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

    def self_update_fixture(self, metadata=None, script=b'#!/bin/sh\nprintf updated\\n\n'):
        (self.root / "candidate").write_bytes(script)
        info = {"schema": 1, "model": "ap3000m", "runtime_abi": "1", "tag": "AP3000M-update",
                "asset": "ap3000m-ota", "size": len(script), "sha256": hashlib.sha256(script).hexdigest(),
                "requires": ["sh"]}
        info.update(metadata or {})
        raw = json.dumps(info).encode()
        (self.root / "metadata").write_bytes(raw)
        (self.root / "runtime-abi").write_text("1\n")
        (self.root / "installed").write_bytes(b"old script\n")
        release = self.release("AP3000M-update")
        release["assets"] = [
            {"name": "ap3000m-ota-update.json", "size": len(raw), "digest": "sha256:" + hashlib.sha256(raw).hexdigest()},
            {"name": "ap3000m-ota", "size": len(script), "digest": "sha256:" + hashlib.sha256(script).hexdigest()}]
        return release

    def self_update(self, release, body='cmd_self_update\n'):
        return self.shell('''
OTA_SCRIPT="$TASK_TMP/installed"
RUNTIME_ABI_FILE="$TASK_TMP/runtime-abi"
acquire_lock() { :; }
curl() {
    local dest='' url=''
    while [ "$#" -gt 0 ]; do
        if [ "$1" = '-o' ]; then shift; dest="$1"; else url="$1"; fi
        shift
    done
    printf '%s\\n' "$url" >> "$TASK_TMP/update-urls"
    case "$url" in
        */ap3000m-ota-update.json) cp "$TASK_TMP/metadata" "$dest";;
        */ap3000m-ota) cp "$TASK_TMP/candidate" "$dest";;
        *) return 1;;
    esac
}
''' + body, API_JSON=json.dumps([release]))

    def assert_installed_untouched(self):
        self.assertEqual((self.root / "installed").read_bytes(), b"old script\n")
        self.assertFalse((self.root / "installed.bak").exists())
        self.assertEqual(list(self.root.glob(".ap3000m-ota.*")), [])

    def test_self_update_uses_verified_release_assets_and_keeps_backup(self):
        release = self.self_update_fixture()
        result = self.self_update(release)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "installed").read_bytes(), (self.root / "candidate").read_bytes())
        self.assertEqual((self.root / "installed.bak").read_bytes(), b"old script\n")
        urls = (self.root / "update-urls").read_text()
        self.assertIn("/releases/download/AP3000M-update/ap3000m-ota", urls)
        self.assertNotIn("/contents/", urls)
        self.assertEqual(list(self.root.glob(".ap3000m-ota.*")), [])

    def test_self_update_refuses_branches_and_missing_firmware_contract_before_download(self):
        release = self.self_update_fixture()
        for body in ('cmd_self_update main\n', 'rm -f "$RUNTIME_ABI_FILE"; cmd_self_update\n'):
            result = self.self_update(release, body)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.root / "update-urls").exists())
            self.assert_installed_untouched()

    def test_self_update_rejects_new_abi_and_missing_dependencies(self):
        for info in ({"runtime_abi": "2"}, {"requires": ["nonexistent_ota_dependency_9001"]},
                     {"tag": "AP3000M-other"}, {"model": "c8-688"}):
            with self.subTest(info=info):
                release = self.self_update_fixture(info)
                result = self.self_update(release)
                self.assertNotEqual(result.returncode, 0)
                self.assert_installed_untouched()
                self.assertNotIn("/ap3000m-ota\n", (self.root / "update-urls").read_text())

    def test_self_update_rejects_corruption_missing_digest_and_bad_shell(self):
        for fault in ("metadata", "script", "digest", "syntax"):
            with self.subTest(fault=fault):
                release = self.self_update_fixture(script=b'#!/bin/sh\nif then\n' if fault == "syntax" else b'#!/bin/sh\nexit 0\n')
                if fault == "metadata":
                    raw = (self.root / "metadata").read_bytes()
                    (self.root / "metadata").write_bytes(raw.replace(b'"ap3000m"', b'"xp3000m"'))
                elif fault == "script":
                    (self.root / "candidate").write_bytes(b'#!/bin/sh\nexit 1\n')
                elif fault == "digest":
                    release["assets"][1].pop("digest")
                result = self.self_update(release)
                self.assertNotEqual(result.returncode, 0)
                self.assert_installed_untouched()

    def test_self_update_ignores_draft_preview_and_other_device(self):
        for overrides in ({"draft": True}, {"prerelease": True}, {"tag_name": "C8-688-update"}, {"assets": []}):
            release = self.self_update_fixture()
            release.update(overrides)
            result = self.self_update(release)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.root / "update-urls").exists())
            self.assert_installed_untouched()

    def test_self_update_honors_explicit_tag_and_rejects_another_result(self):
        release = self.self_update_fixture()
        result = self.self_update(release, 'cmd_self_update --tag AP3000M-other\n')
        self.assertNotEqual(result.returncode, 0)
        self.assert_installed_untouched()

    @unittest.skipIf(os.name == "nt" or not shutil.which("flock"), "Linux flock required")
    def test_self_update_cannot_overlap_a_download_or_upgrade(self):
        release = self.self_update_fixture()
        actual = SOURCE[SOURCE.index("acquire_lock() {"):SOURCE.index("\noperation_state()")]
        result = self.self_update(release, actual + '''
acquire_lock
(exec 9>&-; cmd_self_update) && exit 90
exit 0
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "update-urls").exists())
        self.assert_installed_untouched()

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
