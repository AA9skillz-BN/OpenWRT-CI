/* Execute the bridge and the real pinned bundle's SMS response collector. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { webcrypto } = require('node:crypto');
const bridge = fs.readFileSync('Scripts/C8-688/luci-app-c8-cellular/htdocs/5700/c8-bridge.js', 'utf8');
const wait = () => new Promise(resolve => setTimeout(resolve, 100));
async function until(test) {
    for (let i = 0; i < 100; ++i) {
        if (test()) return;
        await wait();
    }
    assert.fail('asynchronous adapter condition timed out');
}

function environment(options = {}) {
    const calls = [], commands = [], errors = [], jobs = new Map();
    let serial = 0;
    const location = new URL('https://router.test/5700/index.html');
    const nativeFetch = async (url, params) => {
        calls.push(url);
        assert.equal(url, '/ubus/');
        const request = JSON.parse(params.body);
        assert.equal(request.params[0], '1'.repeat(32));
        assert.equal(request.params[1], 'luci.c8_cellular');
        const method = request.params[2], value = request.params[3];
        if (options.denied) return { ok: true, json: async () => ({ result: [6] }) };
        let result;
        if (method === 'submit') {
            assert.match(value.request, /^[a-f0-9]{32}$/);
            commands.push(value.value);
            result = { id: String(++serial), state: 'queued' };
            jobs.set(result.id, value);
        } else {
            const job = jobs.get(value.id);
            if (options.lost && job.value !== 'AT+CONNECT?') throw Error('connection lost');
            const response = options.failed && job.value !== 'AT+CONNECT?' ?
                { success: false, data: null, error: 'missing_final_ok' } :
                { success: true, data: '+DATA: ERROR statistics\r\nOK', error: null };
            result = { state: 'done', response };
        }
        return { ok: true, json: async () => ({ result: [0, result] }) };
    };
    const window = { fetch: nativeFetch, location,
        parent: { location, L: { env: { sessionid: options.noSession ? '' : '1'.repeat(32) } } },
        confirm: () => options.confirm !== false, alert: x => errors.push(x) };
    const context = vm.createContext({ window, location, URL, Response, Event, EventTarget, MessageEvent, AbortController,
        crypto: webcrypto, Uint8Array, Date,
        setTimeout: (fn, ms) => setTimeout(fn, ms === 8000 ? 8000 : 0), clearTimeout });
    vm.runInContext(bridge, context);
    return { window, commands, calls, errors };
}

async function tests() {
    const test = environment();
    const config = await (await test.window.fetch('/cgi-bin/at-ws-info')).json();
    assert.equal(config.success, true);
    assert.deepEqual(test.calls, []);
    const socket = new test.window.WebSocket('ws://router.test:8765');
    let opened = 0;
    socket.onopen = () => ++opened;
    const messages = [];
    socket.onmessage = e => messages.push(e.data);
    await until(() => opened === 1);
    assert.equal(opened, 1);
    socket.send('AT+CSQ');
    socket.send('AT+CMGL=4');
    await until(() => messages.length === 2);
    assert.deepEqual(test.commands, ['AT+CONNECT?', 'AT+CSQ', 'AT+CMGL=4']);
    assert.equal(messages.length, 2);
    assert.equal(JSON.parse(messages[0]).success, true);
    assert(test.calls.every(url => url === '/ubus/'), 'HTTPS bridge must never connect to port 8765');
    socket.send('ping');
    await until(() => messages.at(-1) === 'pong');
    assert.equal(messages.at(-1), 'pong');
    socket.close();
    assert.throws(() => socket.send('AT'));

    for (const options of [{ noSession: true }, { denied: true }]) {
        const denied = environment(options);
        const ws = new denied.window.WebSocket('ws://ignored:8765');
        await until(() => ws.readyState === ws.CLOSED);
        assert.equal(ws.readyState, ws.CLOSED);
        assert.deepEqual(denied.commands, []);
    }
    const lost = environment({ lost: true });
    const ws = new lost.window.WebSocket('ws://ignored:8765');
    const answers = [];
    ws.onmessage = e => answers.push(e.data);
    await until(() => ws.readyState === ws.OPEN);
    ws.send('AT+CMGS=2\\r000102');
    ws.send('AT+CSQ');
    await until(() => ws.readyState === ws.CLOSED);
    assert.equal(ws.readyState, ws.CLOSED);
    assert.deepEqual(lost.commands, ['AT+CONNECT?', 'AT+CMGS=2\\r000102']);
    assert.deepEqual(answers, [], 'lost submit result cannot satisfy next command');
    const cancelled = environment({ confirm: false });
    await cancelled.window.c8SwitchSlot(6);
    assert.deepEqual(cancelled.calls, []);

    if (process.argv[2]) {
        const bundle = fs.readFileSync(process.argv[2], 'utf8');
        assert(!bundle.includes('_r().mock("/api/cpe/'), 'demo mock responses must be absent');
        assert(bundle.includes('"maxReconnectAttempts",0'), 'lost jobs require an explicit page reload');
        const clearStart = bundle.indexOf('key:"clearPendingCommands",value:function(S){');
        const clearEnd = bundle.indexOf('},{key:', clearStart);
        const clear = new Function('S', bundle.slice(clearStart, clearEnd).split('value:function(S){')[1].slice(0, -1));
        let disconnected;
        const waiting = { smsCollecting: true, smsResolve: x => { disconnected = x; },
            smsBuffer: [], smsTimer: null, pendingCommands: new Map() };
        clear.call(waiting, 'connection lost');
        assert.equal(disconnected.success, false);
        assert.equal(disconnected.error, 'connection lost');
        assert.equal(waiting.smsCollecting, false);
        const responseHandler = bundle.indexOf('key:"handleWebSocketMessage"');
        const start = bundle.indexOf('if(this.smsCollecting)', responseHandler);
        const end = bundle.indexOf('if(S==="pong")', start);
        assert(start > 0 && end > start);
        const handler = new Function('S', bundle.slice(start, end));
        for (const response of [{ success: false, data: null, error: 'missing_final_ok' },
            { success: true, data: '+DATA: ERROR statistics\r\nOK', error: null }]) {
            let resolved;
            const service = { smsCollecting: true, smsBuffer: [], smsTimer: null,
                smsResolve: r => { resolved = r; } };
            handler.call(service, JSON.stringify(response));
            assert.deepEqual(resolved, response);
            assert.equal(service.smsCollecting, false);
            assert.equal(service.smsResolve, null);
        }
    }
    console.log('C8 authenticated bridge, HTTPS, no replay and pinned SMS response regression passed');
}
tests().catch(error => { console.error(error); process.exitCode = 1; });
