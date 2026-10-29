'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.argv[2], 'utf8');

function fixture(reply = { code: 0, stdout: 'result=ok\n', stderr: '' }) {
  const calls = [], notices = [];
  let modal, hidden = 0, refreshed = 0;
  const context = vm.createContext({
    view: { extend: spec => spec },
    fs: { exec: (path, args) => {
      calls.push({ path, args: Array.from(args) });
      return Promise.resolve(reply);
    } },
    // Deliberately expose no ui.confirm. These are the installed LuCI APIs.
    ui: {
      showModal: (title, children) => { modal = { title, children }; },
      hideModal: () => { hidden++; },
      addNotification: (title, body, kind) => notices.push({ body, kind })
    },
    _: value => value,
    E: (tag, attrs, children) => ({ tag, attrs, children })
  });
  vm.runInContext(`String.prototype.format = function(...args) {
    let index = 0; return this.replace(/%[sd]/g, () => String(args[index++]));
  };`, context);
  const view = vm.runInContext('(function() {\n' + source + '\n})()', context);
  view.status = { mode: 'auto' };
  view.fetch = () => { refreshed++; return Promise.resolve(); };
  const choose = accepted => {
    const row = modal.children[modal.children.length - 1];
    row.children[accepted ? 2 : 0].attrs.click();
  };
  return { view, calls, notices, choose, modal: () => modal,
    hidden: () => hidden, refreshed: () => refreshed };
}

async function main() {
  let f = fixture();
  let pending = f.view.command(['set', '255', '3'], 'full speed');
  assert.equal(f.modal().title, '退出智能温控');
  assert.equal(f.calls.length, 0, 'no write before confirmation');
  f.choose(false);
  await pending;
  assert.equal(f.calls.length, 0, 'cancel never writes hardware');
  assert.equal(f.view.busy, false);

  f = fixture();
  pending = f.view.command(['set', '255', '3'], 'full speed');
  f.choose(true);
  await pending;
  assert.deepEqual(f.calls, [{ path: '/usr/bin/airpi-fanctl.sh', args: ['set', '255', '3'] }]);
  assert.equal(f.hidden(), 1);
  assert.equal(f.refreshed(), 1);
  assert.equal(f.view.busy, false);
  assert.equal(f.notices[0].kind, 'info', 'LuCI code=0 is success');

  f = fixture();
  f.view.status.mode = 'manual';
  pending = f.view.command(['stepless', '0'], 'stop', 'stop warning');
  assert.equal(f.modal().title, '危险操作确认');
  f.choose(false);
  await pending;
  assert.equal(f.calls.length, 0, 'stop warning cannot be bypassed');
  await f.view.command(['stepless', '160'], 'stepless');
  assert.deepEqual(f.calls[0].args, ['stepless', '160']);
  await f.view.command(['auto'], 'auto');
  assert.deepEqual(f.calls[1].args, ['auto']);

  for (const reply of [{ code: 1, stdout: '', stderr: 'denied' },
                       { code: 0, stdout: 'result=fail\nerror=bad pin\n' },
                       { rc: 0, stdout: 'result=ok\n' }]) {
    f = fixture(reply);
    f.view.status.mode = 'manual';
    await f.view.command(['set', '128', '1'], 'cruise');
    assert.equal(f.notices.length, 1);
    assert.equal(f.notices[0].kind, 'error', 'failed/malformed reply must stay a failure');
    assert.equal(f.view.busy, false);
    assert.equal(f.refreshed(), 1);
  }
  console.log('fan UI confirmation/command regressions passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
