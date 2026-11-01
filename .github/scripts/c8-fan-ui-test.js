'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function element(tag, attrs = {}, children = []) {
  return {
    tag, ...attrs, children, listeners: {},
    appendChild(node) { this.children.push(node); this.lastChild = node; },
    addEventListener(type, handler) { this.listeners[type] = handler; },
    input(value) {
      this.value = value;
      this.listeners.input({target: this});
    }
  };
}

async function main() {
  const staged = {};
  let saves = 0;
  const context = vm.createContext({
    view: {extend: spec => spec},
    L: {bind: (fn, owner) => fn.bind(owner)},
    E: element,
    _: text => text,
    uci: {
      get: () => true,
      set: (config, section, key, value) => { staged[key] = value; },
      save: () => { saves++; return Promise.resolve(); }
    }
  });
  const source = fs.readFileSync(process.argv[2], 'utf8');
  const view = vm.runInContext('(function() {\n' + source + '\n})()', context);
  view.values = {mode: 'manual', manual_pwm: '160'};
  view.original = {...view.values};
  const changed = [];
  view.afterChange = key => changed.push(key);
  view.markDirty = () => {};
  view.notify = () => {};
  view.field('manual_pwm');
  const range = view.widgetRange.manual_pwm;
  const number = view.widgetInput.manual_pwm;
  range.input('230');
  assert.equal(view.values.manual_pwm, '230', 'slider must update the saved value');
  assert.equal(number.value, '230', 'slider must update the numeric field');
  number.input('180');
  assert.equal(view.values.manual_pwm, '180', 'numeric input must update the saved value');
  assert.equal(range.value, '180', 'numeric input must update the slider');
  assert.deepEqual(changed, ['manual_pwm', 'manual_pwm']);
  await view.stageUci(['manual_pwm']);
  assert.equal(saves, 1);
  assert.equal(staged.manual_pwm, '180', 'UCI must stage the edited PWM, not the old value');
  assert.equal(view.original.manual_pwm, '180');
  console.log('C8 slider/input/UCI regression passed');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
