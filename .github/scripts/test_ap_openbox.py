"""Exercise official installer integrity, locks, packaging and real sysupgrade selection."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import urllib.request

REPO = Path(__file__).resolve().parents[2]
PACKAGE = REPO / "Scripts/AP3000M/luci-app-ap3000m-openbox"
SOURCE = (PACKAGE / "root/usr/sbin/ap3000m-openbox").read_text(encoding="utf-8").rsplit('\nmain "$@"', 1)[0]
BASH = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else None)
JQ = os.environ.get("OTA_TEST_JQ") or shutil.which("jq")
UCODE = os.environ.get("UCODE_TEST_BIN") or shutil.which("ucode")


@unittest.skipUnless(BASH and JQ, "bash and jq required")
class OpenBoxInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="openbox-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.script = b"#!/bin/sh\nprintf official > \"$TASK_TMP/executed\"\n"
        self.revision = "a" * 40
        self.metadata = {"sha": hashlib.sha1(b"blob " + str(len(self.script)).encode() + b"\0" + self.script).hexdigest(),
                         "size": len(self.script)}

    def shell(self, body, script=None, metadata=None, revision=None):
        (self.root / "official.sh").write_bytes(self.script if script is None else script)
        (self.root / "metadata.json").write_text(json.dumps(self.metadata if metadata is None else metadata))
        (self.root / "commit.json").write_text(json.dumps({"sha": self.revision if revision is None else revision}))
        test = self.root / "test.sh"
        test.write_text(SOURCE + '''
jq() { "$OPENBOX_TEST_JQ" "$@"; }
ROOT="$TASK_TMP/root"
STATE="$TASK_TMP/state"
LOCK="$TASK_TMP/locks/install.lock"
OTA_LOCK="$TASK_TMP/locks/ota.lock"
prepare_state
TMP="$STATE/download"
mkdir -p "$TMP"
installed() { [ -f "$TASK_TMP/installed" ]; }
fetch() {
    printf '%s\\n' "$1" >> "$TASK_TMP/urls"
    case "$1" in
      */commits/main) cp "$TASK_TMP/commit.json" "$2" ;;
      */contents/scripts/install.sh*) cp "$TASK_TMP/metadata.json" "$2" ;;
      */scripts/install.sh) cp "$TASK_TMP/official.sh" "$2" ;;
      *) return 1 ;;
    esac
}
''' + body, encoding="utf-8", newline="\n")
        return subprocess.run([BASH, test.as_posix()], text=True, encoding="utf-8", capture_output=True,
                              env=dict(os.environ, TASK_TMP=self.root.as_posix(), OPENBOX_TEST_JQ=str(JQ)), timeout=30)

    def test_exact_snapshot_and_git_blob_checked_before_execution(self):
        result = self.shell('download_installer\nsh "$TMP/install.sh"\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "executed").read_text(), "official")
        urls = (self.root / "urls").read_text().splitlines()
        self.assertEqual(len(urls), 3)
        self.assertTrue(urls[1].endswith("?ref=" + self.revision))
        self.assertIn("/" + self.revision + "/scripts/install.sh", urls[2])

    def test_tampered_same_size_script_never_executes(self):
        changed = self.script.replace(b"official", b"injected")
        self.assertEqual(len(changed), len(self.script))
        result = self.shell('download_installer\nsh "$TMP/install.sh"\n', script=changed)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("内容校验不符", result.stderr)
        self.assertFalse((self.root / "executed").exists())

    def test_bad_metadata_and_revision_cannot_change_the_download_url(self):
        for revision, metadata in [("main; touch injected", self.metadata), (self.revision, {"sha": "x", "size": 5}),
                                   (self.revision, {"sha": self.metadata["sha"], "size": 262145})]:
            with self.subTest(revision=revision, metadata=metadata):
                result = self.shell('download_installer\nsh "$TMP/install.sh"\n', revision=revision, metadata=metadata)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "executed").exists())

    @unittest.skipIf(os.name == "nt", "flock available in Linux CI")
    def test_failed_installer_preserves_data_and_reports_failure(self):
        data = self.root / "root/data/settings.json"
        data.parent.mkdir(parents=True)
        data.write_text('{"user":"kept"}')
        self.script = b"#!/bin/sh\nexit 23\n"
        result = self.shell('''
download_installer() { cp "$TASK_TMP/official.sh" "$TMP/install.sh"; }
acquire_lock
worker
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(data.read_text(), '{"user":"kept"}')
        state = json.loads((self.root / "state/state.json").read_text())
        self.assertEqual(state["stage"], "failed")

    @unittest.skipIf(os.name == "nt", "flock available in Linux CI")
    def test_success_requires_complete_installed_payload_and_keeps_user_data(self):
        data = self.root / "root/data/settings.json"
        data.parent.mkdir(parents=True)
        data.write_text('{"disabled":true}')
        self.script = b'#!/bin/sh\ntouch "$TASK_TMP/installed"\n'
        result = self.shell('''
download_installer() { cp "$TASK_TMP/official.sh" "$TMP/install.sh"; }
acquire_lock
worker
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(data.read_text(), '{"disabled":true}')
        self.assertEqual(json.loads((self.root / "state/state.json").read_text())["stage"], "success")
        (self.root / "installed").unlink()
        self.script = b'#!/bin/sh\nexit 0\n'
        result = self.shell('''
download_installer() { cp "$TASK_TMP/official.sh" "$TMP/install.sh"; }
acquire_lock
worker
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads((self.root / "state/state.json").read_text())["stage"], "failed")

    @unittest.skipIf(os.name == "nt", "flock available in Linux CI")
    def test_duplicate_and_ota_locks_reject_parallel_installs(self):
        for held in ("install", "ota"):
            with self.subTest(lock=held):
                result = self.shell('''
mkdir -p "$TASK_TMP/locks"
exec 7>"$TASK_TMP/locks/''' + held + '''.lock"
flock -n 7 || exit 5
acquire_lock
printf bypassed
''')
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("bypassed", result.stdout)

    @unittest.skipIf(os.name == "nt", "flock available in Linux CI")
    def test_status_detects_interrupted_job_and_hides_private_data(self):
        data = self.root / "root/data/password"
        data.parent.mkdir(parents=True)
        data.write_text("PRIVATE-FIXTURE")
        result = self.shell('''
write_state running pending
printf '1;injected' > "$ROOT/data/panel-port"
status
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(result.stdout)
        self.assertTrue(status["data_present"])
        self.assertFalse(status["installed"])
        self.assertFalse(status["busy"])
        self.assertEqual(status["operation"]["stage"], "failed")
        self.assertEqual(status["port"], 3036)
        self.assertNotIn("PRIVATE-FIXTURE", result.stdout)


@unittest.skipIf(os.name == "nt", "actual package Make installation and tar restore run on Linux")
class OpenBoxPackagingTests(unittest.TestCase):
    def test_package_installs_exact_entry_and_data_rule_without_proprietary_programs(self):
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory)
            package = top / "package/installer"
            shutil.copytree(PACKAGE, package)
            (top / "include").mkdir()
            (top / "include/package.mk").touch()
            (top / "rules.mk").write_text('''
INCLUDE_DIR:=$(TOPDIR)/include
BUILD_DIR:=$(TOPDIR)/build
INSTALL_DIR:=install -d
INSTALL_BIN:=install -m0755
INSTALL_DATA:=install -m0644
''')
            (package / "probe.mk").write_text('''include Makefile
define Probe
.PHONY: install
install:
\t$(call Package/luci-app-ap3000m-openbox/install,$(TOPDIR)/rootfs)
endef
$(eval $(Probe))
''')
            result = subprocess.run(["make", "--no-print-directory", "-f", "probe.mk", f"TOPDIR={top}", "install"],
                                    cwd=package, text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            profile = json.loads((REPO / "Profiles/AP3000M.json").read_bytes())
            entries = [item for item in profile["required_files"] if item.get("source", "").startswith("Scripts/AP3000M/luci-app-ap3000m-openbox/")]
            self.assertEqual(len(entries), 6)
            for item in entries:
                installed = top / "rootfs" / item["path"].lstrip("/")
                self.assertEqual(installed.read_bytes(), (REPO / item["source"]).read_bytes())
                if item.get("executable"):
                    self.assertTrue(installed.stat().st_mode & 0o111)
            for forbidden in ("/opt/open-box/bin/openbox-panel", "/opt/open-box/meta.json"):
                self.assertIn(forbidden, profile["forbidden_files"])
                self.assertFalse((top / "rootfs" / forbidden.lstrip("/")).exists())
            config = (REPO / "Config/BASE.txt").read_text() + (REPO / "Config/AP3000M.txt").read_text()
            for package_name in ("luci-app-ap3000m-openbox", "ca-bundle", "kmod-tun", "kmod-nft-queue", "kmod-nft-nat", "kmod-veth", "ip-full"):
                self.assertIn("CONFIG_PACKAGE_" + package_name + "=y", config)
                self.assertIn(package_name, profile["required_packages"])

    def test_real_upstream_sysupgrade_selects_and_restores_data_without_old_programs(self):
        lock = json.loads((REPO / "Profiles/sources.lock.json").read_bytes())["AP3000M"]
        url = lock["url"].removesuffix(".git").replace("https://github.com/", "https://raw.githubusercontent.com/")
        with urllib.request.urlopen(url + "/" + lock["commit"] + "/package/base-files/files/sbin/sysupgrade", timeout=60) as response:
            source = response.read().decode()
        start = source.index("list_static_conffiles() {")
        function = source[start:source.index("\n}", start) + 2]
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory)
            keep = top / "keep.d"
            keep.mkdir()
            (top / "sysupgrade.conf").touch()
            rule = (PACKAGE / "root/lib/upgrade/keep.d/ap3000m-openbox").read_text()
            self.assertEqual([line for line in rule.splitlines() if line and not line.startswith("#")], ["/opt/open-box/data/"])
            (keep / "openbox").write_text(rule.replace("/opt/open-box/", str(top / "opt/open-box") + "/"))
            data = top / "opt/open-box/data"
            data.mkdir(parents=True)
            (data / "settings.json").write_text('{"subscription":"fixture"}')
            (data / "disabled").touch()
            (data / "current").symlink_to("settings.json")
            binary = top / "opt/open-box/bin/openbox-panel"
            binary.parent.mkdir()
            binary.write_text("old incompatible program")
            function = function.replace("/etc/sysupgrade.conf", str(top / "sysupgrade.conf")).replace("/lib/upgrade/keep.d/", str(keep) + "/")
            result = subprocess.run(["sh", "-c", function + '\nlist_static_conffiles ""'], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            paths = result.stdout.splitlines()
            self.assertEqual(set(paths), {str(data / name) for name in ("settings.json", "disabled", "current")})
            with tarfile.open(top / "backup.tar.gz", "w:gz") as bundle:
                for path in paths:
                    bundle.add(path, arcname=str(Path(path).relative_to(top)))
            restored = top / "restored"
            with tarfile.open(top / "backup.tar.gz") as bundle:
                bundle.extractall(restored, filter="data")
            self.assertEqual((restored / "opt/open-box/data/settings.json").read_bytes(), (data / "settings.json").read_bytes())
            self.assertTrue((restored / "opt/open-box/data/current").is_symlink())
            self.assertFalse((restored / "opt/open-box/bin/openbox-panel").exists())


@unittest.skipUnless(UCODE, "real ucode runtime required in Linux CI")
class OpenBoxRpcTests(unittest.TestCase):
    def test_rpc_uses_fixed_commands_and_accepts_only_a_boolean_mirror_flag(self):
        source = (PACKAGE / "root/usr/share/rpcd/ucode/ap3000m-openbox.uc").read_text()
        source = re.sub(r"^import .*?;$", "", source, flags=re.M)
        script = '''let calls = [], code = 0, output = '{"installed":false}';
function popen(command, mode) {
 push(calls, command);
 return {read:function(){return output;},close:function(){return code;}};
}
function load() {
''' + source + '''
}
let api=load()['luci.ap3000m_openbox'];
assert(api.status.call().installed == false);
output='started';
assert(api.install.call({args:{mirror:true}}).started == true);
assert(calls[-1] == '/usr/sbin/ap3000m-openbox install --background --mirror 2>&1');
assert(api.install.call({args:{mirror:'; touch injected'}}).started == true);
assert(calls[-1] == '/usr/sbin/ap3000m-openbox install --background 2>&1');
code=1;
assert(api.install.call({args:{mirror:false}}).error != null);
code=0; output='not started';
assert(api.install.call({args:{mirror:false}}).started != true);
output='invalid json';
assert(api.status.call().error != null);
print('Open-Box RPC behavior verified\\n');
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rpc.uc"
            path.write_text(script, encoding="utf-8")
            result = subprocess.run([UCODE, str(path)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        acl = json.loads((PACKAGE / "root/usr/share/rpcd/acl.d/luci-app-ap3000m-openbox.json").read_bytes())["luci-app-ap3000m-openbox"]
        self.assertEqual(acl["read"]["ubus"]["luci.ap3000m_openbox"], ["status"])
        self.assertEqual(acl["write"]["ubus"]["luci.ap3000m_openbox"], ["install"])


if __name__ == "__main__":
    unittest.main()
