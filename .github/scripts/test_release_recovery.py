"""Exercise publication provenance and attachment checks without external writes."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile
import yaml

REPO = Path(__file__).resolve().parents[2]


def workflow(name):
    return yaml.safe_load((REPO / '.github/workflows' / name).read_text(encoding='utf8'))


def blocks(step):
    return re.findall(r"python3 - <<'PY'\n(.*?)\nPY", step['run'], re.S)


class ReleaseRecoveryTests(unittest.TestCase):
    def execute(self, source, root, **env):
        return subprocess.run([sys.executable, '-c', source], cwd=root, env=dict(os.environ, **env),
                              capture_output=True, text=True)

    def test_existing_tag_controls_release_and_no_commit_target_is_resubmitted(self):
        for name in ('DEVICE-CORE.yml', 'RELEASE-EXISTING.yml'):
            job = workflow(name)['jobs']['publish']
            steps = job['steps']
            create = next(s for s in steps if s.get('id') == 'release')
            self.assertNotIn('target_commitish', create['with'])
            self.assertTrue(create['with']['draft'])
            guard = next(s for s in steps if s['name'] == 'Verify reserved tag points to the built commit')
            self.assertIn('.object.type == "commit" and .object.sha == $sha', guard['run'])
            self.assertLess(steps.index(guard), steps.index(create))
            verify = next(s for s in steps if s['name'] == 'Verify uploaded release asset digests')
            publish = next(s for s in steps if s['name'] == 'Publish complete release')
            self.assertLess(steps.index(verify), steps.index(publish))
            self.assertEqual(job['permissions']['contents'], 'write')

    def test_recovery_requires_completed_main_owned_device_run(self):
        step = workflow('RELEASE-EXISTING.yml')['jobs']['publish']['steps'][0]
        source = blocks(step)[0]
        good = {'status': 'completed', 'head_branch': 'main', 'event': 'workflow_dispatch',
                'head_repository': {'full_name': 'owner/repo'}, 'path': '.github/workflows/TR3000.yml',
                'head_sha': 'a' * 40}
        cases = [good, dict(good, head_branch='feature'), dict(good, status='in_progress'),
                 dict(good, event='pull_request'), dict(good, path='.github/workflows/BUILD-CHECKS.yml'),
                 dict(good, head_repository={'full_name': 'foreign/fork'})]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for n, data in enumerate(cases):
                (root / 'source-run.json').write_text(json.dumps(data))
                result = self.execute(source, root, GITHUB_ENV=str(root / 'env'), GITHUB_REPOSITORY='owner/repo')
                self.assertEqual(result.returncode == 0, n == 0, result.stderr)

    def test_archive_digest_and_path_checks_reject_corruption_and_traversal(self):
        step = next(s for s in workflow('RELEASE-EXISTING.yml')['jobs']['publish']['steps']
                    if s['name'] == 'Download exact archived artifact and check API digest')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'artifact.zip'
            for name, digest_ok, expected in [('wrt/upload/image.bin', True, True),
                                              ('wrt/upload/image.bin', False, False), ('../escape', True, False)]:
                with zipfile.ZipFile(archive, 'w') as z:
                    z.writestr(name, 'accepted bytes')
                digest = hashlib.sha256(archive.read_bytes()).hexdigest() if digest_ok else '0' * 64
                result = self.execute(blocks(step)[0], root, ARTIFACT_DIGEST='sha256:' + digest)
                self.assertEqual(result.returncode == 0, expected, result.stderr)

    def test_artifact_selection_does_not_rely_on_same_step_github_env_updates(self):
        step = workflow('RELEASE-EXISTING.yml')['jobs']['publish']['steps'][0]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'source-profile.txt').write_text('TR3000-512')
            artifact = {'name': 'firmware-TR3000-512-123-1', 'expired': False, 'id': 42, 'digest': 'sha256:abc'}
            for items, expected in [([artifact], True), ([], False), ([artifact, artifact], False),
                                    ([dict(artifact, expired=True)], False),
                                    ([dict(artifact, name='firmware-AP3000M-123-1')], False)]:
                (root / 'source-artifacts.json').write_text(json.dumps({'artifacts': items}))
                result = self.execute(blocks(step)[2], root, SOURCE_RUN='123', GITHUB_OUTPUT=str(root / 'output'))
                self.assertEqual(result.returncode == 0, expected, result.stderr)

    def test_uploaded_asset_must_match_complete_local_set_size_and_digest(self):
        step = next(s for s in workflow('DEVICE-CORE.yml')['jobs']['publish']['steps']
                    if s['name'] == 'Verify uploaded release asset digests')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upload = root / 'release/wrt/upload'
            upload.mkdir(parents=True)
            (upload / 'image.bin').write_bytes(b'accepted bytes')
            asset = {'name': 'image.bin', 'size': 14, 'digest': 'sha256:' + hashlib.sha256(b'accepted bytes').hexdigest()}
            good = {'draft': True, 'tag_name': 'tag', 'assets': [asset]}
            cases = [good, dict(good, draft=False), dict(good, tag_name='other'), dict(good, assets=[]),
                     dict(good, assets=[asset, asset]), dict(good, assets=[dict(asset, size=1)]),
                     dict(good, assets=[dict(asset, digest='sha256:' + '0' * 64)])]
            for n, data in enumerate(cases):
                (root / 'uploaded-release.json').write_text(json.dumps(data))
                result = self.execute(blocks(step)[0], root, RELEASE_TAG='tag')
                self.assertEqual(result.returncode == 0, n == 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
