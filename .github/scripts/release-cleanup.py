"""Keep three releases per device and channel; default to a read-only plan."""
import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import os
import re
import subprocess

MODELS = {"ap3000m", "tr3000-512", "c8-688"}
WORKFLOWS = {"ap3000m": "AP3000M.yml", "tr3000-512": "TR3000.yml", "c8-688": "C8-688.yml"}
OWNER = re.compile(r"<!-- firmware-ci: repo=([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+) "
                   r"run=([1-9][0-9]*) attempt=([1-9][0-9]*) sha=([0-9a-f]{40}) "
                   r"tag=([A-Za-z0-9._-]+) started=([^\s]+) -->")


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("missing timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp needs a timezone")
    return result


def draft_owner(release, repo):
    if not release.get("draft") or model_of(release) not in MODELS:
        return None
    matches = OWNER.findall(release.get("body") or "")
    if len(matches) != 1:
        return None
    owner, run, attempt, sha, tag, started = matches[0]
    if owner != repo or tag != release["tag_name"] or not tag.endswith(f"-{run}-{attempt}"):
        return None
    try:
        return {"run": run, "attempt": attempt, "sha": sha, "started": timestamp(started)}
    except (ValueError, TypeError):
        return None


def stale_draft(release, repo, run, now=None):
    owner = draft_owner(release, repo)
    if not owner:
        return False
    if (str(run.get("id")) != owner["run"] or str(run.get("run_attempt")) != owner["attempt"]
            or run.get("head_sha") != owner["sha"] or run.get("status") != "completed"
            or run.get("conclusion") not in {"failure", "cancelled", "timed_out", "startup_failure", "stale"}
            or (run.get("head_repository") or {}).get("full_name", "").lower() != repo.lower()
            or (run.get("path") or "").split("@", 1)[0] != ".github/workflows/" + WORKFLOWS[model_of(release)]):
        return False
    try:
        # Release.created_at can be the source commit's date, not draft creation.
        last_activity = max(owner["started"], timestamp(run["updated_at"]), timestamp(release["updated_at"]))
    except (KeyError, ValueError, TypeError):
        return False
    return (now or datetime.now(timezone.utc)) - last_activity >= timedelta(days=1)


def api(path):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True, encoding="utf-8"))


def clean_drafts(releases, repo, apply=False):
    for release in releases:
        owner = draft_owner(release, repo)
        if not owner:
            continue
        try:
            run = api(f"repos/{repo}/actions/runs/{owner['run']}/attempts/{owner['attempt']}")
        except subprocess.CalledProcessError:
            print(f"draft: KEEP {release['tag_name']} (run could not be verified)", flush=True)
            continue
        delete = stale_draft(release, repo, run)
        print(f"draft: {'DELETE' if delete else 'KEEP'} {release['tag_name']}", flush=True)
        if delete and apply:
            # Re-read both records before deleting; publication may have resumed.
            current = api(f"repos/{repo}/releases/{release['id']}")
            run = api(f"repos/{repo}/actions/runs/{owner['run']}/attempts/{owner['attempt']}")
            if stale_draft(current, repo, run):
                subprocess.run(["gh", "api", "--method", "DELETE", f"repos/{repo}/releases/{release['id']}"], check=True)


def model_of(release):
    marker = re.search(r"<!--\s*firmware-model:\s*([a-zA-Z0-9-]+)\s*-->", release.get("body") or "")
    if marker:
        model = marker.group(1).lower()
        return model if model in MODELS else None
    tag = re.sub(r"^preview-", "", release["tag_name"], flags=re.I)
    for pattern, model in ((r"^AP3000M-", "ap3000m"),
                           (r"^TR3000[-_]512(?:MB)?-", "tr3000-512"),
                           (r"^C8-688-", "c8-688")):
        if re.match(pattern, tag, re.I):
            return model
    return None


def plan(releases, keep=3):
    if keep < 1:
        raise ValueError("must retain at least one release per channel")
    groups = defaultdict(list)
    for release in releases:
        if release.get("draft"):
            continue
        model = model_of(release)
        if model:
            preview = bool(release.get("prerelease")) or release["tag_name"].lower().startswith("preview-")
            groups[(model, preview)].append(release)
    result = []
    for (model, preview), group in sorted(groups.items()):
        group.sort(key=lambda r: (r.get("published_at") or r["created_at"], r["id"]), reverse=True)
        for index, release in enumerate(group):
            result.append((model, "preview" if preview else "stable", index >= keep, release))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    repo = os.environ["GITHUB_REPOSITORY"]
    pages = json.loads(subprocess.check_output(
        ["gh", "api", "--paginate", "--slurp", f"repos/{repo}/releases?per_page=100"], text=True, encoding="utf-8"))
    releases = [r for page in pages for r in page]
    clean_drafts(releases, repo, args.apply)
    for model, channel, delete, release in plan(releases):
        tag = release["tag_name"]
        print(f'{model}/{channel}: {"DELETE" if delete else "KEEP"} {tag}', flush=True)
        if delete and args.apply:
            subprocess.run(["gh", "release", "delete", tag, "--repo", repo, "--yes"], check=True)


if __name__ == "__main__":
    main()
