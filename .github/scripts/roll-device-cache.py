#!/usr/bin/env python3
"""Prune device caches and aged shared toolchains, always within this ref."""
from datetime import datetime, timedelta, timezone
import json
import os
import re
import subprocess


def main():
    prefix = os.environ["DEVICE_CACHE_PREFIX"]
    if not re.fullmatch(r"wrt-v2-(?:ap3000m|c8-688|tr3000-512)-linux-x64", prefix):
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

    shared = os.environ.get("TOOLCHAIN_CACHE_PREFIX")
    if not shared:
        return
    if not re.fullmatch(r"wrt-v3-toolchain-[a-z0-9_-]+", shared):
        raise ValueError("refusing to prune outside shared toolchain namespace")
    key = os.environ["DEVICE_STAGING_KEY"]
    if not re.fullmatch(re.escape(shared) + r"-[a-f0-9]{64}", key):
        raise ValueError("toolchain key does not match its namespace")
    shared_entries = json.loads(subprocess.check_output([
        "gh", "cache", "list", "--repo", repo, "--ref", os.environ["GITHUB_REF"],
        "--key", shared + "-", "--limit", "100", "--json", "id,key,createdAt"], text=True))
    shared_entries = sorted((e for e in shared_entries if re.fullmatch(
        re.escape(shared) + r"-[a-f0-9]{64}", e["key"])), key=lambda e: e["createdAt"], reverse=True)
    bad = os.environ.get("BAD_TOOLCHAIN_KEY", "")
    if bad:
        if bad != key:
            raise ValueError("bad cache key is not this build's exact toolchain key")
        observed = datetime.fromisoformat(os.environ["BAD_TOOLCHAIN_DETECTED_AT"].replace("Z", "+00:00"))
        for entry in list(shared_entries):
            created = datetime.fromisoformat(entry["createdAt"].replace("Z", "+00:00"))
            # A concurrent run may already have removed/replaced this key.
            # Never delete a replacement created after corruption was observed.
            if entry["key"] == bad and created <= observed:
                result = subprocess.run(["gh", "cache", "delete", str(entry["id"]), "--repo", repo],
                                        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                if result.returncode and "404" not in result.stderr:
                    raise RuntimeError(result.stderr)
                shared_entries.remove(entry)
    if not any(e["key"] == key for e in shared_entries):
        return  # A failed/racing save must not remove any fallback cache.
    # Shared consumers may have different fingerprints. Keep the current key,
    # the newest two generations, and ALL entries younger than seven days.
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    for entry in shared_entries[2:]:
        created = datetime.fromisoformat(entry["createdAt"].replace("Z", "+00:00"))
        if entry["key"] != key and created < cutoff:
            result = subprocess.run(["gh", "cache", "delete", str(entry["id"]), "--repo", repo],
                                    text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if result.returncode and "404" not in result.stderr:
                raise RuntimeError(result.stderr)
    # Retire only this device's obsolete v2 staging after its v3 save is visible.
    # Leave dl/ccache and other devices/ref namespaces untouched.
    for entry in entries:
        if entry["key"].startswith(prefix + "-staging-"):
            subprocess.run(["gh", "cache", "delete", str(entry["id"]), "--repo", repo], check=True)


if __name__ == "__main__":
    main()
