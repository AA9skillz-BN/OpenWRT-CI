"""Adapt the locked fan view and give repaired bytes a distinct LuCI URL."""
import hashlib
from pathlib import Path
import sys

UPSTREAM_SHA256 = "4f7b69a80afb5a2b53e61ede686c026d83e6715f8a60d4c081648282b137487d"
VIEW = "luci-app-airpi-fancontrol/htdocs/luci-static/resources/view/airpi-fancontrol/fancontrol.js"
MENU = "luci-app-airpi-fancontrol/files/usr/share/luci/menu.d/luci-app-airpi-fancontrol.json"
MENU_SHA256 = "7da0abd2352126f39b794ad49c08023fd854238ba80a9fe4872252741e73f2e9"
RECIPE = "luci-app-airpi-fancontrol/Makefile"
RECIPE_SHA256 = "099091082301cca8cae61046a0b78ec76d595c8020b01dbcbba9415f2d4e1dfa"
# Reviewed 7.0.3 changes only the version and explanatory comments in this
# recipe. Its install macro is identical to the locked 7.0.2 implementation.
RECIPE_HASHES = {RECIPE_SHA256,
                 "2b81809c45d7e825e69a83064ca3a6a3f1fb0efbb0e7a09ffdb7c57042a49ffc"}
VIEW_NAME = b"airpi-fancontrol/fancontrol"
INSTALL_VIEW = '''\t$(INSTALL_DATA) ./htdocs/luci-static/resources/view/airpi-fancontrol/fancontrol.js \\
\t\t$(1)/www/luci-static/resources/view/airpi-fancontrol/fancontrol.js
'''

HELPER = """/* LuCI ui exposes showModal/hideModal, not a Promise-based confirm(). */
function confirmAction(title, children) {
	return new Promise(function(resolve) {
		function finish(accepted) {
			ui.hideModal();
			resolve(accepted);
		}
		ui.showModal(title, children.concat([
			E('div', { 'class': 'right' }, [
				E('button', {
					'class': 'cbi-button', 'type': 'button',
					'click': function() { finish(false); }
				}, _('取消')),
				' ',
				E('button', {
					'class': 'cbi-button cbi-button-apply', 'type': 'button',
					'click': function() { finish(true); }
				}, _('确认'))
			])
		]));
	});
}

"""


def patch_view(data):
    text = data.decode("utf-8")
    original = text
    if HELPER in text:
        # Accept a repeated hook only when reversing this exact adaptation
        # reproduces the locked upstream bytes. Partial/drifted patches fail.
        original = text.replace(HELPER, "", 1).replace("confirmAction(", "ui.confirm(")
        original = original.replace("res.code !== 0", "res.rc !== 0")
    if hashlib.sha256(original.encode("utf-8")).hexdigest() != UPSTREAM_SHA256:
        raise ValueError("fan view differs from the reviewed locked source")
    if original != text:
        return data
    if text.count("ui.confirm(") != 3 or text.count("res.rc !== 0") != 1:
        raise ValueError("fan view API anchors changed")
    text = text.replace("ui.confirm(", "confirmAction(")
    text = text.replace("res.rc !== 0", "res.code !== 0")
    return text.replace("function ctl(args) {", HELPER + "function ctl(args) {", 1).encode("utf-8")


def patch_menu(data, view_name):
    old = b'"path": "' + VIEW_NAME + b'"'
    new = b'"path": "' + view_name.encode("ascii") + b'"'
    original = data.replace(new, old)
    if hashlib.sha256(original).hexdigest() != MENU_SHA256:
        raise ValueError("fan menu differs from the reviewed locked source")
    if original.count(old) != 1:
        raise ValueError("fan menu view anchor changed")
    return original.replace(old, new)


def patch_recipe(data, alias_name):
    extra = ("\t$(INSTALL_DATA) ./htdocs/luci-static/resources/view/airpi-fancontrol/" + alias_name + " \\\n"
             "\t\t$(1)/www/luci-static/resources/view/airpi-fancontrol/" + alias_name + "\n")
    text = data.decode("utf-8")
    original = text.replace(INSTALL_VIEW + extra, INSTALL_VIEW)
    if hashlib.sha256(original.encode("utf-8")).hexdigest() not in RECIPE_HASHES:
        raise ValueError("fan recipe differs from the reviewed locked source")
    if original.count(INSTALL_VIEW) != 1:
        raise ValueError("fan recipe install anchor changed")
    return original.replace(INSTALL_VIEW, INSTALL_VIEW + extra).encode("utf-8")


def patch_package(root):
    root = Path(root)
    view, menu, recipe = root / VIEW, root / MENU, root / RECIPE
    data = patch_view(view.read_bytes())
    digest = hashlib.sha256(data).hexdigest()[:12]
    view_name = VIEW_NAME.decode("ascii") + "-" + digest
    alias = view.with_name("fancontrol-" + digest + ".js")
    menu_data = patch_menu(menu.read_bytes(), view_name)
    recipe_data = patch_recipe(recipe.read_bytes(), alias.name)
    # Validate all inputs before changing any file. A repeated hook may reuse
    # only the exact reviewed alias, never silently overwrite different bytes.
    if alias.is_symlink() or (alias.exists() and alias.read_bytes() != data):
        raise ValueError("versioned fan view differs from the repaired source")
    view.write_bytes(data)
    alias.write_bytes(data)
    menu.write_bytes(menu_data)
    recipe.write_bytes(recipe_data)
    return view_name


if __name__ == "__main__":
    name = patch_package(sys.argv[1])
    print("AP3000M fan view: LuCI APIs adapted; menu uses " + name)
