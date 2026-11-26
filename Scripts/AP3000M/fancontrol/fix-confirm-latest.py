"""Adapt the reviewed latest fan view (v7.0.5) and version its LuCI URL."""
import hashlib
from pathlib import Path
import sys

UPSTREAM_SHA256 = "44e35ee5ba5aaa1428f3ff5f1ae399ad4a642b6a45d91ec57c77f2de1c78e57c"
VIEW = "luci-app-airpi-fancontrol/htdocs/luci-static/resources/view/airpi-fancontrol/fancontrol.js"
MENU = "luci-app-airpi-fancontrol/files/usr/share/luci/menu.d/luci-app-airpi-fancontrol.json"
MENU_SHA256 = "7da0abd2352126f39b794ad49c08023fd854238ba80a9fe4872252741e73f2e9"
RECIPE = "luci-app-airpi-fancontrol/Makefile"
RECIPE_SHA256 = "52164cfec267de6a5c5fb9ffdff2ffb0cf2ec9890a073ca97d8bcf83d3d41e60"
RECIPE_HASHES = {RECIPE_SHA256}
CONFIG = "luci-app-airpi-fancontrol/files/etc/config/airpi-fan"
CONFIG_SHA256 = "5115e2d358c5bf188d2fa2a19f5867ab0bce80f4554d528f98fd85fe0a38864b"
CONFIG_EDITS = (
    ("\toption temp_mid '60'\n", "\toption temp_mid '68'\n"),
    ("\toption hysteresis '2'\n", "\toption hysteresis '4'\n"),
    ("\toption min_hold '30'\n", "\toption min_hold '60'\n"),
    ("\toption log_level '2'\n", "\toption log_level '1'\n"),
)
VIEW_NAME = b"airpi-fancontrol/fancontrol"
INSTALL_VIEW = '''\t$(INSTALL_DATA) ./htdocs/luci-static/resources/view/airpi-fancontrol/fancontrol.js \\
\t\t$(1)/www/luci-static/resources/view/airpi-fancontrol/fancontrol.js
'''

# Upstream 7.0.5 added confirmAction() and uci.apply(), but num(res.code, ...)
# falls back to res.rc or 0 when code is absent, treating malformed replies
# without a numeric code===0 as successful hardware writes.
BEFORE_EXEC = (
    "\t\t\t\tvar kv = parseKV(res.stdout);\n"
    "\t\t\t\t/* fs.exec 返回 { code, stdout, stderr }，没有 rc 字段。\n"
    "\t\t\t\t * 旧代码判断 res.rc !== 0 恒为真，成功下发的命令也会被当成失败。 */\n"
    "\t\t\t\tvar rc = num(res && res.code, num(res && res.rc, 0));\n"
    "\t\t\t\tif (rc !== 0 || kv.result === 'fail')\n"
    "\t\t\t\t\tthrow new Error(kv.error || (res.stderr || '').trim() || _('设备返回失败'));"
)
AFTER_EXEC = (
    "\t\t\t\tvar kv = parseKV(res && res.stdout);\n"
    "\t\t\t\tif (!res || res.code !== 0 || kv.result === 'fail')\n"
    "\t\t\t\t\tthrow new Error(kv.error || (res && res.stderr || '').trim() || _('设备返回失败'));"
)


def patch_view(data):
    text = data.decode("utf-8")
    original = text
    if AFTER_EXEC in text:
        original = text.replace(AFTER_EXEC, BEFORE_EXEC, 1)
    if hashlib.sha256(original.encode("utf-8")).hexdigest() != UPSTREAM_SHA256:
        raise ValueError("fan view differs from the reviewed latest source")
    if original != text:
        return data
    if text.count(BEFORE_EXEC) != 1 or text.count("confirmAction(") != 4:
        raise ValueError("fan view API anchors changed")
    return text.replace(BEFORE_EXEC, AFTER_EXEC, 1).encode("utf-8")


def patch_menu(data, view_name):
    old = b'"path": "' + VIEW_NAME + b'"'
    new = b'"path": "' + view_name.encode("ascii") + b'"'
    original = data.replace(new, old)
    if hashlib.sha256(original).hexdigest() != MENU_SHA256:
        raise ValueError("fan menu differs from the reviewed latest source")
    if original.count(old) != 1:
        raise ValueError("fan menu view anchor changed")
    return original.replace(old, new)


def patch_recipe(data, alias_name):
    extra = ("\t$(INSTALL_DATA) ./htdocs/luci-static/resources/view/airpi-fancontrol/" + alias_name + " \\\n"
             "\t\t$(1)/www/luci-static/resources/view/airpi-fancontrol/" + alias_name + "\n")
    text = data.decode("utf-8")
    original = text.replace(INSTALL_VIEW + extra, INSTALL_VIEW)
    if hashlib.sha256(original.encode("utf-8")).hexdigest() not in RECIPE_HASHES:
        raise ValueError("fan recipe differs from the reviewed latest source")
    if original.count(INSTALL_VIEW) != 1:
        raise ValueError("fan recipe install anchor changed")
    return original.replace(INSTALL_VIEW, INSTALL_VIEW + extra).encode("utf-8")


def patch_config(data):
    text = data.decode("utf-8")
    original = text
    if all(new in text for _, new in CONFIG_EDITS):
        for old, new in CONFIG_EDITS:
            original = original.replace(new, old, 1)
    if hashlib.sha256(original.encode("utf-8")).hexdigest() != CONFIG_SHA256:
        raise ValueError("fan config differs from the reviewed latest source")
    if original != text:
        return data
    updated = original
    for old, new in CONFIG_EDITS:
        if updated.count(old) != 1:
            raise ValueError("fan config anchors changed")
        updated = updated.replace(old, new, 1)
    return updated.encode("utf-8")


def patch_package(root):
    root = Path(root)
    view, menu, recipe, config = root / VIEW, root / MENU, root / RECIPE, root / CONFIG
    data = patch_view(view.read_bytes())
    digest = hashlib.sha256(data).hexdigest()[:12]
    view_name = VIEW_NAME.decode("ascii") + "-" + digest
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
    print("AP3000M latest fan view: LuCI APIs adapted; menu uses " + name)
