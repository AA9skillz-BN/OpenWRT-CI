#!/usr/bin/env python3
"""Pre-flight canary for live upstream OpenWrt/ImmortalWrt sources and plugins."""
import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

REPO = Path(__file__).resolve().parents[2]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build = load_module("canary_device_build", REPO / ".github/scripts/device-build.py")
wifi_mod = load_module("canary_wifi_defaults", REPO / ".github/scripts/wifi-defaults.py")
ap_sys_mod = load_module("canary_ap_sys", REPO / "Scripts/AP3000M/system-defaults.py")
openclash_mod = load_module("canary_openclash", REPO / ".github/scripts/ap3000m-openclash-core.py")

SOURCE_TARGETS = (
    ("AP3000M / C8-688 主源码", "immortalwrt/immortalwrt", "master", "AP3000M"),
    ("TR3000-512 主源码", "VIKINGYFY/immortalwrt", "owrt", "TR3000-512"),
)

ADAPTED_PLUGINS = (
    ("openclash", ("AP3000M", "TR3000-512")),
    ("turboacc", ("AP3000M", "TR3000-512", "C8-688")),
    ("fancontrol", ("AP3000M",)),
    ("fm350", ("AP3000M",)),
    ("c8-fancontrol", ("C8-688",)),
    ("mt5700m", ("C8-688",)),
    ("c8-cellular-webui", ("C8-688",)),
)


def fetch_bytes(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "OpenWRT-CI-Canary"})
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        req.add_unredirected_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def resolve_branch_head(repo_slug, branch):
    url = f"https://github.com/{repo_slug}.git"
    out = build.run("git", "ls-remote", "--exit-code", "--refs", "--", url, f"refs/heads/{branch}")
    sha = out.split()[0]
    if len(sha) != 40:
        raise ValueError(f"failed to resolve {repo_slug}@{branch}")
    return sha


def unpack_github_tar(repo_url, sha, dest, subdirs=None):
    slug = repo_url.removeprefix("https://github.com/").removesuffix(".git")
    raw = fetch_bytes(f"https://codeload.github.com/{slug}/tar.gz/{sha}", timeout=90)
    with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            parts = Path(member.name).parts[1:]
            if not parts or ".." in parts:
                continue
            if subdirs:
                if parts[0] not in subdirs:
                    continue
                target = dest / "package" / Path(*parts)
            else:
                target = dest / Path(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(tf.extractfile(member).read())


def check_source_anchors(label, slug, branch, lock_key, sources_lock, head_sha=None):
    locked_sha = sources_lock[lock_key]["commit"]
    head_sha = head_sha or resolve_branch_head(slug, branch)
    raw_base = f"https://raw.githubusercontent.com/{slug}/{head_sha}/"
    mac_uc = fetch_bytes(raw_base + "package/network/config/wifi-scripts/files/lib/wifi/mac80211.uc").decode("utf-8")
    wifi_mod.patch_generator(
        mac_uc,
        "TestSSID",
        "12345678",
        band_suffix=(lock_key == "C8-688"),
        ap3000m_radio_defaults=(lock_key == "AP3000M"),
    )
    if slug == "immortalwrt/immortalwrt":
        cg = fetch_bytes(raw_base + "package/base-files/files/bin/config_generate").decode("utf-8")
        dhcp = fetch_bytes(raw_base + "package/network/services/dnsmasq/files/dhcp.conf").decode("utf-8")
        ap_sys_mod.patch_config_generate(cg)
        ap_sys_mod.patch_dhcp_conf(dhcp)
        net02 = fetch_bytes(raw_base + "target/linux/mediatek/filogic/base-files/etc/board.d/02_network").decode("utf-8")
        if "nradio,c8-668gl|" not in net02 or "\tnradio,c8-668gl)" not in net02:
            raise ValueError("C8-688 02_network anchor nradio,c8-668gl missing")
        with tempfile.TemporaryDirectory() as tmp:
            plat = Path(tmp) / "platform.sh"
            plat.write_bytes(fetch_bytes(raw_base + "target/linux/mediatek/filogic/base-files/lib/upgrade/platform.sh"))
            build.run_hook_check([sys.executable, str(REPO / "Scripts/C8-688/patch-upgrade.py"), str(plat)])
    else:
        dts = fetch_bytes(raw_base + "target/linux/mediatek/dts/mt7981b-cudy-tr3000-v1.dts").decode("utf-8")
        mk = fetch_bytes(raw_base + "target/linux/mediatek/image/filogic.mk").decode("utf-8")
        if "partition@5c0000" not in dts or "Device/cudy_tr3000-v1" not in mk:
            raise ValueError("TR3000-512 UBI layout anchors changed")
    return locked_sha, head_sha


def run_canary(repo=REPO):
    sources_lock = json.loads((repo / "Profiles/sources.lock.json").read_text(encoding="utf-8"))
    packages_lock = json.loads((repo / "Profiles/packages.lock.json").read_text(encoding="utf-8"))
    openclash_lock = json.loads((repo / "Profiles/openclash-core.lock.json").read_text(encoding="utf-8"))
    results = []

    for label, slug, branch, lock_key in SOURCE_TARGETS:
        head_short = "HEAD"
        try:
            head_sha = resolve_branch_head(slug, branch)
            head_short = head_sha[:12]
            locked_sha, _ = check_source_anchors(label, slug, branch, lock_key, sources_lock, head_sha=head_sha)
            results.append({"category": "source", "target": f"{label} ({slug}@{branch})",
                            "locked": locked_sha[:12], "latest": head_short, "ok": True, "detail": "锚点与默认生成契约正常"})
        except Exception as exc:
            results.append({"category": "source", "target": f"{label} ({slug}@{branch})",
                            "locked": sources_lock[lock_key]["commit"][:12], "latest": head_short,
                            "ok": False, "detail": str(exc)})

    staged_c8 = {}
    for key, profiles in ADAPTED_PLUGINS:
        item = packages_lock[key]
        locked_sha = item["commit"]
        head_short = "HEAD"
        try:
            resolved, meta = build.resolve_plugin(key, item, "latest")
            head_sha = resolved["commit"]
            head_short = head_sha[:12]
            with tempfile.TemporaryDirectory() as tmp:
                probe_tree = Path(tmp)
                subdirs = item.get("subdirectories", [item["subdirectory"]] if item.get("subdirectory") else None)
                if subdirs:
                    unpack_github_tar(item["url"], head_sha, probe_tree, subdirs=subdirs)
                else:
                    unpack_github_tar(item["url"], head_sha, probe_tree / "package" / item["directory"])
                for prof in profiles:
                    with tempfile.TemporaryDirectory() as prof_tmp:
                        prof_tree = Path(prof_tmp)
                        shutil.copytree(probe_tree / "package", prof_tree / "package")
                        build.default_plugin_validator(repo, prof, key, prof_tree, "latest")
                if key in ("mt5700m", "c8-cellular-webui"):
                    staged_c8[key] = (head_sha, {
                        rel: probe_tree / "package" / rel
                        for rel in (subdirs or [item["directory"]])
                    })
                    # Persist copy for coupled check below
                    persist = Path(tempfile.mkdtemp(prefix=f"canary-{key}-"))
                    for rel, src_dir in staged_c8[key][1].items():
                        shutil.copytree(src_dir, persist / "package" / rel)
                    staged_c8[key] = (head_sha, persist)
            results.append({"category": "plugin", "target": f"{key} ({','.join(profiles)})",
                            "locked": locked_sha[:12], "latest": head_sha[:12],
                            "ok": True, "detail": f"分支 {meta['branch']} 适配校验通过"})
        except Exception as exc:
            results.append({"category": "plugin", "target": f"{key} ({','.join(profiles)})",
                            "locked": locked_sha[:12], "latest": head_short,
                            "ok": False, "detail": f"auto 模式将自动回退锁版本；原因: {exc}"})

    if {"mt5700m", "c8-cellular-webui"} <= set(staged_c8):
        try:
            with tempfile.TemporaryDirectory() as coupled_dir:
                coupled = Path(coupled_dir)
                for _, (_, src_root) in staged_c8.items():
                    shutil.copytree(src_root / "package", coupled / "package", dirs_exist_ok=True)
                build.run_hook_check([sys.executable, str(repo / "Scripts/C8-688/cellular/adapt.py"),
                                      str(coupled / "package/luci-app-mt5700m"),
                                      str(coupled / "package/at-webserver")])
        except Exception as exc:
            results.append({"category": "plugin", "target": "c8-cellular-coupled (C8-688)",
                            "locked": packages_lock["mt5700m"]["commit"][:12], "latest": "HEAD",
                            "ok": False, "detail": f"auto 模式将自动回退锁版本；原因: {exc}"})
    for _, (_, persist_dir) in staged_c8.items():
        shutil.rmtree(persist_dir, ignore_errors=True)

    try:
        mihomo = openclash_mod.resolve(openclash_lock, "latest")
        results.append({"category": "binary", "target": "MetaCubeX/mihomo (ARM64 core)",
                        "locked": openclash_lock["tag"], "latest": mihomo["tag"],
                        "ok": True, "detail": f"SHA-256 摘要已就绪 ({mihomo['asset']})"})
    except Exception as exc:
        results.append({"category": "binary", "target": "MetaCubeX/mihomo (ARM64 core)",
                        "locked": openclash_lock["tag"], "latest": "latest",
                        "ok": False, "detail": f"auto 模式将自动回退锁版本；原因: {exc}"})

    return {"schema": 1, "all_ok": all(r["ok"] for r in results), "results": results}


def format_markdown(report):
    status_headline = "✅ 全部上游源码与插件当前均兼容" if report["all_ok"] else "⚠️ 检测到上游变更需跟进适配（`PLUGIN_MODE=auto` 构建不受阻断，会自动回退对应插件锁）"
    lines = [
        "## 上游源码与插件适配巡检报告 (Upstream Canary)",
        "",
        status_headline,
        "",
        "| 类别 | 检查目标 | 当前锁版本 | 上游最新 | 状态 | 说明 |",
        "| :--- | :--- | :--- | :--- | :---: | :--- |",
    ]
    for item in report["results"]:
        badge = "✅ 通过" if item["ok"] else "⚠️ 漂移"
        detail = item["detail"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{item['category']}` | {item['target']} | `{item['locked']}` | `{item['latest']}` | {badge} | {detail} |")
    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--markdown", type=Path, default=None)
    parser.add_argument("--fail-on-drift", action="store_true")
    args = parser.parse_args()
    report = run_canary(REPO)
    md = format_markdown(report)
    print(md)
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(md, encoding="utf-8")
    if args.summary:
        with args.summary.open("a", encoding="utf-8") as stream:
            stream.write(md)
    if args.fail_on_drift and not report["all_ok"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
