"""Publish an independently named firmware release using the GitHub CLI."""
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
import os
import re
import subprocess


p = argparse.ArgumentParser()
p.add_argument('--model', required=True)
p.add_argument('--label', required=True)
p.add_argument('--source', required=True)
p.add_argument('--branch', required=True)
p.add_argument('--commit', required=True)
p.add_argument('--notes', required=True)
p.add_argument('--assets', required=True)
a = p.parse_args()
assert re.fullmatch(r'[a-z0-9-]+', a.model)
files = sorted(x for x in Path(a.assets).iterdir() if x.is_file())
if not files:
    raise SystemExit('No release assets')
date = datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d-%H%M%S')
run = os.environ['GITHUB_RUN_NUMBER']
attempt = os.environ.get('GITHUB_RUN_ATTEMPT', '1')
tag = f'{a.model}-{date}-r{run}-a{attempt}'
body = Path(a.notes).read_text(encoding='utf-8')
# The stable marker is the cleanup grouping key, independent of display titles.
body = f'<!-- firmware-model: {a.model} -->\n\n' + body
body += (f'\n\n## 本次构建\n\n源码：{a.source}\n\n分支：`{a.branch}`'
         f'\n\n源码提交：`{a.commit}`\n\n仓库配置提交：`{os.environ["GITHUB_SHA"]}`'
         f'\n\n构建记录：{os.environ["GITHUB_SERVER_URL"]}/{os.environ["GITHUB_REPOSITORY"]}'
         f'/actions/runs/{os.environ["GITHUB_RUN_ID"]}\n')
notes = Path(a.assets) / 'release-notes.md'
notes.write_text(body, encoding='utf-8')
subprocess.run(['gh', 'release', 'create', tag, '--repo', os.environ['GITHUB_REPOSITORY'],
                '--target', os.environ['GITHUB_SHA'], '--title', f'{a.label} | {a.branch} | {date}',
                '--notes-file', str(notes), '--latest=false', *map(str, files)], check=True)
