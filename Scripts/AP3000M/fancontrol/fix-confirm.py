"""Adapt the locked fan view to LuCI's modal and file.exec APIs."""
import hashlib
from pathlib import Path
import sys

UPSTREAM_SHA256 = "4f7b69a80afb5a2b53e61ede686c026d83e6715f8a60d4c081648282b137487d"
VIEW = "luci-app-airpi-fancontrol/htdocs/luci-static/resources/view/airpi-fancontrol/fancontrol.js"

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


if __name__ == "__main__":
    path = Path(sys.argv[1]) / VIEW
    path.write_bytes(patch_view(path.read_bytes()))
    print("AP3000M fan view: LuCI confirmation and execution status adapted")
