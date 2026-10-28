#!/usr/bin/env python3
"""Single-device build contract. Uses only the Python standard library."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile
from device_image import verify_payload
import toolchain_cache


def run(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def read_config(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            values[line[2:-11]] = "n"
    return values


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def profile(repo, name):
    if name not in ("TR3000-512", "AP3000M", "C8-688"):
        raise ValueError(f"unsupported device profile: {name}")
    return json.loads((repo / "Profiles" / f"{name}.json").read_text(encoding="utf-8"))


def merge_config(repo, tree, name, data):
    values = {}
    for item in [*data["config_fragments"], f"Config/{name}.txt"]:
        values.update(read_config(repo / item))
    requested = dict(values)
    private = repo / "Config/PRIVATE.txt"
    if private.exists():
        requested.update(read_config(private))
    for line in os.environ.get("WRT_PACKAGE", "").replace("\\n", "\n").splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            requested[key] = value
    for names in data.get("excluded_feed_packages", {}).values():
        for package in names:
            if requested.get(f"CONFIG_PACKAGE_{package}") in ("y", "m"):
                raise ValueError(f"{package}: excluded due to upstream Kconfig recursion; review profile before enabling")
    write(tree / ".config", "".join(f"{k}={v}\n" for k, v in values.items()))


def remove_package(path):
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        # Git packfiles may be read-only on Windows during local validation.
        def writable_remove(function, filename, _error):
            os.chmod(filename, stat.S_IWRITE | stat.S_IREAD)
            function(filename)
        shutil.rmtree(path, onerror=writable_remove)


def install_packages(repo, tree, data, reports):
    locks = json.loads((repo / "Profiles/packages.lock.json").read_text(encoding="utf-8"))
    provenance = {}
    for key in data["packages"]:
        item = locks[key]
        revision = item["commit"]
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise ValueError(f"{key}: expected a full commit SHA")
        # Remove exact conflicting package directories and installed feed links.
        # Never use substring matches: e.g. 'argon' must not delete unrelated packages.
        for name in item["replaces"]:
            for feed in (tree / "feeds").iterdir():
                if feed.is_dir():
                    for found in list(feed.rglob(name)):
                        if found.is_dir() and found.name == name:
                            remove_package(found)
            feeds = tree / "package/feeds"
            if feeds.exists():
                for feed in feeds.iterdir():
                    remove_package(feed / name)
        dest = tree / "package" / item["directory"]
        remove_package(dest)
        dest.mkdir(parents=True)
        run("git", "init", "-q", str(dest))
        run("git", "-C", str(dest), "remote", "add", "origin", item["url"])
        subprocess.run(["git", "-C", str(dest), "fetch", "--depth=1", "origin", revision], check=True)
        run("git", "-C", str(dest), "checkout", "--detach", "FETCH_HEAD")
        if run("git", "-C", str(dest), "rev-parse", "HEAD") != revision:
            raise ValueError(f"{key}: fetched revision mismatch")
        subdirs = item.get("subdirectories", [item["subdirectory"]] if item.get("subdirectory") else [])
        if subdirs:
            for subdir in subdirs:
                source = dest / subdir
                if not (source / "Makefile").is_file():
                    raise ValueError(f"{key}: package Makefile missing: {subdir}")
                extracted = tree / "package" / subdir
                remove_package(extracted)
                shutil.copytree(source, extracted)
            remove_package(dest)
        provenance[key] = item
    for directory in data.get("excluded_feed_packages", {}):
        # Exclude only unused broken recipes, keeping upstream feed sources intact.
        for feed in (tree / "package/feeds").iterdir() if (tree / "package/feeds").exists() else []:
            remove_package(feed / directory)
    write(reports / "packages.lock.json", json.dumps(provenance, indent=2) + "\n")


def check_kconfig_output(output):
    # Some upstream Make/Kconfig combinations print errors but exit with status 0.
    if re.search(r"(?:^|[\s:])error:|recursive dependency detected", output, re.IGNORECASE | re.MULTILINE):
        raise ValueError("Kconfig reported errors; see build-reports/defconfig.log")


def defconfig(tree, data, reports):
    requested = read_config(tree / ".config")
    result = subprocess.run(["make", "defconfig"], cwd=tree, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, check=False)
    write(reports / "defconfig.log", result.stdout)
    print(result.stdout, end="", flush=True)
    result.check_returncode()
    check_kconfig_output(result.stdout)
    actual = read_config(tree / ".config")
    changes = {key: {"requested": value, "resolved": actual.get(key, "unavailable")}
               for key, value in requested.items()
               if key.startswith("CONFIG_PACKAGE_") and value in ("y", "m")
               and actual.get(key) != value}
    write(reports / "config-resolution.json", json.dumps(changes, indent=2) + "\n")
    for key, change in sorted(changes.items()):
        print(f"::warning::{key}: requested {change['requested']}, resolved {change['resolved']}")
    check_config(tree, data)


def check_config(tree, data):
    cfg = read_config(tree / ".config")
    expected = data["device_symbol"]
    devices = {k for k, v in cfg.items() if k.startswith("CONFIG_TARGET_DEVICE_")
               and "_DEVICE_" in k and v == "y"}
    if devices != {expected}:
        raise ValueError(f"expected only {expected}; got {sorted(devices)}")
    required = ["CONFIG_TARGET_mediatek", "CONFIG_TARGET_mediatek_filogic",
                "CONFIG_CCACHE", *[f"CONFIG_PACKAGE_{p}" for p in data["required_packages"]]]
    missing = [key for key in required if cfg.get(key) != "y"]
    if missing:
        raise ValueError("defconfig dropped required options: " + ", ".join(missing))


def verify_image(tree, data, reports, repo=None):
    target = tree / "bin/targets/mediatek/filogic"
    # The matching manifest must belong to the same device/image prefix.
    images = list(target.glob(f"*{data['image_device']}*-sysupgrade.bin"))
    if len(images) != 1:
        raise ValueError(f"expected exactly one sysupgrade image, got {images}")
    image = images[0]
    # Image/Manifest uses IMG_PREFIX[-PROFILE_SANITIZED]. A normal single-device
    # source build leaves PROFILE unset and emits the target-wide IMG_PREFIX.
    end = image.name.index(data["image_device"]) + len(data["image_device"])
    manifest = image.with_name(image.name[:end] + ".manifest")
    if not manifest.is_file():
        prefix = image.name[:end - len(data["image_device"])].removesuffix("-")
        generic = image.with_name(prefix + ".manifest")
        if not generic.is_file():
            raise ValueError(f"missing matching manifest: {manifest} or {generic}")
        # A target-wide manifest is valid only for our enforced single-device
        # configuration. Never select an arbitrary manifest from the directory.
        check_config(tree, data)
        manifest = generic
    installed = {line.split()[0] for line in manifest.read_text().splitlines() if line.strip()}
    missing = set(data["required_packages"]) - installed
    if missing:
        raise ValueError("manifest missing: " + ", ".join(sorted(missing)))
    with tarfile.open(image) as archive:
        members = archive.getmembers()
        kernels = [m for m in members if m.isfile() and m.name.endswith("/kernel") and m.size > 0]
        roots = [m for m in members if m.isfile() and m.name.endswith("/root") and m.size > 0]
        if len(kernels) != 1 or len(roots) != 1:
            raise ValueError("sysupgrade must contain a nonempty kernel and root filesystem")
        if kernels[0].name.rsplit("/", 1)[0] != roots[0].name.rsplit("/", 1)[0]:
            raise ValueError("kernel/root belong to different sysupgrade directories")
        verify_payload(archive, kernels[0], roots[0], data,
                       repo or Path(os.environ["GITHUB_WORKSPACE"]), reports, tree=tree)
    # fwtool validates the actual embedded metadata, not the archive filename.
    fwtool = tree / "staging_dir/host/bin/fwtool"
    metadata = reports / "image-metadata.json"
    subprocess.run([str(fwtool), "-i", str(metadata), str(image)], check=True)
    meta = json.loads(metadata.read_text())
    supported = meta.get("new_supported_devices", meta.get("supported_devices", []))
    if data["supported_device"] not in supported:
        raise ValueError(f"wrong supported_devices: {supported}")
    if meta.get("version", {}).get("target") != "mediatek/filogic":
        raise ValueError("image metadata has the wrong target")
    write(reports / "verified-image.json", json.dumps({"image": image.name,
          "manifest": manifest.name, "supported_devices": supported}, indent=2) + "\n")
    return image, manifest


def package_ota_update(repo, upload, tag):
    source = repo / "Scripts/AP3000M/ota"
    abi = (source / "runtime-abi").read_text().strip()
    if not re.fullmatch(r"[0-9]+", abi):
        raise ValueError("invalid OTA runtime ABI")
    script = (source / "ap3000m-ota").read_bytes()
    (upload / "ap3000m-ota").write_bytes(script)
    metadata = {"schema": 1, "model": "ap3000m", "runtime_abi": abi,
                "tag": tag, "asset": "ap3000m-ota", "size": len(script),
                "sha256": hashlib.sha256(script).hexdigest(),
                "requires": ["curl", "jq", "sha256sum", "sysupgrade", "flock"]}
    write(upload / "ap3000m-ota-update.json", json.dumps(metadata, indent=2) + "\n")


def package(tree, repo, name, data, reports):
    check_config(tree, data)
    image, manifest = verify_image(tree, data, reports, repo)
    upload = tree / "upload"
    upload.mkdir(exist_ok=True)
    tag = os.environ["WRT_RELEASE_TAG"]
    shutil.copy2(tree / ".config", upload / f"Config-{tag}.txt")
    branch = os.environ["WRT_BRANCH"].replace("/", "-")
    outname = f"{image.stem}-{os.environ['WRT_INFO']}-{branch}-{os.environ['WRT_DATE']}.bin"
    shutil.copy2(image, upload / outname)
    shutil.copy2(manifest, upload / manifest.name)
    if name == "AP3000M":
        # verify_image has already checked these exact files in the squashfs.
        package_ota_update(repo, upload, tag)
    for file in reports.iterdir():
        if file.is_file() and file.name in ("source-commit.txt", "feeds.buildinfo", "packages.lock.json",
                                             "build-inputs.json", "image-metadata.json", "compatibility.txt", "ci-commit.txt",
                                             "config-resolution.json", "source-lock.json", "verified-payload.json",
                                             "toolchain-inputs.json", "toolchain-cache.json"):
            shutil.copy2(file, upload / file.name)
    # Do not discard evidence needed to reproduce or diagnose this release.
    checksums = []
    for file in sorted(upload.iterdir()):
        if file.is_file() and file.name != "sha256sums":
            with file.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(stream.read()).hexdigest()
            checksums.append(f"{digest}  {file.name}\n")
    write(upload / "sha256sums", "".join(checksums))
    text = (f"<!-- firmware-model: {name.lower()} -->\n"
            f"**{data['title']} 单机型固件**\n\n"
            f"设备：`{data['supported_device']}`\n\n"
            f"{data['release_note']}\n\n"
            f"源码：{os.environ['WRT_REPO']}\n\n分支：{os.environ['WRT_BRANCH']}\n\n"
            f"提交：`{run('git', 'rev-parse', 'HEAD', cwd=tree)}`\n\n"
            f"登录地址：{os.environ['WRT_IP']}；主机名 / Wi-Fi：{os.environ['WRT_NAME']}\n\n"
            "附件含 sysupgrade、实际配置、manifest、校验和及源码/feeds/插件版本记录。\n")
    compatibility = reports / "compatibility.txt"
    if compatibility.exists():
        text += "\n构建兼容处理：\n\n" + compatibility.read_text() + "\n"
    write(reports / "release-notes.md", text)


def cache_keys(repo, tree, name, reports):
    feeds = {p.name: run("git", "rev-parse", "HEAD", cwd=p)
             for p in sorted((tree / "feeds").iterdir()) if (p / ".git").exists()}
    config = (tree / ".config").read_text()
    write(reports / "source-commit.txt", run("git", "rev-parse", "HEAD", cwd=tree) + "\n")
    write(reports / "ci-commit.txt", run("git", "rev-parse", "HEAD", cwd=repo) + "\n")
    write(reports / "feeds.buildinfo", run(str(tree / "scripts/feeds"), "list", "-fs", cwd=tree) + "\n")
    compiler = run("gcc", "-dumpfullversion")
    inputs = {"schema": 2, "device": name, "source": run("git", "rev-parse", "HEAD", cwd=tree),
              "feeds": feeds, "config": config, "runner": run("uname", "-m"),
              "os": Path("/etc/os-release").read_text(), "gcc": compiler,
              "rust": run("rustc", "-vV"), "host": (reports / "host-toolchains.txt").read_text(),
              "scripts": {}, "packages": json.loads((reports / "packages.lock.json").read_text())}
    for folder in (repo / ".github/scripts", repo / "Scripts" / name, repo / "Profiles"):
        for file in sorted(folder.rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts:
                inputs["scripts"][str(file.relative_to(repo))] = hashlib.sha256(file.read_bytes()).hexdigest()
    # Include relevant source patches. Timestamped image overlays never affect
    # host/toolchain identity, so they must not force a cold toolchain every run.
    for folder in (tree / "target", tree / "package/network/config/firewall4"):
        for file in sorted(folder.rglob("*")):
            if file.is_file():
                inputs["scripts"]["source/" + str(file.relative_to(tree))] = hashlib.sha256(file.read_bytes()).hexdigest()
    serialized = json.dumps(inputs, sort_keys=True, ensure_ascii=False)
    write(reports / "build-inputs.json", serialized + "\n")
    # Full provenance remains above; installed tools have their own dependencies.
    staging, tool_inputs = toolchain_cache.fingerprint(tree)
    toolchain_cache.write(reports / toolchain_cache.INPUTS, {"key": staging, "inputs": tool_inputs})
    prefix = f"wrt-v2-{name.lower()}-linux-x64"
    # ccache validates compiler/content itself; toolchain staging never falls back.
    ccache = hashlib.sha256((compiler + inputs["rust"] + inputs["os"]).encode()).hexdigest()[:16]
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
        stream.write(f"prefix={prefix}\nstaging={staging}\n"
                     f"toolchain_prefix={staging.rsplit('-', 1)[0]}\n"
                     f"ccache={prefix}-ccache-{ccache}-\ndl={prefix}-dl-\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["merge", "packages", "check", "defconfig", "package", "cache"])
    parser.add_argument("--profile", default=os.environ.get("WRT_PROFILE"))
    parser.add_argument("--repo", type=Path, default=Path(os.environ.get("GITHUB_WORKSPACE", ".")))
    parser.add_argument("--tree", type=Path, default=Path.cwd())
    args = parser.parse_args()
    repo, tree = args.repo.resolve(), args.tree.resolve()
    data = profile(repo, args.profile)
    reports = repo / "build-reports"
    reports.mkdir(exist_ok=True)
    if args.command == "merge":
        merge_config(repo, tree, args.profile, data)
    elif args.command == "packages":
        install_packages(repo, tree, data, reports)
    elif args.command == "check":
        check_config(tree, data)
    elif args.command == "defconfig":
        defconfig(tree, data, reports)
    elif args.command == "package":
        package(tree, repo, args.profile, data, reports)
    elif args.command == "cache":
        cache_keys(repo, tree, args.profile, reports)


if __name__ == "__main__":
    main()
