'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../../Scripts/C8-688/luci-app-c8-688-status/htdocs/luci-static/resources/view/c8-688-status/overview.js'), 'utf8');
let refresh, response;
function E(tag, attrs, children) {
	return { tag, attrs: attrs || {}, children, disabled: !!(attrs || {}).disabled,
		addEventListener: function(name, callback) { this[name] = callback; } };
}
const api = vm.runInNewContext('(function() {\n' + source + '\n})()', {
	E, L: { url: s => '/' + s }, Date, Blob, URL, setTimeout,
	view: { extend: s => s },
	rpc: { declare: () => () => Promise.resolve(response) },
	poll: { add: (fn, seconds) => { assert.equal(seconds, 10); refresh = fn; } },
	dom: { content: (target, content) => { target.children = content; } }
});
const initial = {
	schema_version: 1, board: { model: '<script>untrusted</script>' }, warnings: [],
	boot: { second_slot_running: true },
	thermal: { cpu_millidegrees: null, fans: [{ pwm: 0, rpm: 0 }] }, usb: [],
	network: { devices: [{ name: 'lan1', present: true, carrier: 0, speed_mbps: null, interfaces: [], rx_errors: 0, tx_errors: null }], interfaces: [], ipv4_defaults: null, ipv6_defaults: [] }
};
const tree = api.render(initial);
const stamp = tree.children[2], button = tree.children[3], body = tree.children[5];
assert.equal(button.disabled, false);
const text = JSON.stringify(body);
assert(text.includes('0 / 255（0%）'));
assert(text.includes('0 RPM'));
assert(text.includes('未连接'));
assert(text.includes('未知'));
assert(text.includes('<script>untrusted</script>')); // Passed as text, no HTML interpolation.
assert(!source.includes('innerHTML'));
response = { error: 'board_status_unavailable' };
refresh().then(() => {
	assert.equal(button.disabled, true);
	assert(stamp.textContent.includes('上一次快照'));
	response = initial;
	return refresh();
}).then(() => {
	assert.equal(button.disabled, false);
	assert(stamp.textContent.includes('更新时间'));
	console.log('C8 status UI zero/unknown/stale behavior passed');
}).catch(error => { console.error(error); process.exitCode = 1; });
