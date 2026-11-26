#!/usr/bin/env bash
set -euo pipefail
python3 "$(dirname -- "${BASH_SOURCE[0]}")/patch-layout.py"
git diff --check
git diff > tr3000-512.patch
git rev-parse HEAD > source-commit.txt
