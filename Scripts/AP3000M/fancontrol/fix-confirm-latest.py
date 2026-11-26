"""Validate the upstream fixed fan UI and retain only the strict exec-result adaptation."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[3]
VIEW = "luci-app-airpi-fancontrol/htdocs/luci-static/resources/view/airpi-fancontrol/fancontrol.js"
MENU = "luci-app-airpi-fancontrol/files/usr/share/luci/menu.d/luci-app-airpi-fancontrol.json"
MENU_ENTRY = "admin/status/airpi-fancontrol"
RECIPE = "luci-app-airpi-fancontrol/Makefile"
CONFIG = "luci-app-airpi-fancontrol/files/etc/config/airpi-fan"
CONFIG_TARGETS = (
    ("temp_high", "75"),
    ("temp_mid", "68"),
    ("temp_low", "48"),
    ("hysteresis", "4"),
    ("min_hold", "60"),
    ("log_level", "1"),
)
VIEW_NAME = "airpi-fancontrol/fancontrol"
INSTALL_VIEW = '''\t$(INSTALL_DATA) ./htdocs/luci-static/resources/view/airpi-fancontrol/fancontrol.js \\
\t\t$(1)/www/luci-static/resources/view/airpi-fancontrol/fancontrol.js
'''

# Upstream 7.0.5 added confirmAction() and uci.apply(), but num(res.code, ...)
# falls back to res.rc or 0 when code is absent, treating malformed replies
# without a numeric code===0 as successful hardware writes.
RC_FALLBACK_PATTERN = re.compile(
    r"\t\t\t\tvar kv = parseKV\(res(?:\s*&&\s*res)?\.stdout\);\n"
    r"(?:\t\t\t\t/\*[\s\S]*?\*/\n)?"
    r"\t\t\t\tvar rc = num\(res && res\.code, num\(res && res\.rc, 0\)\);\n"
    r"\t\t\t\tif \(rc !== 0 \|\| kv\.result === 'fail'\)\n"
    r"\t\t\t\t\tthrow new Error\(kv\.error \|\| \((?:res && )?res\.stderr \|\| ''\)\.trim\(\) \|\| _\('([^']+)'\)\);"
)
STRICT_EXEC_TEMPLATE = (
    "\t\t\t\tvar kv = parseKV(res && res.stdout);\n"
    "\t\t\t\tif (!res || res.code !== 0 || kv.result === 'fail')\n"
    "\t\t\t\t\tthrow new Error(kv.error || (res && res.stderr || '').trim() || _('\\1'));"
)


def strip_comments(js_text):
    return re.sub(r"/\*[\s\S]*?\*/|//[^\n]*", "", js_text)


def verify_view_behavior(data):
    node = shutil.which("node")
    harness = REPO / ".github/scripts/fancontrol-ui-test.js"
    if not node or not harness.is_file():
        return
    with tempfile.TemporaryDirectory() as temp:
        candidate = Path(temp) / "fancontrol.js"
        candidate.write_bytes(data)
        result = subprocess.run([node, str(harness), str(candidate)],
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
    if result.returncode != 0:
        raise ValueError("adapted fan view failed behavioral UI check: " +
                         (result.stderr or result.stdout).strip())


def patch_view(data):
    text = data.decode("utf-8")
    if RC_FALLBACK_PATTERN.search(text):
        matches = list(RC_FALLBACK_PATTERN.finditer(text))
        if len(matches) != 1:
            raise ValueError("fan view exec status anchor is ambiguous")
        text = RC_FALLBACK_PATTERN.sub(STRICT_EXEC_TEMPLATE, text, count=1)

    updated_code = strip_comments(text)
    if "confirmAction(" not in updated_code or "ui.confirm(" in updated_code:
        raise ValueError("fan view confirmation contract missing")
    if "res.code !== 0" not in updated_code or "res.rc" in updated_code:
        raise ValueError("fan view exec exit-code contract missing")
    encoded = text.encode("utf-8")
    verify_view_behavior(encoded)
    return encoded


def patch_menu(data, view_name):
    doc = json.loads(data.decode("utf-8"))
    entry = doc.get(MENU_ENTRY)
    if not isinstance(entry, dict) or entry.get("action", {}).get("type") != "view":
        raise ValueError("fan menu structure differs from expected LuCI view entry")
    current_path = entry["action"].get("path")
    if current_path not in (VIEW_NAME, view_name):
        raise ValueError("fan menu view anchor changed")
    old = f'"path": "{current_path}"'.encode("ascii")
    new = f'"path": "{view_name}"'.encode("ascii")
    if data.count(old) != 1:
        raise ValueError("fan menu path token count changed")
    return data.replace(old, new, 1)


def patch_recipe(data, alias_name):
    text = data.decode("utf-8")
    if "PKG_NAME:=luci-app-airpi-fancontrol" not in text:
        raise ValueError("fan recipe package identity changed")
    extra_pattern = re.compile(
        r"\t\$\(INSTALL_DATA\) \./htdocs/luci-static/resources/view/airpi-fancontrol/fancontrol-[0-9a-f]{12}\.js \\\n"
        r"\t\t\$\(1\)/www/luci-static/resources/view/airpi-fancontrol/fancontrol-[0-9a-f]{12}\.js\n"
    )
    extra = ("\t$(INSTALL_DATA) ./htdocs/luci-static/resources/view/airpi-fancontrol/" + alias_name + " \\\n"
             "\t\t$(1)/www/luci-static/resources/view/airpi-fancontrol/" + alias_name + "\n")
    existing = extra_pattern.findall(text)
    if existing:
        if existing != [extra]:
            raise ValueError("fan recipe already installs a different versioned view")
        base = text.replace(INSTALL_VIEW + extra, INSTALL_VIEW, 1)
    else:
        base = text
    if base.count(INSTALL_VIEW) != 1:
        raise ValueError("fan recipe install anchor changed")
    return base.replace(INSTALL_VIEW, INSTALL_VIEW + extra, 1).encode("utf-8")


def patch_config(data):
    text = data.decode("utf-8")
    if "config fan 'settings'" not in text:
        raise ValueError("fan config settings section missing")
    updated = text
    for key, target in CONFIG_TARGETS:
        pattern = re.compile(rf"^([ \t]*option[ \t]+{re.escape(key)}[ \t]+)'[^']*'[ \t]*$", re.MULTILINE)
        if len(pattern.findall(updated)) != 1:
            raise ValueError(f"fan config anchor missing or duplicated: {key}")
        updated = pattern.sub(rf"\g<1>'{target}'", updated, count=1)
    return updated.encode("utf-8")


def patch_package(root):
    root = Path(root)
    view, menu, recipe, config = root / VIEW, root / MENU, root / RECIPE, root / CONFIG
    data = patch_view(view.read_bytes())
    digest = hashlib.sha256(data).hexdigest()[:12]
    view_name = VIEW_NAME + "-" + digest
    alias = view.with_name("fancontrol-" + digest + ".js")
    menu_data = patch_menu(menu.read_bytes(), view_name)
    recipe_data = patch_recipe(recipe.read_bytes(), alias.name)
    config_data = patch_config(config.read_bytes())
    if alias.is_symlink() or (alias.exists() and alias.read_bytes() != data):
        raise ValueError("versioned fan view differs from the repaired source")
    view.write_bytes(data)
    alias.write_bytes(data)
    menu.write_bytes(menu_data)
    recipe.write_bytes(recipe_data)
    config.write_bytes(config_data)
    return view_name


if __name__ == "__main__":
    name = patch_package(sys.argv[1])
    print("AP3000M latest fan view: LuCI APIs adapted and UI-verified; menu uses " + name)
