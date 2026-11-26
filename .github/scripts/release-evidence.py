#!/usr/bin/env python3
"""Check release inputs and bind recovery to one successful original attempt."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from actions_evidence import (DEVICE_WORKFLOWS, api, artifact_for_run, device_run,
                              digest, download_zip, number, require, require_steps, sha, unzip_firmware)

ROOT = Path("release")
UPLOAD = ROOT / "wrt/upload"
NOTES = ROOT / "build-reports/release-notes.md"


def output(name, value, file="GITHUB_OUTPUT"):
    with open(os.environ[file], "a", encoding="utf-8") as stream:
        stream.write(f"{name}={value}\n")


def select(repo, run_id, artifact_id):
    run = device_run(repo, run_id, "main")
    profile = next(profile for profile, path in DEVICE_WORKFLOWS.items() if path == run["path"])
    artifact, attempt = artifact_for_run(repo, run, profile, "firmware", artifact_id)
    require_steps(repo, run_id, attempt,
                  {"Compile Firmware", "Verify and Package Device Firmware", "Upload Validated Firmware"})
    commit = sha(run["head_sha"])
    archive = Path("artifact.zip")
    download_zip(repo, artifact, archive)
    unzip_firmware(archive, ROOT)
    tag = verify_local(commit, profile, run_id, attempt)
    output("BUILD_SHA", commit, "GITHUB_ENV")
    output("tag", tag)
    output("artifact_id", number(artifact["id"]))


def verify_logic(repo, commit, branch):
    runs = api(repo, f"actions/workflows/BUILD-CHECKS.yml/runs?head_sha={sha(commit)}&per_page=100",
               "workflow_runs")
    matches = [run for run in runs if run["head_repository"]["full_name"] == repo
               and run["head_branch"] == branch and run["head_sha"] == commit
               and run["event"] in ("push", "workflow_dispatch")]
    require(matches, f"Build logic checks have no run for {commit} on {branch}")
    recent = max(matches, key=lambda run: run["id"])
    status = recent["conclusion"] if recent["status"] == "completed" else recent["status"]
    require(status == "success", f"Build logic checks for {commit} are {status}: {recent['html_url']}")
    print(f"Build logic checks passed for {commit}: {recent['html_url']}")


def verify_local(commit, profile, run_id, attempt=None):
    require((UPLOAD / "ci-commit.txt").read_text().strip() == sha(commit),
            "Firmware CI commit differs from the selected run")
    require(f"<!-- firmware-model: {profile.lower()} -->" in NOTES.read_text(encoding="utf-8"),
            "Firmware notes refer to a different device")
    configs = list(UPLOAD.glob("Config-*.txt"))
    require(len(configs) == 1, "Expected one final firmware configuration")
    tag = configs[0].stem.removeprefix("Config-")
    prefix = re.escape(profile) if attempt is not None else r"(?:preview-)?" + re.escape(profile)
    suffix = number(attempt) if attempt is not None else r"[1-9][0-9]*"
    require(re.fullmatch(prefix + r"-[A-Za-z0-9._-]+-" + number(run_id) + "-" + suffix, tag),
            "Firmware tag differs from the selected device, run or attempt")
    files = set()
    for line in (UPLOAD / "sha256sums").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.+-]+)", line)
        require(match, "Malformed firmware checksum line")
        expected, filename = match.groups()
        require(filename not in files and filename not in (".", "..", "sha256sums"),
                f"Invalid checksum entry: {filename}")
        path = UPLOAD / filename
        require(path.is_file() and not path.is_symlink() and digest(path) == expected,
                f"Firmware checksum mismatch: {filename}")
        files.add(filename)
    require(files == {path.name for path in UPLOAD.iterdir()} - {"sha256sums"},
            "Checksum list must cover every firmware attachment")
    return tag


def verify_tag(repo, tag, commit, create=False):
    subprocess.run(["git", "check-ref-format", f"refs/tags/{tag}"], check=True)
    ref = f"refs/tags/{tag}"
    matches = [entry for entry in api(repo, f"git/matching-refs/tags/{tag}") if entry["ref"] == ref]
    if not matches and create:
        # The optional credential is scoped to this tag-creation request only.
        env = os.environ.copy()
        if env.get("FIRMWARE_TAG_TOKEN"):
            env["GH_TOKEN"] = env["FIRMWARE_TAG_TOKEN"]
        args = ["gh", "api", "--method", "POST", f"repos/{repo}/git/refs", "-f", f"ref={ref}", "-f", f"sha={commit}"]
        created = subprocess.run(args, capture_output=True, env=env, text=True)
        if created.returncode:
            matches = [entry for entry in api(repo, f"git/matching-refs/tags/{tag}") if entry["ref"] == ref]
            require(matches, f"Could not create exact tag {tag}: {created.stderr.strip()}."
                    " For historical commits GitHub may require FIRMWARE_TAG_TOKEN with Contents and Workflows write.")
        else:
            matches = [json.loads(created.stdout)]
    require(len(matches) == 1 and matches[0]["ref"] == ref
            and matches[0]["object"]["type"] == "commit" and matches[0]["object"]["sha"] == sha(commit),
            f"Release tag {tag} does not point directly to {commit}")


def refuse_existing(repo, tag, own_id=None):
    data = subprocess.check_output(["gh", "api", "--paginate", "--slurp", f"repos/{repo}/releases?per_page=100"])
    require(not any(release["tag_name"] == tag and release["id"] != own_id
                    for page in json.loads(data) for release in page),
            f"Release or draft already exists for {tag}")


def create_draft(repo, tag, prerelease):
    refuse_existing(repo, tag)
    # POST only. An existing tag always controls which commit is released.
    args = ["gh", "api", "--method", "POST", f"repos/{repo}/releases",
            "-f", f"tag_name={tag}", "-f", f"name={tag}", "-F", "draft=true",
            "-F", f"prerelease={str(prerelease).lower()}", "-F", f"body=@{NOTES}"]
    draft = json.loads(subprocess.check_output(args))
    require(draft["draft"] is True and draft["tag_name"] == tag, "GitHub returned an unexpected draft")
    release_id = number(draft["id"])
    output("id", release_id)
    for path in sorted(UPLOAD.iterdir()):
        url = f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets?" + urlencode({"name": path.name})
        with path.open("rb") as stream:
            request = Request(url, data=stream, method="POST", headers={
                "Content-Type": "application/octet-stream", "Content-Length": str(path.stat().st_size),
            })
            request.add_unredirected_header("Authorization", "Bearer " + os.environ["GH_TOKEN"])
            with urlopen(request, timeout=180) as response:
                require(response.status == 201, f"Upload did not create an asset: {path.name}")


def publish(repo, tag, release_id, commit):
    verify_tag(repo, tag, commit)
    refuse_existing(repo, tag, int(release_id))
    prerelease = os.environ["GITHUB_REF"] != "refs/heads/main"
    subprocess.run(["gh", "api", "--method", "PATCH", f"repos/{repo}/releases/{release_id}",
                    "-F", "draft=false", "-f", f"make_latest={str(not prerelease).lower()}"], check=True)


def verify_uploaded(release, tag):
    require(release["draft"] is True and release["tag_name"] == tag,
            "Uploaded release must be the expected draft")
    local = {path.name: path for path in UPLOAD.iterdir() if path.is_file()}
    assets = release["assets"]
    require(len(assets) == len(local) and {asset["name"] for asset in assets} == set(local),
            "Draft release attachment set differs from the verified firmware")
    for asset in assets:
        path = local[asset["name"]]
        require(asset["size"] == path.stat().st_size and asset.get("digest") == "sha256:" + digest(path),
                f"Draft release asset differs from verified firmware: {path.name}")
    print(f"Verified {len(assets)} uploaded release asset sizes and SHA-256 digests")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("select", "logic", "local", "tag", "new", "draft", "uploaded", "publish"))
    parser.add_argument("--create-tag", action="store_true")
    args = parser.parse_args()
    repo = os.environ["GITHUB_REPOSITORY"]
    if args.command == "select":
        selected_id = os.environ.get("SOURCE_ARTIFACT", "")
        select(repo, number(os.environ["SOURCE_RUN"]), number(selected_id) if selected_id else None)
    elif args.command == "logic":
        verify_logic(repo, os.environ["BUILD_SHA"], os.environ["GITHUB_REF_NAME"])
    elif args.command == "local":
        tag = verify_local(os.environ["BUILD_SHA"], os.environ["WRT_PROFILE"], os.environ["GITHUB_RUN_ID"])
        require(tag == os.environ["RELEASE_TAG"], "Firmware configuration tag differs from the reserved tag")
    elif args.command == "tag":
        verify_tag(repo, os.environ["RELEASE_TAG"], os.environ["BUILD_SHA"], args.create_tag)
    elif args.command == "new":
        refuse_existing(repo, os.environ["RELEASE_TAG"])
    elif args.command == "draft":
        create_draft(repo, os.environ["RELEASE_TAG"], os.environ["GITHUB_REF"] != "refs/heads/main")
    elif args.command == "publish":
        publish(repo, os.environ["RELEASE_TAG"], number(os.environ["RELEASE_ID"]), os.environ["BUILD_SHA"])
    elif args.command == "uploaded":
        release = api(repo, f"releases/{number(os.environ['RELEASE_ID'])}")
        verify_uploaded(release, os.environ["RELEASE_TAG"])


if __name__ == "__main__":
    main()
