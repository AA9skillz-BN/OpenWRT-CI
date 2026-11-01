"""Adapt the locked fan view and give repaired bytes a distinct LuCI URL."""
import hashlib
from pathlib import Path
import sys

UPSTREAM_SHA256 = "4f7b69a80afb5a2b53e61ede686c026d83e6715f8a60d4c081648282b137487d"
VIEW = "luci-app-airpi-fancontrol/htdocs/luci-static/resources/view/airpi-fancontrol/fancontrol.js"
MENU = "luci-app-airpi-fancontrol/files/usr/share/luci/menu.d/luci-app-airpi-fancontrol.json"
MENU_SHA256 = "7da0abd2352126f39b794ad49c08023fd854238ba80a9fe4872252741e73f2e9"
VIEW_NAME = b"airpi-fancontrol/fancontrol"

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


def patch_package(root):
    root = Path(root)
    view, menu = root / VIEW, root / MENU
    data = patch_view(view.read_bytes())
    digest = hashlib.sha256(data).hexdigest()[:12]
    view_name = VIEW_NAME.decode("ascii") + "-" + digest
    alias = view.with_name("fancontrol-" + digest + ".js")
    menu_data = patch_menu(menu.read_bytes(), view_name)
    # Validate all inputs before changing any file. A repeated hook may reuse
    # only the exact reviewed alias, never silently overwrite different bytes.
    if alias.is_symlink() or (alias.exists() and alias.read_bytes() != data):
        raise ValueError("versioned fan view differs from the repaired source")
    view.write_bytes(data)
    alias.write_bytes(data)
    menu.write_bytes(menu_data)
    return view_name


if __name__ == "__main__":
    name = patch_package(sys.argv[1])
    print("AP3000M fan view: LuCI APIs adapted; menu uses " + name)
