"""Keep three releases per device and channel; default to a read-only plan."""
import argparse
from collections import defaultdict
import json
import os
import re
import subprocess

MODELS = {"ap3000m", "tr3000-512", "c8-688"}


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
    for model, channel, delete, release in plan([r for page in pages for r in page]):
        tag = release["tag_name"]
        print(f'{model}/{channel}: {"DELETE" if delete else "KEEP"} {tag}', flush=True)
        if delete and args.apply:
            subprocess.run(["gh", "release", "delete", tag, "--repo", repo, "--yes"], check=True)


if __name__ == "__main__":
    main()
