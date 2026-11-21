"""Exercise release-tag reservation and cleanup through the actual workflow shell."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import yaml

REPO = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else None)
JQ = shutil.which("jq")
SHA = "a" * 40
TAG = "AP3000M-immortalwrt-master-test-123-1"
WORKFLOW = yaml.safe_load((REPO / ".github/workflows/DEVICE-CORE.yml").read_text(encoding="utf-8"))


@unittest.skipUnless(BASH and JQ, "bash and jq required")
class ReleaseTagTests(unittest.TestCase):
    def execute(self, script, **values):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "test.sh"
            path.write_text('''set -eu
jq() { "$TASK_JQ" "$@"; }
gh() {
    printf '%s\\n' "$*" >> calls
    case "$*" in
        *'--method DELETE'*) return 0;;
        *'--method POST'*) printf '%s' "$CREATED";;
        *'--paginate --slurp'*) printf '%s' "$RELEASES";;
        *'matching-refs'*) printf '%s' "$EXISTING";;
        *'git/ref/heads/'*) printf '%s' "$BRANCH_SHA";;
        *) printf '%s' "$DETAIL";;
    esac
}
''' + script, encoding="utf-8", newline="\n")
            detail = {"ref": "refs/tags/" + TAG, "object": {"type": "commit", "sha": SHA}}
            env = dict(os.environ, GITHUB_REPOSITORY="owner/repo", GITHUB_REF="refs/heads/main",
                       GITHUB_SHA=SHA, RELEASE_TAG=TAG, TASK_JQ=Path(JQ).as_posix(),
                       GITHUB_OUTPUT=(root / "output").as_posix(), EXISTING="[]", RELEASES="[[]]",
                       BRANCH_SHA=SHA, CREATED=json.dumps(detail), DETAIL=json.dumps(detail))
            env.update(values)
            result = subprocess.run([BASH, path.as_posix()], cwd=directory, env=env, capture_output=True, text=True)
            return result, (root / "calls").read_text(), (root / "output").read_text() if (root / "output").exists() else ""

    def test_reservation_creates_exact_tag_and_preserves_existing_or_changed_refs(self):
        step = next(s for s in WORKFLOW["jobs"]["prepare-release"]["steps"] if s.get("id") == "reserve")
        self.assertIn("!inputs.WRT_TEST", step["if"])
        self.assertIn("inputs.WRT_PUBLISH", step["if"])
        for case in ("new", "same", "different", "annotated", "advanced", "wrong-created"):
            with self.subTest(case=case):
                values = {}
                if case in ("same", "different", "annotated"):
                    values["EXISTING"] = json.dumps([{"ref": "refs/tags/" + TAG, "object": {
                        "type": "tag" if case == "annotated" else "commit",
                        "sha": "b" * 40 if case == "different" else SHA}}])
                if case == "advanced": values["BRANCH_SHA"] = "b" * 40
                if case == "wrong-created": values["CREATED"] = json.dumps({"ref": "refs/tags/" + TAG, "object": {"type": "commit", "sha": "b" * 40}})
                result, calls, output = self.execute(step["run"], **values)
                self.assertEqual(result.returncode == 0, case in ("new", "same"), result.stderr)
                self.assertEqual("--method POST" in calls, case in ("new", "wrong-created"))
                self.assertNotIn("--method DELETE", calls)
                if case == "new":
                    self.assertIn("created=true", output)
                    self.assertIn("ref=refs/tags/" + TAG + " -f sha=" + SHA, calls)
                if case == "same": self.assertIn("created=false", output)

    def test_unbuilt_tag_cleanup_preserves_other_tags_and_all_releases(self):
        job = WORKFLOW["jobs"]["cleanup-build-tag"]
        self.assertIn("tag_created == 'true'", job["if"])
        self.assertIn("needs.core.result != 'success'", job["if"])
        for case in ("owned", "changed", "draft", "published"):
            with self.subTest(case=case):
                values = {}
                if case == "changed": values["DETAIL"] = json.dumps({"object": {"type": "commit", "sha": "b" * 40}})
                if case in ("draft", "published"):
                    values["RELEASES"] = json.dumps([[{"tag_name": TAG, "draft": case == "draft"}]])
                result, calls, _ = self.execute(job["steps"][0]["run"], **values)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual("--method DELETE" in calls, case == "owned")

    def test_publish_retry_uses_original_build_artifact(self):
        jobs = WORKFLOW["jobs"]
        name = jobs["publish"]["steps"][0]["with"]["name"]
        self.assertEqual(name, "${{needs.core.outputs.firmware_artifact}}")
        self.assertNotIn("github.run_attempt", name)
        self.assertIn("firmware_artifact", jobs["core"]["outputs"])


if __name__ == "__main__":
    unittest.main()
