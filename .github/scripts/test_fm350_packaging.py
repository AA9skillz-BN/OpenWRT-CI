"""Reproduce the FM350 image mismatch with its real recipe, ELF and sstrip."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import urllib.request

REPO = Path(__file__).resolve().parents[2]


def module(name):
    spec = importlib.util.spec_from_file_location(
        name.replace("-", "_"), REPO / "Scripts/AP3000M/fm350" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


fix = module("fix-packaging")
pdp = module("fix-pdp-dns")


def download(url):
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def unpack(data, destination):
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive.getmembers():
            relative = Path(*Path(member.name).parts[1:])
            if not member.isfile():
                continue
            if ".." in relative.parts:
                raise ValueError("archive traversal")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())


class Fm350PackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lock = json.loads((REPO / "Profiles/packages.lock.json").read_bytes())["fm350"]
        if lock["url"] != "https://github.com/LianXia233/luci-app-fm350.git":
            raise AssertionError("review FM350 packaging for a changed source")
        cls.archive = download("https://codeload.github.com/LianXia233/luci-app-fm350/tar.gz/" + lock["commit"])
        with tarfile.open(fileobj=io.BytesIO(cls.archive)) as archive:
            recipe = next(m for m in archive.getmembers() if m.name.count("/") == 1 and m.name.endswith("/Makefile"))
            cls.recipe = archive.extractfile(recipe).read()
        if hashlib.sha256(cls.recipe).hexdigest() != fix.UPSTREAM_SHA256:
            raise AssertionError("locked FM350 recipe hash mismatch")

    def test_recipe_patch_is_exact_repeatable_and_rejects_drift(self):
        patched = fix.patch_makefile(self.recipe)
        self.assertEqual(fix.patch_makefile(patched), patched)
        for data in (self.recipe + b"\n", patched + b"\n",
                     patched.replace(b"$(RSTRIP) $(RUST_OUT)", b"true")):
            with self.assertRaisesRegex(ValueError, "reviewed locked source"):
                fix.patch_makefile(data)

    @unittest.skipIf(os.name == "nt", "actual OpenWrt strip/ELF regression requires Linux")
    def test_real_recipe_compile_install_and_repeated_package_strip(self):
        cargo = shutil.which("cargo")
        self.assertIsNotNone(cargo)
        self.assertIsNotNone(shutil.which("patchelf"))
        source = json.loads((REPO / "Profiles/sources.lock.json").read_bytes())["AP3000M"]
        raw = "https://raw.githubusercontent.com/immortalwrt/immortalwrt/" + source["commit"] + "/"
        rules = download(raw + "rules.mk").decode()
        # Use the upstream strip macro, rather than reproducing its options.
        strip_rules = rules.split("# strip an entire directory\n", 1)[1].split("\nNINJA =", 1)[0]
        rstrip = download(raw + "scripts/rstrip.sh")
        sstrip = download("https://sources.openwrt.org/ELFkickers-3.2.tar.gz")
        self.assertEqual(hashlib.sha256(sstrip).hexdigest(),
                         "9b81e6c53e0c94fc198d9882eb737156f36d565152dc32118897c77b06a2687c")
        with tempfile.TemporaryDirectory() as directory:
            top = Path(directory)
            tree = top / "package/luci-app-fm350"
            unpack(self.archive, tree)
            pdp_file = tree / pdp.SOURCE
            pdp_file.write_bytes(pdp.patch_pdp(pdp_file.read_bytes()))
            tools = top / "elfkickers"
            unpack(sstrip, tools)
            build = subprocess.run(["make", "-C", str(tools), "bin/sstrip"],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            (top / "host/bin").mkdir(parents=True)
            shutil.copy2(tools / "bin/sstrip", top / "host/bin/sstrip")
            (top / "host/bin/patchelf").symlink_to(shutil.which("patchelf"))
            (top / "scripts").mkdir()
            (top / "scripts/rstrip.sh").write_bytes(rstrip)
            (top / "scripts/rstrip.sh").chmod(0o755)
            (top / "include").mkdir()
            (top / "include/package.mk").touch()
            (top / "rules.mk").write_text('''
INCLUDE_DIR:=$(TOPDIR)/include
SCRIPT_DIR:=$(TOPDIR)/scripts
STAGING_DIR_HOST:=$(TOPDIR)/host
PKG_BUILD_DIR=$(TOPDIR)/build/$(PKG_NAME)
ARCH:=aarch64
CP:=cp -fpR
INSTALL_DIR:=install -d
INSTALL_DATA:=install -m0644
INSTALL_CONF:=install -m0600
INSTALL_BIN:=install -m0755
CONFIG_USE_SSTRIP:=y
CONFIG_SSTRIP_DISCARD_TRAILING_ZEROES:=y
qstrip=$(subst ",,$(1))
''' + strip_rules)
            # The real recipe still invokes Cargo; select the runner's musl
            # linker instead of requiring a fully built OpenWrt GCC toolchain.
            sysroot = subprocess.check_output(["rustc", "--print", "sysroot"], text=True).strip()
            host = next(s.removeprefix("host: ") for s in subprocess.check_output(
                ["rustc", "-vV"], text=True).splitlines() if s.startswith("host: "))
            linker = Path(sysroot) / "lib/rustlib" / host / "bin/rust-lld"
            self.assertTrue(linker.is_file())
            wrapper = top / "cargo-with-test-linker"
            wrapper.write_text("#!/bin/sh\nexport CARGO_TARGET_AARCH64_UNKNOWN_LINUX_MUSL_LINKER=" +
                shlex.quote(str(linker)) + "\nexec " + shlex.quote(cargo) + ' "$@" --locked\n')
            wrapper.chmod(0o755)
            (tree / "probe.mk").write_text('''
include Makefile
.PHONY: compile install packaged
compile:
\t$(Build/Compile)
install:
\t$(call Package/$(PKG_NAME)/install,$(TOPDIR)/rootfs)
packaged:
\t$(RSTRIP) $(TOPDIR)/rootfs
''')
            command = ["make", "--no-print-directory", "-f", "probe.mk",
                       f"TOPDIR={top}", f"CARGO={wrapper}", "compile", "install", "packaged"]
            before = subprocess.run(command, cwd=tree, capture_output=True, text=True, timeout=300)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            binary = tree / "rust/target/aarch64-unknown-linux-musl/release/fm350d"
            installed = top / "rootfs/usr/sbin/fm350d"
            self.assertNotEqual(binary.read_bytes(), installed.read_bytes(),
                                "original recipe must reproduce the image gate mismatch")
            recipe = tree / "Makefile"
            recipe.write_bytes(fix.patch_makefile(recipe.read_bytes()))
            after = subprocess.run(command, cwd=tree, capture_output=True, text=True, timeout=180)
            self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
            self.assertEqual(binary.read_bytes(), installed.read_bytes(),
                             "compiled and packaged binary must remain byte-identical")
            self.assertEqual(installed.read_bytes()[:6], b"\x7fELF\x02\x01")
            self.assertEqual(int.from_bytes(installed.read_bytes()[18:20], "little"), 183)
            self.assertTrue(installed.stat().st_mode & 0o111)
            entry = json.loads((REPO / "Profiles/AP3000M.json").read_bytes())["required_files"][0]
            self.assertEqual(entry["path"], "/usr/sbin/fm350d")
            self.assertEqual(entry["build_source"], str(binary.relative_to(top)))
            self.assertEqual(entry["elf_machine"], 183)
            print("FM350 real AArch64 recipe: original mismatch reproduced; repaired RSTRIP is idempotent and byte-exact")


if __name__ == "__main__":
    unittest.main()
