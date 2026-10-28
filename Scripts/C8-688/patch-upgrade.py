"""Add C8's guarded eMMC handlers without replacing upstream platform code."""
from pathlib import Path
import re
import sys


def patch_platform(text):
    handlers = {"platform_do_upgrade": "c8_688_do_upgrade",
                "platform_check_image": "c8_688_check_image",
                "platform_copy_config": "c8_688_copy_config"}
    source = ". /lib/upgrade/c8-688.sh\n"
    if source in text:
        if all(text.count(f'\t\t{handler} "$1"') == 1 for handler in handlers.values()):
            return text
        raise ValueError("incomplete existing C8 upgrade adaptation")
    if "nradio,c8-688" in text:
        raise ValueError("upstream now contains C8 upgrade logic; review before adapting")
    for function, handler in handlers.items():
        pattern = rf"(?ms)^{function}\(\) \{{\n.*?^}}"
        match = re.search(pattern, text)
        if not match:
            raise ValueError(f"upstream upgrade function missing: {function}")
        block = match.group()
        case = re.search(r'(?m)^\tcase (?:"\$board"|"\$\(board_name\)") in\n', block)
        if not case:
            raise ValueError(f"upstream board dispatch changed: {function}")
        added = ('\tnradio,c8-688)\n' + f'\t\t{handler} "$1"\n' + '\t\treturn $?\n\t\t;;\n')
        block = block[:case.end()] + added + block[case.end():]
        text = text[:match.start()] + block + text[match.end():]
    return source + text


if __name__ == "__main__":
    path = Path(sys.argv[1])
    path.write_text(patch_platform(path.read_text()), newline="\n")
