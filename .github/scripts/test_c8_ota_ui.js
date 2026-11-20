// Execute the real LuCI view with isolated DOM/RPC; never contact a router.
'use strict';
const fs = require('fs');
const assert = require('assert');
const path = require('path');
const source = fs.readFileSync(path.join(__dirname,
 '../../Scripts/C8-688/luci-app-c8-688-ota/htdocs/luci-static/resources/view/c8-688-ota/overview.js'), 'utf8');
const switchSource = fs.readFileSync(path.join(__dirname,
 '../../Scripts/C8-688/luci-app-c8-688-ota/htdocs/luci-static/resources/view/c8-688-ota/switch.js'), 'utf8');
const tag = 'C8-688-immortalwrt-master-fixture';

function fixture(ready = true, options = {}) {
 const calls = [], nodes = [];
 let refresh;
 let accept = false;
 function E(name, attrs, children) {
  const node = { name, attrs: attrs || {}, children: Array.isArray(children) ? children : [ children ],
   style: {}, value: '', handlers: {}, disabled: !!(attrs && attrs.disabled),
   appendChild(c) { this.children.push(c); },
   addEventListener(e, cb) { this.handlers[e] = cb; } };
  nodes.push(node);
  return node;
 }
 let status = { current: 'old', enabled: true, ota_ready: ready,
  boot: { ready, system: '1' }, images: [], keep_config: true,
  cached: { latest: tag, size: 4, sha256: 'a'.repeat(64), update: true }, ...options.status };
 const rpc = { declare: spec => (...args) => {
  calls.push([spec.method, args]);
  if (options.lost && spec.method !== 'status') return Promise.reject(Error('response lost'));
  if (options.pollLost && spec.method === 'status') return Promise.reject(Error('status lost'));
  return Promise.resolve(spec.method === 'status' ? status : options.response || { started: true });
 } };
 const view = new Function('view', 'rpc', 'poll', 'E', 'confirm', options.switch ? switchSource : source)(
  { extend: x => x }, rpc, { add(fn) { refresh = fn; } }, E, () => accept);
 view.readonly = options.readonly === true;
 view.render(status);
 const button = text => nodes.find(n => n.name === 'button' && n.children[0] === text);
 return { calls, button, nodes, confirm: v => { accept = v; }, refresh: async value => {
  if (value) status = value;
  return refresh();
 } };
}

(async () => {
 const first = fixture(true, { switch: true });
 assert.deepStrictEqual(first.calls, [], 'rendering must never change the boot environment');
 const original = first.button('切回第一系统并重启');
 assert.strictEqual(original.disabled, false);
 original.handlers.click();
 assert.deepStrictEqual(first.calls, []); // Cancel must not submit anything.
 first.confirm(true);
 original.handlers.click();
 await new Promise(resolve => setImmediate(resolve));
 assert.deepStrictEqual(first.calls, [['official', [true]]]);
 assert.strictEqual(original.disabled, true);
 original.handlers.click();
 assert.strictEqual(first.calls.length, 1, 'double clicks must not repeat the switch');
 await first.refresh(); // Older idle status must not clear the submitted latch.
 assert.strictEqual(original.disabled, true);

 const ota = fixture();
 const upgrade = ota.button('升级（保留配置）');
 assert.strictEqual(upgrade.disabled, false); // CLI downloads as part of upgrade.
 upgrade.handlers.click();
 assert.deepStrictEqual(ota.calls, []);
 ota.confirm(true);
 upgrade.handlers.click();
 await new Promise(resolve => setImmediate(resolve));
 assert.deepStrictEqual(ota.calls, [['upgrade', [true, tag]]]);
 assert.strictEqual(ota.button('切回第一系统并重启'), undefined, 'system switching has its own page');
 assert(!source.includes("method: 'official'"), 'OTA view must not submit a system switch');

 const invalid = fixture(false);
 assert.strictEqual(invalid.button('升级（保留配置）').disabled, true);
 assert.strictEqual(invalid.button('升级（不保留配置）').disabled, true);
 for (const options of [
  { status: { boot: { ready: false, error: 'bad CRC' } } },
  { status: { boot: { ready: true, system: 'unknown' } } },
  { status: { boot: null } },
  { status: { error: 'status unavailable' } },
  { status: { enabled: false } }, { readonly: true },
  { status: { downloading: tag } },
  ...['running', 'flashing', 'switching'].map(state => ({ status: { operation: { state } } }))
 ]) {
  const blocked = fixture(true, { switch: true, ...options });
  const button = blocked.button('切回第一系统并重启');
  assert.strictEqual(button.disabled, true);
  blocked.confirm(true);
  button.handlers.click();
  assert.deepStrictEqual(blocked.calls, [], 'blocked boot/status/task must never submit a write');
 }
 const stale = fixture(true, { switch: true });
 await stale.refresh({ error: 'CRC status unavailable' });
 assert.strictEqual(stale.button('切回第一系统并重启').disabled, true);
 await stale.refresh({ enabled: true, boot: { ready: true, system: '0' } });
 assert.strictEqual(stale.button('切回第一系统并重启').disabled, false);
 assert(stale.nodes.some(node => node.textContent === '第一系统（原厂）'));
 const pollLost = fixture(true, { switch: true, pollLost: true });
 await pollLost.refresh();
 assert.strictEqual(pollLost.button('切回第一系统并重启').disabled, true);
 for (const options of [{ lost: true }, { response: { error: 'guard rejected', started: false } }]) {
  const failed = fixture(true, { switch: true, ...options });
  failed.confirm(true);
  const button = failed.button('切回第一系统并重启');
  button.handlers.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.strictEqual(button.disabled, true);
  button.handlers.click();
  assert.strictEqual(failed.calls.length, 1);
  if (options.lost) {
   await failed.refresh();
   assert.strictEqual(button.disabled, true, 'an ambiguous switch result cannot be retried on stale idle status');
  }
 }
 console.log('C8 separate system switch confirmation, stale/lost status, fixed OTA version and mutual exclusion verified');
})().catch(e => { console.error(e); process.exitCode = 1; });
