// Execute the real LuCI view with isolated DOM/RPC; never contact a router.
'use strict';
const fs = require('fs');
const assert = require('assert');
const path = require('path');
const source = fs.readFileSync(path.join(__dirname,
 '../../Scripts/C8-688/luci-app-c8-688-ota/htdocs/luci-static/resources/view/c8-688-ota/overview.js'), 'utf8');
const tag = 'C8-688-immortalwrt-master-fixture';

function fixture(ready = true) {
 const calls = [], nodes = [];
 let accept = false;
 function E(name, attrs, children) {
  const node = { name, attrs: attrs || {}, children: Array.isArray(children) ? children : [ children ],
   style: {}, value: '', handlers: {}, disabled: !!(attrs && attrs.disabled),
   appendChild(c) { this.children.push(c); },
   addEventListener(e, cb) { this.handlers[e] = cb; } };
  nodes.push(node);
  return node;
 }
 const status = { current: 'old', enabled: true, ota_ready: ready,
  boot: { ready, system: '1' }, images: [], keep_config: true,
  cached: { latest: tag, size: 4, sha256: 'a'.repeat(64), update: true } };
 const rpc = { declare: spec => (...args) => {
  calls.push([spec.method, args]);
  return Promise.resolve(spec.method === 'status' ? status : { started: true });
 } };
 const view = new Function('view', 'rpc', 'poll', 'E', 'confirm', source)(
  { extend: x => x }, rpc, { add() {} }, E, () => accept);
 view.render(status);
 const button = text => nodes.find(n => n.name === 'button' && n.children[0] === text);
 return { calls, button, confirm: v => { accept = v; } };
}

(async () => {
 const first = fixture();
 const original = first.button('切回第一系统（原厂）');
 assert.strictEqual(original.disabled, false);
 original.handlers.click();
 assert.deepStrictEqual(first.calls, []); // Cancel must not submit anything.
 first.confirm(true);
 original.handlers.click();
 await new Promise(resolve => setImmediate(resolve));
 assert.deepStrictEqual(first.calls, [['official', [true]]]);
 assert.strictEqual(original.disabled, true); // Submitted task excludes OTA.
 assert.strictEqual(first.button('升级（保留配置）').disabled, true);

 const ota = fixture();
 const upgrade = ota.button('升级（保留配置）');
 assert.strictEqual(upgrade.disabled, false); // CLI downloads as part of upgrade.
 upgrade.handlers.click();
 assert.deepStrictEqual(ota.calls, []);
 ota.confirm(true);
 upgrade.handlers.click();
 await new Promise(resolve => setImmediate(resolve));
 assert.deepStrictEqual(ota.calls, [['upgrade', [true, tag]]]);
 assert.strictEqual(ota.button('切回第一系统（原厂）').disabled, true);

 const invalid = fixture(false);
 assert.strictEqual(invalid.button('切回第一系统（原厂）').disabled, true);
 assert.strictEqual(invalid.button('升级（保留配置）').disabled, true);
 assert.strictEqual(invalid.button('升级（不保留配置）').disabled, true);
 console.log('C8 LuCI confirmation, fixed version, one-click OTA and mutual exclusion verified');
})().catch(e => { console.error(e); process.exitCode = 1; });
