"""Inspect the DTB and squashfs carried by the actual sysupgrade archive."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def check_dtb(dtb, data):
    def prop(node, name, kind="s"):
        return output("fdtget", "-t", kind, str(dtb), node, name).split()

    compatible = prop("/", "compatible")
    if data["supported_device"] not in compatible:
        raise ValueError(f"kernel DTB has wrong compatible: {compatible}")
    if data.get("root_partition"):
        args = prop("/chosen", "bootargs")
        if "root=PARTLABEL=" + data["root_partition"] not in args:
            raise ValueError("kernel DTB bootargs select the wrong root partition")
    if data.get("ubi_reg"):
        pending, partitions = ["/"], []
        while pending:
            node = pending.pop()
            children = output("fdtget", "-l", str(dtb), node).split()
            for child in children:
                path = node.rstrip("/") + "/" + child
                pending.append(path)
                if child.startswith("partition@") and prop(path, "label") == ["ubi"]:
                    partitions.append([int(cell, 16) for cell in prop(path, "reg", "x")])
        if partitions != [data["ubi_reg"]]:
            raise ValueError(f"kernel DTB has wrong UBI layout: {partitions}")
    return compatible


def verify_payload(archive, kernel, root, data, repo, reports, tree=None):
    with tempfile.TemporaryDirectory(prefix="verify-image-") as directory:
        temp = Path(directory)
        kernel_file, root_file = temp / "kernel.itb", temp / "root.squashfs"
        for member, dest in ((kernel, kernel_file), (root, root_file)):
            with archive.extractfile(member) as source, dest.open("wb") as stream:
                shutil.copyfileobj(source, stream)
        # Read the default FIT configuration, rather than guessing DTB index 1.
        config = output("fdtget", "-t", "s", str(kernel_file), "/configurations", "default")
        fdt = output("fdtget", "-t", "s", str(kernel_file), "/configurations/" + config, "fdt")
        images = output("fdtget", "-l", str(kernel_file), "/images").split()
        if fdt not in images:
            raise ValueError("default FIT configuration has no matching DTB")
        dtb = temp / "device.dtb"
        subprocess.run(["dumpimage", "-T", "flat_dt", "-p", str(images.index(fdt)),
                        "-o", str(dtb), str(kernel_file)], check=True)
        compatible = check_dtb(dtb, data)
        paths = [item["path"].lstrip("/") for item in data["required_files"]]
        paths.append(data["build_tag_file"].lstrip("/"))
        extracted = temp / "root"
        subprocess.run(["unsquashfs", "-no-progress", "-d", str(extracted), str(root_file), *paths], check=True)
        for item in data["required_files"]:
            file = extracted / item["path"].lstrip("/")
            if not file.is_file() or file.stat().st_size == 0:
                raise ValueError(f"rootfs missing required file: {item['path']}")
            if not file.resolve().is_relative_to(extracted.resolve()):
                raise ValueError("required file resolves outside rootfs")
            if item.get("executable") and not file.stat().st_mode & 0o111:
                raise ValueError(f"rootfs file is not executable: {item['path']}")
            if item.get("source") and file.read_bytes() != (repo / item["source"]).read_bytes():
                raise ValueError(f"rootfs file differs from build source: {item['path']}")
            if item.get("build_source"):
                if tree is None or file.read_bytes() != (tree / item["build_source"]).read_bytes():
                    raise ValueError(f"rootfs file differs from adapted source: {item['path']}")
        tag = extracted / data["build_tag_file"].lstrip("/")
        if not tag.is_file() or tag.read_text().strip() != os.environ["WRT_RELEASE_TAG"]:
            raise ValueError("rootfs build tag differs from this build")
        report = {"dtb_compatible": compatible, "ubi_reg": data.get("ubi_reg"),
                  "root_partition": data.get("root_partition"), "verified_files": paths}
        (reports / "verified-payload.json").write_text(json.dumps(report, indent=2) + "\n")
