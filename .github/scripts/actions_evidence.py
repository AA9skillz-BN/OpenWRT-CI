"""Verify GitHub Actions evidence before replay or promotion."""
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import zipfile


DEVICE_WORKFLOWS = {
    "AP3000M": ".github/workflows/AP3000M.yml",
    "TR3000-512": ".github/workflows/TR3000.yml",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value),
            "Expected a full commit SHA")
    return value


def number(value):
    require(re.fullmatch(r"[1-9][0-9]*", str(value)), "Expected a positive numeric Actions ID")
    return str(value)


def digest(path):
    with path.open("rb") as stream:
        hasher = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
        return hasher.hexdigest()


def api(repo, path, field=None):
    args = ["gh", "api"]
    if field is not None:
        args.extend(("--paginate", "--slurp"))
    response = json.loads(subprocess.check_output([*args, f"repos/{repo}/{path}"]))
    return response if field is None else [item for page in response for item in page[field]]


def device_run(repo, run_id, branch):
    run = api(repo, f"actions/runs/{number(run_id)}")
    require(run["event"] == "workflow_dispatch" and run["head_repository"]["full_name"] == repo
            and run["head_branch"] == branch and run["status"] == "completed",
            "Expected a completed manual device run from this repository and branch")
    require(run["path"] in DEVICE_WORKFLOWS.values(), "Expected a supported device workflow")
    sha(run["head_sha"])
    return run


def artifact_for_run(repo, run, profile, kind, artifact_id=None, *, only_attempt=None):
    run_id = number(run["id"])
    artifacts = api(repo, f"actions/runs/{run_id}/artifacts?per_page=100", "artifacts")
    prefix = f"{kind}-{profile}-{run_id}-"
    matches = [item for item in artifacts if not item["expired"]
               and re.fullmatch(re.escape(prefix) + r"[1-9][0-9]*", item["name"])
               and (artifact_id is None or number(item["id"]) == artifact_id)
               and (only_attempt is None or item["name"] == prefix + number(only_attempt))]
    require(len(matches) == 1, f"Expected one retained {kind} artifact. Specify its ID if several attempts match.")
    item = matches[0]
    attempt = int(item["name"].removeprefix(prefix))
    require(attempt <= run["run_attempt"], "Artifact attempt is newer than its run")
    return item, attempt


def require_steps(repo, run_id, attempt, names):
    jobs = api(repo, f"actions/runs/{number(run_id)}/attempts/{number(attempt)}/jobs?per_page=100", "jobs")
    require(any(job["conclusion"] == "success"
                and names <= {step["name"] for step in job["steps"] if step["conclusion"] == "success"}
                for job in jobs), "Selected run attempt lacks the successful build and upload steps")


def download_zip(repo, artifact, archive):
    expected = artifact.get("digest") or ""
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", expected), "Artifact API SHA-256 is missing")
    with archive.open("wb") as stream:
        subprocess.run(["gh", "api", f"repos/{repo}/actions/artifacts/{number(artifact['id'])}/zip"],
                       stdout=stream, check=True)
    require("sha256:" + digest(archive) == expected, "Artifact ZIP SHA-256 differs from the API")


def unzip_firmware(archive, root):
    with zipfile.ZipFile(archive) as bundle:
        files = [info for info in bundle.infolist() if not info.is_dir()]
        paths = [info.filename for info in files]
        require(len(paths) == len(set(paths)), "Duplicate firmware artifact paths")
        for info in files:
            name = info.filename
            require(name == "build-reports/release-notes.md"
                    or re.fullmatch(r"wrt/upload/[A-Za-z0-9_.+-]+", name)
                    and Path(name).name not in (".", ".."),
                    f"Unexpected firmware artifact path: {name}")
            require(not stat.S_ISLNK(info.external_attr >> 16), "Unexpected firmware symlink")
        for info in files:
            bundle.extract(info, root)
