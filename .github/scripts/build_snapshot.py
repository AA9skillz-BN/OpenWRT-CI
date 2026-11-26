"""Capture configuration preview inputs and reproduce them on a new runner."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

from actions_evidence import (DEVICE_WORKFLOWS, artifact_for_run, device_run,
                              digest, download_zip, number, require, require_steps)

FILENAME = "build-snapshot.json"
INPUTS = ("WRT_REPO", "WRT_BRANCH", "WRT_SOURCE", "WRT_SOURCE_MODE", "WRT_PLUGIN_MODE",
          "WRT_PACKAGE", "WRT_THEME", "WRT_NAME", "WRT_SSID", "WRT_IP")


def read_json(path):
    return json.loads(path.read_bytes())


def replay(repo):
    path = repo / "build-reports/preview-inputs.json"
    if os.environ.get("WRT_PREVIEW_RUN_ID"):
        return read_json(path)
    return None


def identity():
    return {"schema": 1, "repository": os.environ["GITHUB_REPOSITORY"],
            "ref": os.environ["GITHUB_REF"], "ci_sha": os.environ["GITHUB_SHA"],
            "profile": os.environ["WRT_PROFILE"],
            "inputs": {name: os.environ.get(name, "") for name in INPUTS}}


def load_preview(repo):
    wanted = identity()
    run_id = number(os.environ["WRT_PREVIEW_RUN_ID"])
    remote = wanted["repository"]
    branch = wanted["ref"].removeprefix("refs/heads/")
    require(wanted["ref"] == f"refs/heads/{branch}", "Preview replay requires a branch")
    run = device_run(remote, run_id, branch)
    profile = wanted["profile"]
    require(run["head_sha"] == wanted["ci_sha"] and run["path"] == DEVICE_WORKFLOWS[profile]
            and run["conclusion"] == "success", "Preview must pass for this device and exact CI commit")
    attempt = run["run_attempt"]
    # A previous successful attempt does not certify a different attempt.
    artifact, selected = artifact_for_run(remote, run, profile, "config", only_attempt=attempt)
    require(selected == attempt, "Preview artifact is not from the successful run attempt")
    require_steps(remote, run_id, attempt, {"Upload Configuration Preview"})
    with tempfile.TemporaryDirectory() as scratch:
        archive = Path(scratch) / "preview.zip"
        download_zip(remote, artifact, archive)
        with zipfile.ZipFile(archive) as bundle:
            filenames = bundle.namelist()
            candidates = [name for name in filenames if re.fullmatch(r"Config-[A-Za-z0-9._-]+\.txt", name)]
            require(len(filenames) == 2 and FILENAME in filenames and len(candidates) == 1,
                    "Expected a preview snapshot and a single configuration")
            require(all(info.file_size <= 10 * 1024 * 1024 for info in bundle.infolist()),
                    "Preview evidence exceeds size limit")
            snapshot = json.loads(bundle.read(FILENAME))
            configuration = hashlib.sha256(bundle.read(candidates[0])).hexdigest()
    require(all(snapshot.get(key) == value for key, value in wanted.items()),
            "Preview build inputs differ. Match the preview's source, modes and package input.")
    require(snapshot["config_sha256"] == configuration, "Preview configuration SHA-256 differs")
    reports = repo / "build-reports"
    reports.mkdir(exist_ok=True)
    (reports / "preview-inputs.json").write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    (reports / "preview-origin.json").write_text(json.dumps({
        "run_id": int(run_id), "attempt": attempt, "artifact_id": artifact["id"], "digest": artifact["digest"],
    }, indent=2) + "\n", encoding="utf-8")
    print(f"Replaying configuration preview {run_id} attempt {attempt}")


def capture(repo, tree):
    reports = repo / "build-reports"
    snapshot = {
        **identity(),
        "source": read_json(reports / "source-lock.json")[os.environ["WRT_PROFILE"]],
        "plugins": read_json(reports / "plugin-resolution.json"),
        "mihomo": read_json(reports / "openclash-core.json"),
        "config_sha256": digest(tree / ".config"),
    }
    expected = replay(repo)
    if expected is not None:
        differing = [key for key, value in snapshot.items() if value != expected.get(key)]
        require(not differing, f"Preview build inputs changed: {', '.join(differing)}")
    (reports / FILENAME).write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("load", "capture"))
    args = parser.parse_args()
    repo = Path(os.environ["GITHUB_WORKSPACE"])
    if args.command == "load":
        load_preview(repo)
    else:
        capture(repo, repo / "wrt")


if __name__ == "__main__":
    main()
