"""Exercise release provenance and attachment checks without external writes."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import actions_evidence as evidence
import yaml

REPO = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release", Path(__file__).with_name("release-evidence.py"))
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)
SHA = "a" * 40
REPOSITORY = "owner/repo"
PROFILE = "TR3000-512"
TAG = "TR3000-512-vikingyfy-owrt-26.11.26-12.30.45-123-1"


class ReleaseRecoveryTests(unittest.TestCase):
    def test_logic_tag_and_attachments_gate_both_publication_paths(self):
        for name in ("DEVICE-CORE.yml", "RELEASE-EXISTING.yml"):
            workflow = yaml.safe_load((REPO / ".github/workflows" / name).read_text(encoding="utf-8"))
            job = workflow["jobs"]["publish"]
            phases = [step.get("run", "").split("release-evidence.py ", 1)[1].strip()
                      for step in job["steps"] if "release-evidence.py " in step.get("run", "")]
            tag = "tag" if name == "DEVICE-CORE.yml" else "tag --create-tag"
            self.assertLess(phases.index("logic"), phases.index(tag))
            self.assertLess(phases.index(tag), phases.index("draft"))
            self.assertLess(phases.index("draft"), phases.index("uploaded"))
            self.assertLess(phases.index("uploaded"), phases.index("publish"))
            self.assertEqual(job["permissions"], {"contents": "write", "actions": "read"})

    def test_requires_completed_owned_device_run_and_selected_attempt(self):
        good = {"id": 123, "status": "completed", "head_branch": "main", "event": "workflow_dispatch",
                "head_repository": {"full_name": REPOSITORY}, "path": evidence.DEVICE_WORKFLOWS[PROFILE],
                "head_sha": SHA, "run_attempt": 2}
        with patch.object(evidence, "api", return_value=good):
            self.assertEqual(evidence.device_run(REPOSITORY, 123, "main"), good)
        for changes in ({"head_branch": "feature"}, {"status": "in_progress"}, {"event": "pull_request"},
                        {"path": "other.yml"}, {"head_repository": {"full_name": "foreign/fork"}}):
            with patch.object(evidence, "api", return_value=dict(good, **changes)), self.assertRaises(ValueError):
                evidence.device_run(REPOSITORY, 123, "main")
        artifact = {"id": 42, "name": f"firmware-{PROFILE}-123-1", "expired": False}
        second = dict(artifact, id=43, name=f"firmware-{PROFILE}-123-2")
        with patch.object(evidence, "api", return_value=[artifact, second]):
            with self.assertRaisesRegex(ValueError, "Specify its ID"):
                evidence.artifact_for_run(REPOSITORY, good, PROFILE, "firmware")
            self.assertEqual(evidence.artifact_for_run(REPOSITORY, good, PROFILE, "firmware", "42"), (artifact, 1))
        steps = {"Compile Firmware", "Verify and Package Device Firmware", "Upload Validated Firmware"}
        job = {"conclusion": "success", "steps": [{"name": name, "conclusion": "success"} for name in steps]}
        with patch.object(evidence, "api", return_value=[job]) as api:
            evidence.require_steps(REPOSITORY, 123, 1, steps)
            api.assert_called_once_with(REPOSITORY, "actions/runs/123/attempts/1/jobs?per_page=100", "jobs")
        with patch.object(evidence, "api", return_value=[dict(job, steps=job["steps"][:-1])]), self.assertRaises(ValueError):
            evidence.require_steps(REPOSITORY, 123, 1, steps)

    def test_require_successful_checks_for_same_commit_and_branch(self):
        run = {"id": 1, "head_branch": "main", "head_sha": SHA, "event": "push",
               "head_repository": {"full_name": REPOSITORY}, "status": "completed",
               "conclusion": "success", "html_url": "https://example.test/run"}
        with patch.object(release, "api", return_value=[run]):
            release.verify_logic(REPOSITORY, SHA, "main")
        for rows in ([], [dict(run, head_sha="b" * 40)], [dict(run, event="pull_request")],
                     [run, dict(run, id=2, status="in_progress")], [dict(run, conclusion="failure")]):
            with patch.object(release, "api", return_value=rows), self.assertRaises(ValueError):
                release.verify_logic(REPOSITORY, SHA, "main")

    def test_zip_digest_and_allowed_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, correct, accepted in (("wrt/upload/image.bin", True, True),
                                            ("wrt/upload/image.bin", False, False), ("../escape", True, False)):
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w") as archive:
                    archive.writestr(name, b"accepted bytes")
                data = buffer.getvalue()
                digest = hashlib.sha256(data).hexdigest() if correct else "0" * 64
                with patch.object(evidence.subprocess, "run", side_effect=lambda *_, **kw: kw["stdout"].write(data)):
                    def verify():
                        evidence.download_zip(REPOSITORY, {"id": 42, "digest": "sha256:" + digest}, root / "archive.zip")
                        evidence.unzip_firmware(root / "archive.zip", root)
                    if accepted:
                        verify()
                    else:
                        with self.assertRaises(ValueError):
                            verify()

    def test_verify_checksums_and_remote_draft_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upload = root / "upload"
            upload.mkdir()
            notes = root / "notes"
            notes.write_text("<!-- firmware-model: tr3000-512 -->")
            files = {"ci-commit.txt": SHA.encode(), f"Config-{TAG}.txt": b"CONFIG=y", "image.bin": b"image"}
            for name, data in files.items():
                (upload / name).write_bytes(data)
            (upload / "sha256sums").write_text("".join(
                hashlib.sha256(data).hexdigest() + "  " + name + "\n" for name, data in files.items()))
            with patch.object(release, "UPLOAD", upload), patch.object(release, "NOTES", notes):
                self.assertEqual(release.verify_local(SHA, PROFILE, 123, 1), TAG)
                with self.assertRaisesRegex(ValueError, "attempt"):
                    release.verify_local(SHA, PROFILE, 123, 2)
                assets = [{"name": path.name, "size": path.stat().st_size, "digest": "sha256:" + evidence.digest(path)}
                          for path in upload.iterdir()]
                draft = {"draft": True, "tag_name": TAG, "assets": assets}
                release.verify_uploaded(draft, TAG)
                for wrong in (dict(draft, draft=False), dict(draft, assets=assets[:-1]),
                              dict(draft, assets=[dict(assets[0], size=1), *assets[1:]]),
                              dict(draft, assets=[dict(assets[0], digest="sha256:" + "0" * 64), *assets[1:]])):
                    with self.assertRaises(ValueError):
                        release.verify_uploaded(wrong, TAG)
                (upload / "extra.bin").write_bytes(b"unlisted")
                with self.assertRaisesRegex(ValueError, "every firmware attachment"):
                    release.verify_local(SHA, PROFILE, 123, 1)

    def test_tag_creation_is_opt_in_and_existing_tag_is_never_moved(self):
        ref = {"ref": "refs/tags/" + TAG, "object": {"type": "commit", "sha": SHA}}
        with patch.object(release, "api", return_value=[ref]), patch.object(release.subprocess, "run") as run:
            release.verify_tag(REPOSITORY, TAG, SHA, True)
            self.assertEqual(run.call_count, 1)  # git check-ref-format only
            with self.assertRaises(ValueError):
                release.verify_tag(REPOSITORY, TAG, "b" * 40, True)
            self.assertEqual(run.call_count, 2)
        with patch.object(release, "api", return_value=[]), patch.object(release.subprocess, "run") as run:
            with self.assertRaises(ValueError):
                release.verify_tag(REPOSITORY, TAG, SHA)
            run.return_value.returncode = 0
            import json
            run.return_value.stdout = json.dumps(ref)
            release.verify_tag(REPOSITORY, TAG, SHA, True)
            self.assertIn("POST", run.call_args.args[0])
            self.assertIn(f"sha={SHA}", run.call_args.args[0])
