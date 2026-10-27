#!/usr/bin/env python3
"""Resolve an explicit source/feed snapshot; never silently fall back to latest."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

EXCLUDED = {"video", "pon_drivers", "pon_userspace"}


def git(tree, *args):
    return subprocess.check_output(["git", "-C", str(tree), *args], text=True).strip()


def sha(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("expected a full lowercase commit SHA")
    return value


def resolve(repo, name, url, branch, mode):
    if mode not in ("locked", "latest"):
        raise ValueError("source mode must be locked or latest")
    data = json.loads((repo / "Profiles/sources.lock.json").read_text(encoding="utf-8"))[name]
    if mode == "locked":
        if (url.removesuffix(".git"), branch) != (data["url"].removesuffix(".git"), data["branch"]):
            raise ValueError("source differs from version lock; select latest explicitly or update the lock")
        sha(data["commit"])
        for item in data["feeds"].values():
            sha(item["commit"])
    return data


def feed_config(text, locked=None):
    rows, seen = [], set()
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:
            raise ValueError(f"unsupported feed definition: {line}")
        kind, name, address = parts
        if name in EXCLUDED:
            continue
        if kind not in ("src-git", "src-git-full") or name in seen:
            raise ValueError(f"unsupported or duplicate feed: {line}")
        seen.add(name)
        if locked is not None:
            item = locked[name]
            # Review URL changes instead of silently replacing a different feed.
            base = re.split(r"[;^]", address, maxsplit=1)[0]
            if base.removesuffix(".git") != item["url"].removesuffix(".git"):
                raise ValueError(f"{name}: feed URL differs from version lock")
            address = item["url"] + "^" + sha(item["commit"])
        rows.append(f"{kind} {name} {address}\n")
    if locked is not None and seen != set(locked):
        raise ValueError("feed set differs from version lock")
    return "".join(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("source", "feeds", "verify"))
    args = parser.parse_args()
    repo = Path(os.environ["GITHUB_WORKSPACE"])
    tree = repo / "wrt"
    reports = repo / "build-reports"
    reports.mkdir(exist_ok=True)
    name = os.environ["WRT_PROFILE"]
    url, branch = os.environ["WRT_REPO"], os.environ["WRT_BRANCH"]
    mode = os.environ.get("WRT_SOURCE_MODE", "locked")
    lock = resolve(repo, name, url, branch, mode)
    if args.command == "source":
        revision = lock["commit"] if mode == "locked" else branch
        # Keep compatibility with the old C8 pin, but never mix two conflicting locks.
        if name == "C8-688":
            lines = (repo / "Scripts/C8-688/source-pin.txt").read_text().splitlines()
            pin = next((line.split("#", 1)[0].strip() for line in lines if line.split("#", 1)[0].strip()), "auto")
            if pin not in ("auto", "master", "main", "HEAD"):
                sha(pin)
                if mode == "locked" and pin != revision:
                    raise ValueError("C8 source-pin conflicts with sources.lock.json")
                revision = pin
        if revision.startswith("-"):
            raise ValueError("invalid source revision")
        git(tree, "init", "-q")
        git(tree, "remote", "add", "origin", url)
        git(tree, "fetch", "--depth=1", "origin", revision)
        git(tree, "checkout", "--detach", "FETCH_HEAD")
        actual = git(tree, "rev-parse", "HEAD")
        if mode == "locked" and actual != lock["commit"]:
            raise ValueError("source revision mismatch")
        (reports / "source-commit.txt").write_text(actual + "\n")
        with Path(os.environ["GITHUB_ENV"]).open("a") as stream:
            stream.write("WRT_HASH=" + actual + "\n")
    elif args.command == "feeds":
        source = tree / "feeds.conf"
        if not source.exists():
            source = tree / "feeds.conf.default"
        text = feed_config(source.read_text(), lock["feeds"] if mode == "locked" else None)
        (tree / "feeds.conf").write_text(text, newline="\n")
    else:
        feeds = {}
        for line in (tree / "feeds.conf").read_text().splitlines():
            _kind, feed, address = line.split()
            actual = git(tree / "feeds" / feed, "rev-parse", "HEAD")
            if mode == "locked" and actual != lock["feeds"][feed]["commit"]:
                raise ValueError(f"{feed}: fetched feed revision mismatch")
            feeds[feed] = {"url": re.split(r"[;^]", address, maxsplit=1)[0], "commit": actual}
        snapshot = {"url": url, "branch": branch, "commit": git(tree, "rev-parse", "HEAD"), "feeds": feeds}
        (reports / "source-lock.json").write_text(json.dumps({name: snapshot}, indent=2) + "\n")


if __name__ == "__main__":
    main()
