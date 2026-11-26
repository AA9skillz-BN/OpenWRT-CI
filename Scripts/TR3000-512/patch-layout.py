from pathlib import Path
import re


def patch_layout(tree):
    dts = tree / Path('target/linux/mediatek/dts/mt7981b-cudy-tr3000-v1.dts')
    text = dts.read_text(encoding='utf-8')
    old = 'reg = <0x5c0000 0x4000000>;'
    if text.count(old) != 1:
        raise ValueError('Unexpected upstream UBI layout; review patch')
    text = text.replace(old, 'reg = <0x5c0000 0x1da40000>;')
    text = text.replace('model = "Cudy TR3000 v1";',
                        'model = "Cudy TR3000 512MB v1";')
    patched_dts = text

    image = tree / Path('target/linux/mediatek/image/filogic.mk')
    text = image.read_text(encoding='utf-8')
    pattern = r'(?ms)^define Device/cudy_tr3000-v1\n.*?^endef$'
    match = re.search(pattern, text)
    if not match:
        raise ValueError('TR3000-v1 device definition missing')
    block = match.group()
    for setting in ('IMAGE_SIZE := 65536k', 'BLOCKSIZE := 128k', 'PAGESIZE := 2048'):
        if block.count(setting) != 1:
            raise ValueError('Unexpected TR3000-v1 layout setting: ' + setting)
    block = block.replace('IMAGE_SIZE := 65536k', 'IMAGE_SIZE := 485632k')
    text = text[:match.start()] + block + text[match.end():]
    dts.write_text(patched_dts, encoding='utf-8', newline='\n')
    image.write_text(text, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    patch_layout(Path.cwd())
