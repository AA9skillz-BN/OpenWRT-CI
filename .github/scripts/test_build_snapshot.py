"""Configuration replay binds API evidence and rejects changed build inputs."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import build_snapshot as snapshot


class BuildSnapshotTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.repo = Path(scratch.name)
        self.reports = self.repo / "build-reports"
        self.reports.mkdir()
        self.tree = self.repo / "wrt"
        self.tree.mkdir()
        env = patch.dict(os.environ, {
            "GITHUB_REPOSITORY": "owner/repo", "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": "a" * 40,
            "WRT_PROFILE": "AP3000M", "WRT_PACKAGE": "", "WRT_PREVIEW_RUN_ID": "",
            "WRT_SOURCE_MODE": "latest", "WRT_PLUGIN_MODE": "auto",
        })
        env.start()
        self.addCleanup(env.stop)
        for name, data in (("source-lock.json", {"AP3000M": {"commit": "b" * 40}}),
                           ("plugin-resolution.json", {"mode": "auto"}),
                           ("openclash-core.json", {"sha256": "a" * 64})):
            (self.reports / name).write_text(json.dumps(data))
        self.config = self.tree / ".config"
        self.config.write_bytes(b"CONFIG_AP=y\n")
        snapshot.capture(self.repo, self.tree)

    def test_load_and_replay_require_same_head_inputs_and_config(self):
        original = (self.reports / snapshot.FILENAME).read_bytes()
        run = {"id": 123, "run_attempt": 1, "head_sha": "a" * 40,
               "path": ".github/workflows/AP3000M.yml", "conclusion": "success"}
        artifact = {"id": 42, "digest": "sha256:" + "1" * 64}

        def download(repo, item, archive):
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr(snapshot.FILENAME, original)
                bundle.writestr("Config-AP3000M-build-123-1.txt", self.config.read_bytes())

        with patch.dict(os.environ, WRT_PREVIEW_RUN_ID="123"), patch.object(snapshot, "device_run", return_value=run), \
                patch.object(snapshot, "artifact_for_run", return_value=(artifact, 1)), \
                patch.object(snapshot, "require_steps") as steps, patch.object(snapshot, "download_zip", side_effect=download):
            snapshot.load_preview(self.repo)
            steps.assert_called_once_with("owner/repo", "123", 1, {"Upload Configuration Preview"})
            snapshot.capture(self.repo, self.tree)
            self.assertEqual(snapshot.replay(self.repo)["config_sha256"], hashlib.sha256(self.config.read_bytes()).hexdigest())
            self.assertEqual(snapshot.read_json(self.reports / "preview-origin.json")["artifact_id"], 42)
            with patch.dict(os.environ, WRT_PACKAGE="CONFIG_PACKAGE_test=y"), self.assertRaisesRegex(ValueError, "build inputs differ"):
                snapshot.load_preview(self.repo)
            self.config.write_bytes(b"CONFIG_OTHER=y\n")
            with self.assertRaisesRegex(ValueError, "configuration SHA-256 differs"):
                snapshot.load_preview(self.repo)
            with self.assertRaisesRegex(ValueError, "config_sha256"):
                snapshot.capture(self.repo, self.tree)
            run["head_sha"] = "d" * 40
            with self.assertRaisesRegex(ValueError, "exact CI commit"):
                snapshot.load_preview(self.repo)
