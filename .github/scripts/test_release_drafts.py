"""Draft cleanup must prove ownership, terminal failure and a full grace day."""
import copy
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import yaml

SPEC = importlib.util.spec_from_file_location("cleanup", Path(__file__).with_name("release-cleanup.py"))
cleanup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cleanup)
REPO = "owner/repo"
SHA = "a" * 40
NOW = datetime(2026, 10, 28, 12, tzinfo=timezone.utc)
BASH = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else None)
JQ = os.environ.get("OTA_TEST_JQ") or shutil.which("jq")


class DraftTests(unittest.TestCase):
    def setUp(self):
        self.release = {"id": 10, "draft": True, "tag_name": "AP3000M-build-123-1",
                        "created_at": "2020-01-01T00:00:00Z", "updated_at": "2026-10-27T11:00:00Z",
                        "body": f"<!-- firmware-model: ap3000m -->\n<!-- firmware-ci: repo={REPO} run=123 attempt=1 sha={SHA} tag=AP3000M-build-123-1 started=2026-10-27T10:00:00Z -->"}
        self.run = {"id": 123, "run_attempt": 1, "head_sha": SHA, "status": "completed",
                    "conclusion": "failure", "updated_at": "2026-10-27T11:00:00Z",
                    "head_repository": {"full_name": REPO}, "path": ".github/workflows/AP3000M.yml"}

    def test_owned_failed_draft_older_than_day_is_eligible(self):
        self.assertTrue(cleanup.stale_draft(self.release, REPO, self.run, NOW))

    def test_commit_created_at_does_not_make_a_fresh_draft_stale(self):
        for field in ("marker", "release", "run"):
            release, run = copy.deepcopy(self.release), copy.deepcopy(self.run)
            if field == "marker":
                release["body"] = release["body"].replace("started=2026-10-27T10:00:00Z", "started=2026-10-28T11:00:00Z")
            elif field == "release":
                release["updated_at"] = "2026-10-28T11:00:00Z"
            else:
                run["updated_at"] = "2026-10-28T11:00:00Z"
            self.assertFalse(cleanup.stale_draft(release, REPO, run, NOW))

    def test_running_successful_wrong_attempt_source_or_workflow_is_preserved(self):
        for change in ({"status": "in_progress"}, {"conclusion": "success"}, {"run_attempt": 2},
                       {"id": 124}, {"head_sha": "b" * 40}, {"path": ".github/workflows/WRT-CORE.yml"},
                       {"head_repository": {"full_name": "other/repo"}}, {"updated_at": "invalid"}):
            with self.subTest(change=change):
                self.assertFalse(cleanup.stale_draft(self.release, REPO, dict(self.run, **change), NOW))

    def test_manual_published_foreign_or_ambiguous_drafts_are_preserved(self):
        for change in ({"body": "manual draft"}, {"draft": False}, {"tag_name": "AP3000M-build-124-1"},
                       {"body": self.release["body"] * 2}, {"updated_at": "invalid"}):
            with self.subTest(change=change):
                self.assertFalse(cleanup.stale_draft(dict(self.release, **change), REPO, self.run, NOW))
        self.assertFalse(cleanup.stale_draft(self.release, "foreign/repo", self.run, NOW))

    def test_read_only_mode_and_run_api_failure_never_delete(self):
        with patch.object(cleanup, "api", return_value=self.run), patch.object(cleanup.subprocess, "run") as delete:
            cleanup.clean_drafts([self.release], REPO)
            delete.assert_not_called()
        with patch.object(cleanup, "api", side_effect=subprocess.CalledProcessError(1, "gh")), \
                patch.object(cleanup.subprocess, "run") as delete:
            cleanup.clean_drafts([self.release], REPO, True)
            delete.assert_not_called()

    def test_rereads_and_preserves_a_draft_that_was_published(self):
        with patch.object(cleanup, "stale_draft", side_effect=[True, False]), \
                patch.object(cleanup, "api", side_effect=[self.run, dict(self.release, draft=False), self.run]), \
                patch.object(cleanup.subprocess, "run") as delete:
            cleanup.clean_drafts([self.release], REPO, True)
            delete.assert_not_called()

    def test_apply_deletes_only_the_verified_release_id_without_its_tag(self):
        with patch.object(cleanup, "stale_draft", return_value=True), \
                patch.object(cleanup, "api", side_effect=[self.run, self.release, self.run]), \
                patch.object(cleanup.subprocess, "run") as delete:
            cleanup.clean_drafts([self.release], REPO, True)
            delete.assert_called_once_with(["gh", "api", "--method", "DELETE", "repos/owner/repo/releases/10"], check=True)


    @unittest.skipUnless(BASH and JQ, "bash and jq required")
    def test_publication_failure_cleanup_executes_only_for_its_own_unpublished_draft(self):
        repo = Path(__file__).resolve().parents[2]
        workflow = yaml.safe_load((repo / ".github/workflows/DEVICE-CORE.yml").read_text(encoding="utf-8"))
        steps = workflow["jobs"]["publish"]["steps"]
        script = next(step for step in steps if step["name"] == "Remove this run's failed draft")
        self.assertIn("failure()", script["if"])
        self.assertIn("cancelled()", script["if"])
        for case in ("owned", "manual", "other-attempt", "published", "published-during-check", "ambiguous"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                draft = copy.deepcopy(self.release)
                if case == "manual":
                    draft["body"] = "manual"
                elif case == "other-attempt":
                    draft["body"] = draft["body"].replace("attempt=1", "attempt=2")
                elif case == "published":
                    draft["draft"] = False
                pages = [[draft, draft] if case == "ambiguous" else [draft]]
                current = dict(draft, draft=False) if case == "published-during-check" else draft
                path = Path(directory) / "test.sh"
                path.write_text('''
set -eu
jq() { "$OTA_TEST_JQ" "$@"; }
gh() {
    case "$*" in
        *'--method DELETE'*) printf '%s' "$*" > deleted;;
        *'--paginate --slurp'*) printf '%s' "$PAGES";;
        *) printf '%s' "$DETAIL";;
    esac
}
''' + script["run"], encoding="utf-8", newline="\n")
                env = dict(os.environ, GITHUB_REPOSITORY=REPO, GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="1",
                           GITHUB_SHA=SHA, RELEASE_TAG=self.release["tag_name"], OTA_TEST_JQ=Path(JQ).as_posix(),
                           PAGES=json.dumps(pages), DETAIL=json.dumps(current))
                result = subprocess.run([BASH, path.as_posix()], cwd=directory, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                deleted = Path(directory) / "deleted"
                self.assertEqual(deleted.exists(), case == "owned")
                if deleted.exists():
                    self.assertEqual(deleted.read_text(), "api --method DELETE repos/owner/repo/releases/10")


if __name__ == "__main__":
    unittest.main()
