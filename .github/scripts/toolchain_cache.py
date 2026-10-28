#!/usr/bin/env python3
"""Content-addressed installed tools/toolchain cache; never manufacture stamps."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess

SCHEMA = 3
EPOCH = 946684800  # Older than every supported build, independent of commit dates.
MANIFEST = ".ci-toolchain-cache.json"
BOOTSTRAP = ".ci-toolchain-bootstrap"
INPUTS = "toolchain-inputs.json"
STATE = "toolchain-cache.json"
CONFIG_SYMBOL = re.compile(r"\bCONFIG_[A-Za-z0-9_][A-Za-z0-9_-]*")
# LibreSSL 4.2.1's install-data-hook installs these aliases, although its
# verified source archive omits bn_dump.3. Accept only these exact, local
# documentation links; never extend this exception to binaries or libraries.
LIBRESSL_ABSENT_MAN_ALIASES = frozenset((
    "bn_add_words.3", "bn_div_words.3", "bn_expand.3", "bn_mul_add_words.3",
    "bn_mul_comba4.3", "bn_mul_comba8.3", "bn_mul_normal.3", "bn_mul_words.3",
    "bn_sqr_comba4.3", "bn_sqr_comba8.3", "bn_sqr_words.3", "bn_sub_words.3",
    "bn_wexpand.3",
))
# Expanded by OpenWrt itself, including the selected host tools and stamp name.
VARIABLES = (
    "ARCH", "ARCH_SUFFIX", "BOARD", "SUBTARGET", "CPU_TYPE", "CPU_SUBTYPE",
    "GCC_ARCH", "GCCV", "LIBC", "GNU_TARGET_NAME", "REAL_GNU_TARGET_NAME",
    "TARGET_CROSS", "TARGET_CFLAGS", "TARGET_CXXFLAGS", "TARGET_LDFLAGS",
    "HOST_CFLAGS", "HOST_CXXFLAGS", "HOST_LDFLAGS", "GNU_HOST_NAME", "HOST_OS", "HOST_ARCH",
    "STAGING_DIR_HOST", "TOOLCHAIN_DIR", "BUILD_DIR_HOST", "BUILD_DIR_TOOLCHAIN",
    "LINUX_VERSION", "LINUX_KERNEL_HASH", "LINUX_SOURCE", "LINUX_SITE", "LINUX_DIR",
    "KERNEL_PATCHVER", "GENERIC_PLATFORM_DIR", "GENERIC_BACKPORT_DIR",
    "GENERIC_PATCH_DIR", "GENERIC_HACK_DIR", "GENERIC_FILES_DIR",
    "PLATFORM_DIR", "PLATFORM_SUBDIR", "tools-y", "tools-",
    "toolchain/builddirs", "tools/stamp-compile", "toolchain/stamp-compile",
    "MAKEFILE_LIST",
)


def capture(*args, cwd=None, **kwargs):
    return subprocess.check_output(args, cwd=cwd, text=True, **kwargs).strip()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def json_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def config(path):
    values = {}
    for line in path.read_text().splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value
        elif line.startswith("# CONFIG_") and line.endswith(" is not set"):
            values[line[2:-11]] = ""  # Undefined in Make; literal '=n' is different.
    return values


def probe(tree):
    # The normal outer Makefile exports these before its recursive build.
    # Read that real preamble instead of assuming rules.mk initializes them:
    # an empty HOST_OS selects extra tools and a different completion stamp.
    host_names = ("HOST_OS", "HOST_ARCH", "GNU_HOST_NAME")
    host_probe = tree / "tmp/.ci-toolchain-host.mk"
    host_probe.parent.mkdir(parents=True, exist_ok=True)
    host_probe.write_text("\n".join([
        *[f"$(info CI_CACHE_HOST:{name}=$({name}))" for name in host_names],
        ".PHONY: ci-cache-host", "ci-cache-host:", "\t@:", "",
    ]), encoding="utf-8")
    result = capture("make", "--no-print-directory", "-s", "-f", "Makefile", "-f", str(host_probe),
                     "OPENWRT_BUILD=", "ci-cache-host", cwd=tree)
    host = dict(line.removeprefix("CI_CACHE_HOST:").split("=", 1)
                for line in result.splitlines() if line.startswith("CI_CACHE_HOST:"))
    if set(host) != set(host_names) or not all(host.values()):
        raise ValueError("OpenWrt top-level host probe returned incomplete variables")
    # No build target is executed. $(info) avoids shell interpolation of flags.
    makefile = "\n".join([
        "include $(TOPDIR)/rules.mk", "include $(INCLUDE_DIR)/kernel.mk",
        "include $(INCLUDE_DIR)/depends.mk", "include $(INCLUDE_DIR)/subdir.mk",
        "include $(TOPDIR)/tools/Makefile", "include $(TOPDIR)/toolchain/Makefile",
        *[f"$(info CI_CACHE:{name}=$({name}))" for name in VARIABLES],
        ".PHONY: ci-cache-inputs", "ci-cache-inputs:", "\t@:", "",
    ])
    # GNU Make materializes stdin as /tmp/GmXXXXXX and includes that random path
    # in MAKEFILE_LIST. Use our own known, generated file within the source tree.
    generated = tree / "tmp/.ci-toolchain-probe.mk"
    generated.parent.mkdir(parents=True, exist_ok=True)
    generated.write_text(makefile, encoding="utf-8")
    result = capture("make", "--no-print-directory", "-s", "-f", str(generated),
                     f"TOPDIR={tree}", *[f"{name}={host[name]}" for name in host_names],
                     "ci-cache-inputs", cwd=tree)
    values = dict(line.removeprefix("CI_CACHE:").split("=", 1)
                  for line in result.splitlines() if line.startswith("CI_CACHE:"))
    if set(values) != set(VARIABLES):
        raise ValueError("OpenWrt cache probe returned incomplete variables")
    return values


def package_versions(inventory, seeds):
    """Installed direct build dependencies and their Depends/Pre-Depends closure."""
    packages, by_name = {}, {}
    for line in inventory.splitlines():
        name, version, dependencies, predependencies = (line.split("\t") + [""]*4)[:4]
        packages[name] = (version, dependencies + "," + predependencies)
        by_name.setdefault(name.split(":")[0], []).append(name)
    pending, selected = list(seeds), {}
    while pending:
        name = pending.pop()
        for binary in by_name.get(name.split(":")[0], []):
            if binary in selected:
                continue
            version, dependencies = packages[binary]
            selected[binary] = version
            # Include every installed alternative; missing virtual providers are
            # covered by their concrete installed parent and executable hashes.
            for dependency in re.split(r"[,|]", dependencies):
                match = re.match(r"\s*([a-zA-Z0-9.+-]+)(?::[a-zA-Z0-9-]+)?", dependency)
                if match:
                    pending.append(match[1])
    missing = [name for name in seeds if name.split(":")[0] not in by_name]
    if missing:
        raise ValueError("missing build host packages: " + ", ".join(sorted(missing)))
    return selected


def host_environment():
    tools = {}
    for name in ("gcc", "g++", "ld", "as", "make", "bash", "perl", "python3",
                 "patch", "tar", "cmake", "ninja", "pkg-config"):
        binary = Path(shutil.which(name) or "missing").resolve(strict=True)
        tools[name] = {"path": str(binary), "sha256": digest(binary)}
    seeds = Path(__file__).with_name("build-host-packages.txt").read_text().split()
    inventory = capture("dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${Depends}\t${Pre-Depends}\n")
    return {"machine": capture("uname", "-m"),
            "os": Path("/etc/os-release").read_text(),
            "packages": package_versions(inventory, [*seeds, "libc6", "libstdc++6"]),
            "gcc": capture("gcc", "-dumpfullversion"),
            "libc": capture("ldd", "--version").splitlines()[0], "tools": tools,
            "environment": {key: os.environ[key] for key in (
                "CC", "CXX", "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS",
                "HOSTCC", "HOSTCXX", "SOURCE_DATE_EPOCH", "GIT_SRC_CHECKOUT_DIR",
                "GIT_TREE_OVERRIDE_DIR", "LD_LIBRARY_PATH", "LIBRARY_PATH", "CPATH",
                "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "PKG_CONFIG_PATH", "PYTHONPATH") if key in os.environ}}


def record(path):
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode):
        return {"type": "link", "target": os.readlink(path)}
    if stat.S_ISREG(mode):
        return {"type": "file", "sha256": digest(path), "mode": stat.S_IMODE(mode)}
    if stat.S_ISDIR(mode):
        return {"type": "directory", "mode": stat.S_IMODE(mode)}
    raise ValueError(f"unsupported cache file type: {path}")


def input_paths(tree, values):
    # Git's inventory includes applied changes and new non-ignored patches, but
    # excludes generated scripts/config binaries and object files after a build.
    paths = capture("git", "ls-files", "-z", "--cached", "--others", "--exclude-standard",
                    "--", "tools", "toolchain", "include", "scripts", "config",
                    "Makefile", "rules.mk", "Config.in", "target/linux/generic", cwd=tree).split("\0")
    paths = {p for p in paths if p and os.path.lexists(tree / p)}
    # Target Makefiles and every extra include discovered by the actual probe.
    for filename in shlex.split(values["MAKEFILE_LIST"]):
        path = Path(filename)
        if not path.is_absolute():
            path = tree / path
        try:
            relative = path.relative_to(tree).as_posix()
        except ValueError:
            if path.is_file():
                raise ValueError(f"external Makefile include requires dependency review: {path}")
            continue
        if relative == ".config" or relative.startswith(("staging_dir/", "tmp/")):
            continue
        # kernel.mk optionally includes this build output once the kernel has
        # been configured. It is absent at key calculation and is not a source
        # dependency of the installed tools/compiler. Exclude only the exact
        # internal kernel output; keep all other discovered includes covered.
        if (relative.startswith("build_dir/") and
                path == Path(values["LINUX_DIR"]) / ".config"):
            continue
        if path.is_file():
            paths.add(relative)
    for required in ("tools/Makefile", "toolchain/Makefile", "include/subdir.mk",
                     "scripts/timestamp.pl", "rules.mk"):
        if required not in paths:
            raise ValueError(f"missing cache dependency: {required}")
    return sorted(paths)


def fingerprint(tree, environment=None, values=None):
    tree = tree.resolve()
    values = probe(tree) if values is None else values
    cfg = config(tree / ".config")
    for symbol in ("CONFIG_EXTERNAL_TOOLCHAIN", "CONFIG_NATIVE_TOOLCHAIN"):
        if cfg.get(symbol) == "y":
            raise ValueError(f"installed cache does not support {symbol}")
    if cfg.get("CONFIG_EXTERNAL_KERNEL_TREE", "") not in ('""', ""):
        raise ValueError("external kernel tree cannot use installed cache")
    if cfg.get("CONFIG_KERNEL_GIT_CLONE_URI", "") not in ('""', ""):
        raise ValueError("custom kernel Git sources require cache dependency review")
    files, referenced, native = {}, set(), False
    for relative in input_paths(tree, values):
        path = tree / relative
        if path.is_symlink() and not path.resolve().is_relative_to(tree):
            raise ValueError(f"external source link requires dependency review: {relative}")
        files[relative] = record(path)
        if path.is_symlink():
            if not path.resolve().is_file():
                raise ValueError(f"source directory link requires dependency review: {relative}")
            files[relative]["resolved_sha256"] = digest(path.resolve())
        if path.is_file():
            text = path.read_bytes().decode("utf-8", errors="replace")
            native |= bool(re.search(r"-(?:march|mtune|mcpu)=native\b", text))
            referenced.update(CONFIG_SYMBOL.findall(text))
            if relative.startswith(("tools/", "toolchain/")) and re.search(r"CONFIG_[\w-]*\$[({]", text):
                raise ValueError(f"dynamic config dependency requires review: {relative}")
    # Read referenced CONFIG symbols rather than a hand-maintained whitelist.
    # Includes PACKAGE_kmod-b43, GDB/NASM, CPU/ABI and hardening dependencies.
    selected = {key: cfg.get(key, "") for key in sorted(referenced) if not key.endswith("_")}
    effective = {key: value for key, value in values.items() if key != "MAKEFILE_LIST"}
    # Internal toolchains start with GNU_TARGET_NAME- in the pristine info.mk.
    # GCC's install then writes REAL_GNU_TARGET_NAME- (including the libc) to
    # that generated file. Both describe the same configured compiler; model
    # its final prefix while continuing to reject unexpected overrides.
    if values["REAL_GNU_TARGET_NAME"]:
        bootstrap_cross = values["GNU_TARGET_NAME"] + "-"
        installed_cross = values["REAL_GNU_TARGET_NAME"] + "-"
        if values["TARGET_CROSS"] not in (bootstrap_cross, installed_cross):
            raise ValueError("unexpected internal toolchain prefix requires dependency review")
        effective["TARGET_CROSS"] = installed_cross
    target = f"{values['BOARD']}-{values['SUBTARGET']}"
    if not re.fullmatch(r"[a-z0-9_-]+-[a-z0-9_-]+", target):
        raise ValueError(f"invalid cache target: {target}")
    inputs = {"schema": SCHEMA, "implementation": digest(Path(__file__)), "root": str(tree),
              "source_files": files, "config": selected, "effective": effective,
              "host": host_environment() if environment is None else environment}
    flags = " ".join([*selected.values(), *effective.values(), *inputs["host"].get("environment", {}).values()])
    if native or re.search(r"-(?:march|mtune|mcpu)=native\b", flags):
        cpu = Path("/proc/cpuinfo").read_text()
        inputs["host"]["native_cpu_features"] = sorted({line for line in cpu.splitlines()
            if line.startswith(("flags\t", "Features\t"))})
    key = f"wrt-v3-toolchain-{target}-{json_digest(inputs)}"
    return key, inputs


def roots(tree, inputs, allow_links=False):
    if (tree / "staging_dir").is_symlink():
        raise ValueError("cache staging parent must not be a symlink")
    result = []
    for name, prefix in (("STAGING_DIR_HOST", "host"), ("TOOLCHAIN_DIR", "toolchain-")):
        path = Path(inputs["effective"][name])
        if path.parent != tree / "staging_dir" or not path.name.startswith(prefix) or (path.is_symlink() and not allow_links):
            raise ValueError(f"unsafe installed cache root: {path}")
        if name == "STAGING_DIR_HOST" and path.name != "host":
            raise ValueError("hostpkg is not an installed tools cache")
        result.append(path)
    return result


class InstalledLinksError(ValueError):
    def __init__(self, issues):
        self.issues = issues
        super().__init__("invalid installed cache links: " + "; ".join(
            f"{item['path']} -> {item['target']}: {item['error']}" for item in issues))


def known_absent_manual(path, inputs):
    manual = Path(inputs["effective"]["STAGING_DIR_HOST"]) / "share/man/man3"
    target = manual / "bn_dump.3"
    return (path.parent == manual and path.name in LIBRESSL_ABSENT_MAN_ALIASES and
            os.readlink(path) == "bn_dump.3" and manual.resolve(strict=True) == manual and
            not os.path.lexists(target))


def snapshot(tree, inputs):
    entries, external, issues = {}, {}, []
    installed = roots(tree, inputs)
    for root in installed:
        if not root.is_dir():
            raise ValueError(f"missing installed cache directory: {root}")
        for path in [root, *sorted(root.rglob("*"))]:
            relative = path.relative_to(tree).as_posix()
            entries[relative] = record(path)
            if path.is_symlink():
                try:
                    try:
                        resolved = path.resolve(strict=True)
                    except FileNotFoundError:
                        if not known_absent_manual(path, inputs):
                            raise
                        entries[relative]["dangling"] = True
                        continue
                    if resolved.is_relative_to(tree):
                        if not any(resolved.is_relative_to(item) for item in installed):
                            source = inputs["source_files"].get(resolved.relative_to(tree).as_posix(), {})
                            if not resolved.is_file() or source.get("sha256") != digest(resolved):
                                raise ValueError("installed link depends on uncached build files")
                    elif resolved.is_file():
                        external[str(resolved)] = digest(resolved)
                    else:
                        raise ValueError("external directory link cannot be verified")
                except (OSError, RuntimeError, ValueError) as error:
                    issues.append({"path": relative, "target": os.readlink(path), "error": str(error)})
    if issues:
        raise InstalledLinksError(issues)
    return entries, external


def stamps(tree, inputs):
    values = inputs["effective"]
    paths = [Path(values[name]) for name in ("tools/stamp-compile", "toolchain/stamp-compile")]
    paths.append(Path(values["TOOLCHAIN_DIR"]) / "stamp/.gcc_final_installed")
    for path in paths:
        if not path.is_file() or path.is_symlink() or path.stat().st_mtime <= EPOCH:
            raise ValueError(f"missing or invalid completed-build stamp: {path}")
        if not any(path.is_relative_to(root) for root in roots(tree, inputs)):
            raise ValueError(f"stamp outside cache roots: {path}")
    return paths


def expected(reports):
    return json.loads((reports / INPUTS).read_text())


def check_inputs(tree, reports):
    expected_inputs = expected(reports)
    key, inputs = fingerprint(tree)
    if inputs != expected_inputs["inputs"] or key != expected_inputs["key"]:
        write(reports / "toolchain-inputs-changed.json", {"key": key, "inputs": inputs})
        raise ValueError("tools/toolchain inputs changed after cache key calculation")
    return key, inputs


def output(**values):
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
            for name, value in values.items():
                stream.write(f"{name}={value}\n")


def prepare(tree, reports):
    key, inputs = check_inputs(tree, reports)
    backup = tree / BOOTSTRAP
    if backup.exists():
        raise ValueError("bootstrap backup already exists; refusing to overwrite pristine state")
    backup.mkdir()
    for root in roots(tree, inputs):
        if root.exists():
            shutil.copytree(root, backup / root.name, symlinks=True)
    write(backup / "identity.json", {"key": key})


def cold_reset(tree, reports, inputs):
    backup = tree / BOOTSTRAP
    if json.loads((backup / "identity.json").read_text())["key"] != expected(reports)["key"]:
        raise ValueError("cannot restore pristine host prerequisites for this build")
    paths = roots(tree, inputs, allow_links=True)
    paths += [p for p in (tree / "staging_dir").glob("toolchain-*") if p not in paths]
    # All deletion targets are direct children of the verified staging directory.
    for path in paths:
        if path.parent != tree / "staging_dir":
            raise ValueError(f"unsafe cold-build cleanup path: {path}")
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
    manifest = tree / MANIFEST
    if manifest.is_dir() and not manifest.is_symlink():
        shutil.rmtree(manifest)
    else:
        manifest.unlink(missing_ok=True)
    for root in roots(tree, inputs):
        if (backup / root.name).is_dir():
            shutil.copytree(backup / root.name, root, symlinks=True)
    # Re-run the original key check against pristine prerequisites, not restored
    # info.mk. Actual source/config changes still fail this build contract.
    check_inputs(tree, reports)


def restore(tree, reports, hit, restore_failed=False):
    original = expected(reports)
    key, inputs = original["key"], original["inputs"]
    state = {"key": key, "exact_hit": hit, "validated": False}
    write(reports / STATE, state)
    if not hit and not restore_failed:
        check_inputs(tree, reports)
        output(effective_hit="false", bad_key="", bad_detected_at="")
        print("Installed tools/toolchain cache miss; normal source build")
        return
    original_times = {}
    try:
        if restore_failed:
            raise ValueError("cache service restore failed or left partial files")
        validate(tree, reports, key, inputs, original_times, state)
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
        for filename, times in original_times.items():
            os.utime(filename, ns=times, follow_symlinks=False)
        cold_reset(tree, reports, inputs)
        detected = datetime.now(timezone.utc).isoformat() if hit else ""
        state.update({"fallback": "cold", "reason": str(error), "bad_key": key if hit else "",
                      "bad_detected_at": detected})
        write(reports / STATE, state)
        output(effective_hit="false", bad_key=state["bad_key"], bad_detected_at=detected)
        print("::warning::Installed toolchain cache rejected; pristine host prerequisites restored for a cold build: " +
              str(error).replace("\n", " ").replace("\r", " "))
        return
    output(effective_hit="true", bad_key="", bad_detected_at="")


def validate(tree, reports, key, inputs, original_times, state):
    saved = json.loads((tree / MANIFEST).read_text())
    if not isinstance(saved, dict) or saved.get("key") != key or saved.get("schema") != SCHEMA:
        raise ValueError("installed cache manifest does not match the exact key")
    entries, external = snapshot(tree, inputs)
    if saved.get("files") != entries or saved.get("external_files") != external:
        raise ValueError("installed cache is incomplete, changed, or links to a different runner tool")
    completed = stamps(tree, inputs)
    check_inputs(tree, reports)
    # Only verified content is normalized. No stamp is created or refreshed.
    for folder in (tree / "tools", tree / "toolchain"):
        for path in [*sorted(folder.rglob("*"), reverse=True), folder]:
            original_times[path] = (path.lstat().st_atime_ns, path.lstat().st_mtime_ns)
            os.utime(path, (EPOCH, EPOCH), follow_symlinks=False)
    for stamp, source, dependencies in ((completed[0], "tools", []),
                                        (completed[1], "toolchain", [str(completed[2])])):
        subprocess.run([str(tree / "scripts/timestamp.pl"), "-n", str(stamp), source, *dependencies],
                       cwd=tree, check=True)
    state["validated"] = True
    state["files_verified"] = len(entries)
    write(reports / STATE, state)
    print(f"Verified {len(entries)} installed entries; original build stamps retained")


def seal(tree, reports):
    key, inputs = check_inputs(tree, reports)
    stamps(tree, inputs)
    try:
        entries, external = snapshot(tree, inputs)
    except InstalledLinksError as error:
        write(reports / "toolchain-links-rejected.json", {"key": key, "links": error.issues})
        raise
    write(tree / MANIFEST, {"schema": SCHEMA, "key": key, "files": entries, "external_files": external})
    print(f"Sealed {len(entries)} installed entries for {key}")


def report(tree, reports):
    if not (reports / STATE).is_file():
        return
    state = json.loads((reports / STATE).read_text())
    compiled, intervals, begins = set(), [], {}
    logs = [p for p in (reports / "parallel.tsv", reports / "serial.tsv") if p.is_file()]
    for log in logs:
        for line in log.read_text().splitlines():
            parts = line.split("\t")
            if len(parts) != 4:
                continue
            when, event, phase, target = parts
            if not target.startswith(("tools/", "toolchain/")):
                continue
            marker = (log.name, phase, target)
            if event == "begin":
                begins[marker] = float(when)
                if phase == "compile":
                    compiled.add(target)
            elif event == "end" and marker in begins:
                intervals.append((begins.pop(marker), float(when)))
    merged = []
    for begin, end in sorted(intervals):
        if merged and begin <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([begin, end])
    state.update({"compiled": sorted(compiled), "activity_seconds": sum(b-a for a, b in merged),
                  "time_logs": [p.name for p in logs], "unfinished_events": len(begins)})
    if state["exact_hit"] and state["validated"] and (compiled or not logs):
        print("::warning::Exact tools/toolchain cache restored, but " +
              ("compile events were recorded; see toolchain-cache.json" if compiled else "build timing evidence is missing"))
    write(reports / STATE, state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "restore", "seal", "report"))
    parser.add_argument("--tree", type=Path, default=Path.cwd())
    parser.add_argument("--reports", type=Path)
    args = parser.parse_args()
    tree = args.tree.resolve()
    reports = args.reports or Path(os.environ["GITHUB_WORKSPACE"]) / "build-reports"
    if args.command == "prepare":
        prepare(tree, reports)
    elif args.command == "restore":
        restore(tree, reports, os.environ.get("TOOLCHAIN_CACHE_HIT") == "true",
                os.environ.get("TOOLCHAIN_RESTORE_OUTCOME") == "failure")
    elif args.command == "seal":
        seal(tree, reports)
    else:
        report(tree, reports)


if __name__ == "__main__":
    main()
