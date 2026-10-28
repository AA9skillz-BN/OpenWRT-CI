"""Check every tracked shell script, including extensionless device helpers."""
import os
from pathlib import Path
import shutil
import subprocess

repo = Path(__file__).resolve().parents[2]
paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo).decode().split("\0")
count = 0
for name in filter(None, paths):
    file = repo / name
    if not file.is_file():
        continue
    first = file.read_bytes().split(b"\n", 1)[0]
    if not first.startswith(b"#!") or not (b"/sh" in first or b"bash" in first):
        continue
    interpreter = "bash" if b"bash" in first else "sh"
    executable = shutil.which(interpreter)
    if executable is None and os.name == "nt":
        candidate = Path("C:/Program Files/Git/bin/bash.exe")
        executable = str(candidate) if candidate.is_file() else None
    if executable is None:
        raise RuntimeError(f"missing shell: {interpreter}")
    subprocess.run([executable, "-n", str(file)], check=True)
    count += 1
print(f"Shell syntax checked: {count} scripts")
