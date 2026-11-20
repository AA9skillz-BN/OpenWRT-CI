/* SPDX-License-Identifier: MIT
 * Protocol adapter for the locked WEBUI. Every command goes through LuCI ACLs.
 * A lost response closes this adapter; it never retries a modem write.
 */
(function () {
    'use strict';
    const nativeFetch = window.fetch.bind(window);
    let sequence = 0;
    function session() {
        try {
            if (window.parent === window || window.parent.location.origin !== window.location.origin)
                return null;
            return window.parent.L && window.parent.L.env.sessionid;
        } catch (_) { return null; }
    }
    async function rpc(method, params) {
        const sid = session();
        if (!sid || !/^[a-f0-9]{32}$/.test(sid) || /^0+$/.test(sid))
            throw new Error('请先登录 LuCI，再从蜂窝设置进入');
        const abort = new AbortController();
        const timer = setTimeout(() => abort.abort(), 8000);
        let response, data;
        try {
        response = await nativeFetch('/ubus/', {
            method: 'POST', credentials: 'same-origin', cache: 'no-store',
            signal: abort.signal,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ jsonrpc: '2.0', id: ++sequence, method: 'call',
                params: [sid, 'luci.c8_cellular', method, params] })
        });
        data = await response.json();
        } finally { clearTimeout(timer); }
        if (!response.ok || data.error || !data.result || data.result[0] !== 0 || !data.result[1])
            throw new Error('认证失效或蜂窝 RPC 不可用');
        if (data.result[1].error) throw new Error(data.result[1].error);
        return data.result[1];
    }
    function nonce() {
        return Array.from(crypto.getRandomValues(new Uint8Array(16)),
            x => x.toString(16).padStart(2, '0')).join('');
    }
    async function job(operation, value) {
        const submitted = await rpc('submit', { operation: operation, value: value, request: nonce() });
        const deadline = Date.now() + 220000;
        while (Date.now() < deadline) {
            await new Promise(resolve => setTimeout(resolve, 200));
            const result = await rpc('result', { id: submitted.id });
            if (result.state === 'done') return result.response;
        }
        throw new Error('操作结果未确认，请勿自动重试。重新进入页面后先查询状态');
    }
    window.fetch = function (input, options) {
        const url = new URL(typeof input === 'string' ? input : input.url, location.href);
        if (url.origin === location.origin && url.pathname === '/cgi-bin/at-ws-info') {
            return Promise.resolve(new Response(JSON.stringify({ success: true,
                data: { host: location.hostname, port: 8765, require_auth: false } }),
                { headers: { 'Content-Type': 'application/json' } }));
        }
        return nativeFetch(input, options);
    };
    class LuCIWebSocket extends EventTarget {
        constructor(url) {
            super();
            this.url = url;
            this.readyState = LuCIWebSocket.CONNECTING;
            this.chain = Promise.resolve();
            // The socket is a compatibility object only. Its URL is never used
            // for a network connection, including on HTTPS LuCI installations.
            setTimeout(async () => {
                try {
                    // Real ACL/service check before claiming a connection.
                    const response = await job('at', 'AT+CONNECT?');
                    if (!response.success) throw new Error(response.error);
                    if (this.readyState !== LuCIWebSocket.CONNECTING) return;
                    this.readyState = LuCIWebSocket.OPEN;
                    this.emit('open', new Event('open'));
                } catch (_) { this.fail(); }
            }, 0);
        }
        emit(type, event) {
            this.dispatchEvent(event);
            if (typeof this['on' + type] === 'function') this['on' + type](event);
        }
        send(command) {
            if (this.readyState !== LuCIWebSocket.OPEN) throw new Error('cellular adapter closed');
            if (command === 'ping') {
                setTimeout(() => this.emit('message', new MessageEvent('message', { data: 'pong' })), 0);
                return;
            }
            this.chain = this.chain.then(async () => {
                if (this.readyState !== LuCIWebSocket.OPEN) return;
                try {
                    const response = await job('at', command);
                    if (this.readyState === LuCIWebSocket.OPEN)
                        this.emit('message', new MessageEvent('message', { data: JSON.stringify(response) }));
                } catch (_) {
                    // No late answer may satisfy the next pending AT command.
                    this.fail();
                }
            });
        }
        fail() {
            this.emit('error', new Event('error'));
            this.close();
        }
        close() {
            if (this.readyState === LuCIWebSocket.CLOSED) return;
            this.readyState = LuCIWebSocket.CLOSED;
            this.emit('close', new Event('close'));
        }
    }
    Object.assign(LuCIWebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    Object.assign(LuCIWebSocket.prototype, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    window.WebSocket = LuCIWebSocket;
    window.c8SwitchSlot = async function (slot) {
        const names = { 0: '外置卡 1（正面）', 6: '外置卡 2（反面）', 1: '内置卡' };
        if (!names[slot] || !window.confirm('切换到' + names[slot] + '将暂时中断蜂窝网络，确认继续？')) return false;
        try {
            const response = await job('slot', String(slot));
            if (!response.success) throw new Error(response.error);
            window.alert('卡槽写入与回读已完成，请等待模组重新注册。上网状态请另行检查。');
            return true;
        } catch (e) { window.alert('切卡未确认：' + e.message + '。请检查状态，勿连续重试。'); return false; }
    };
    window.c8CellularJob = job;
}());
