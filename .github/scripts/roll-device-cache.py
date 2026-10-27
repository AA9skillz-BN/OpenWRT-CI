#!/usr/bin/env python3
"""Retain the current and newest cache of each kind, within this device/ref only."""
import json
import os
import subprocess


def main():
    prefix = os.environ["DEVICE_CACHE_PREFIX"]
    if not prefix.startswith("wrt-v2-"):
        raise ValueError("refusing to prune outside device cache namespace")
    repo = os.environ["GITHUB_REPOSITORY"]
    entries = json.loads(subprocess.check_output([
        "gh", "cache", "list", "--repo", repo, "--ref", os.environ["GITHUB_REF"],
        "--key", prefix + "-", "--limit", "100", "--json", "id,key,createdAt"], text=True))
    current = {os.environ[key] for key in ("DEVICE_DL_KEY", "DEVICE_CCACHE_KEY", "DEVICE_STAGING_KEY")}
    for kind in ("dl", "ccache", "staging"):
        group = sorted((e for e in entries if e["key"].startswith(f"{prefix}-{kind}-")),
                       key=lambda e: e["createdAt"], reverse=True)
        # If the current cache was not saved (or hasn't become visible), keep all
        # fallback generations for that kind. Never delete a later concurrent save.
        if not any(e["key"] in current for e in group):
            continue
        for entry in group[1:]:
            if entry["key"] not in current:
                subprocess.run(["gh", "cache", "delete", str(entry["id"]), "--repo", repo], check=True)


if __name__ == "__main__":
    main()
