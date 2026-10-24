#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
from pathlib import Path
import re

dts = Path('target/linux/mediatek/dts/mt7981b-cudy-tr3000-v1.dts')
text = dts.read_text()
old = 'reg = <0x5c0000 0x4000000>;'
assert text.count(old) == 1, 'Unexpected upstream UBI layout; review patch'
text = text.replace(old, 'reg = <0x5c0000 0x1da40000>;')
text = text.replace('model = "Cudy TR3000 v1";',
                    'model = "Cudy TR3000 512MB v1";')
dts.write_bytes(text.encode())

image = Path('target/linux/mediatek/image/filogic.mk')
text = image.read_text()
pattern = r'(?ms)^define Device/cudy_tr3000-v1\n.*?^endef$'
match = re.search(pattern, text)
assert match, 'Device definition missing'
block = match.group()
assert 'IMAGE_SIZE := 65536k' in block, 'Unexpected image size'
assert 'BLOCKSIZE := 128k' in block
assert 'PAGESIZE := 2048' in block
block = block.replace('IMAGE_SIZE := 65536k', 'IMAGE_SIZE := 485632k')
text = text[:match.start()] + block + text[match.end():]
image.write_bytes(text.encode())
PY
git diff --check
git diff > tr3000-512.patch
git rev-parse HEAD > source-commit.txt
