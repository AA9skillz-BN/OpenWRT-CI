"""Exercise plugin snapshots with real local Git remotes, without changing locks."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import yaml

SPEC = importlib.util.spec_from_file_location("plugin_build", Path(__file__).with_name("device-build.py"))
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)
REPO = Path(__file__).resolve().parents[2]


class PluginModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(build.remove_package, self.root)
        self.remote = self.root / "upstream"
        self.remote.mkdir()
        self.git("init", "-q", "--initial-branch=main")
        self.first = self.commit("version one")
        self.git("branch", "stable")
        self.second = self.commit("version two")
        self.repo, self.tree, self.reports = (self.root / name for name in ("repo", "tree", "reports"))
        self.item = {"url": self.remote.as_uri(), "commit": self.first,
                     "directory": "plugin", "replaces": ["plugin"]}
        self.lock = self.repo / "Profiles/packages.lock.json"
        self.write_lock()
        (self.tree / "feeds").mkdir(parents=True)
        self.data = {"packages": ["example"]}

    def git(self, *args):
        return subprocess.check_output(["git", "-c", "user.name=Plugin Test",
            "-c", "user.email=plugin-test@example.invalid", "-C", str(self.remote), *args],
            text=True, stderr=subprocess.PIPE).strip()

    def commit(self, text):
        for name in ("Makefile", "app/Makefile", "backend/Makefile"):
            build.write(self.remote / name, text + "\n")
        self.git("add", ".")
        self.git("commit", "-qm", text)
        return self.git("rev-parse", "HEAD")

    def write_lock(self):
        build.write(self.lock, json.dumps({"example": self.item}))

    def install(self, plugin_mode=None, source_mode="locked"):
        before = self.lock.read_bytes()
        with patch.dict(os.environ, {"WRT_SOURCE_MODE": source_mode}):
            if plugin_mode is None:
                with patch.dict(os.environ):
                    os.environ.pop("WRT_PLUGIN_MODE", None)
                    build.install_packages(self.repo, self.tree, self.data, self.reports)
            else:
                with patch.dict(os.environ, {"WRT_PLUGIN_MODE": plugin_mode}):
                    build.install_packages(self.repo, self.tree, self.data, self.reports)
        self.assertEqual(before, self.lock.read_bytes())
        return json.loads((self.reports / "packages.lock.json").read_text())

    def test_default_and_all_four_source_plugin_combinations_are_independent(self):
        self.assertEqual(self.install()["example"], self.item)
        for source in ("locked", "latest"):
            for mode, expected in (("locked", self.first), ("latest", self.second)):
                with self.subTest(source=source, plugin=mode):
                    result = self.install(mode, source)
                    self.assertEqual(result["example"]["commit"], expected)
                    actual = subprocess.check_output(["git", "-C", str(self.tree / "package/plugin"),
                                                      "rev-parse", "HEAD"], text=True).strip()
                    self.assertEqual(actual, expected)
                    record = json.loads((self.reports / "plugin-resolution.json").read_text())
                    self.assertEqual(record["mode"], mode)
                    self.assertEqual(record["plugins"]["example"]["locked_commit"], self.first)
                    self.assertEqual(record["plugins"]["example"]["resolved_commit"], expected)
                    self.assertEqual(record["plugins"]["example"]["branch"], "main" if mode == "latest" else None)

    def test_latest_can_follow_an_explicit_branch(self):
        self.item["branch"] = "stable"
        self.write_lock()
        self.assertEqual(self.install("latest")["example"]["commit"], self.first)
        record = json.loads((self.reports / "plugin-resolution.json").read_text())
        self.assertEqual(record["plugins"]["example"]["branch"], "stable")

    def test_latest_fetches_the_resolved_sha_even_when_branch_advances(self):
        original = build.resolve_plugin
        def advance(*args):
            selected = original(*args)
            self.commit("version three")
            return selected
        with patch.object(build, "resolve_plugin", side_effect=advance):
            result = self.install("latest")
        self.assertNotEqual(self.git("rev-parse", "HEAD"), self.second)
        self.assertEqual(result["example"]["commit"], self.second)
        self.assertEqual((self.tree / "package/plugin/Makefile").read_text(), "version two\n")

    def test_latest_updates_next_run_and_invalidates_download_key(self):
        first = self.install("latest")
        key = build.download_key(self.tree, "cache", "source", {}, first, "config")
        third = self.commit("version three")
        second = self.install("latest")
        self.assertEqual(second["example"]["commit"], third)
        self.assertNotEqual(key, build.download_key(self.tree, "cache", "source", {}, second, "config"))

    def test_missing_branch_fails_without_falling_back_or_removing_existing_package(self):
        self.item["branch"] = "missing"
        self.write_lock()
        marker = self.tree / "package/plugin/Makefile"
        build.write(marker, "existing package")
        with self.assertRaises(subprocess.CalledProcessError):
            self.install("latest")
        self.assertEqual(marker.read_text(), "existing package")
        self.assertFalse((self.reports / "packages.lock.json").exists())

    def test_detached_remote_head_requires_an_explicit_branch_in_latest(self):
        self.git("checkout", "--detach", "-q", self.second)
        with self.assertRaisesRegex(ValueError, "default plugin branch"):
            self.install("latest")
        # Locked mode remains reproducible without a symbolic remote HEAD.
        self.assertEqual(self.install("locked")["example"], self.item)

    def test_fetch_failure_never_uses_the_locked_commit_in_latest(self):
        self.item["url"] = (self.root / "missing-remote").as_uri()
        self.write_lock()
        with self.assertRaises(subprocess.CalledProcessError):
            self.install("latest")
        self.assertFalse((self.reports / "packages.lock.json").exists())

    def test_unknown_mode_is_rejected_before_touching_git(self):
        with patch.object(build, "run") as git, self.assertRaisesRegex(ValueError, "plugin mode"):
            self.install("newest")
        git.assert_not_called()

    def test_latest_retains_multiple_package_extraction(self):
        self.item["subdirectories"] = ["app", "backend"]
        self.write_lock()
        result = self.install("latest")
        self.assertEqual(result["example"]["commit"], self.second)
        self.assertFalse((self.tree / "package/plugin").exists())
        for name in ("app", "backend"):
            self.assertEqual((self.tree / "package" / name / "Makefile").read_text(), "version two\n")


class PluginWorkflowTests(unittest.TestCase):
    def test_every_device_exposes_and_passes_both_modes_independently(self):
        for name in ("TR3000", "AP3000M", "C8-688"):
            workflow = yaml.safe_load((REPO / f".github/workflows/{name}.yml").read_text(encoding="utf-8"))
            inputs = workflow[True]["workflow_dispatch"]["inputs"]
            for mode in ("SOURCE_MODE", "PLUGIN_MODE"):
                self.assertEqual(inputs[mode]["options"], ["locked", "latest"])
                self.assertEqual(inputs[mode]["default"], "locked")
            for job in workflow["jobs"].values():
                for mode in ("SOURCE_MODE", "PLUGIN_MODE"):
                    self.assertEqual(job["with"]["WRT_" + mode], "${{ inputs." + mode + " || 'locked' }}")
        core = yaml.safe_load((REPO / ".github/workflows/DEVICE-CORE.yml").read_text(encoding="utf-8"))
        for mode in ("SOURCE_MODE", "PLUGIN_MODE"):
            key = "WRT_" + mode
            self.assertEqual(core["on"]["workflow_call"]["inputs"][key]["default"], "locked")
            self.assertEqual(core["env"][key], "${{inputs." + key + "}}")


if __name__ == "__main__":
    unittest.main()
