/* SPDX-License-Identifier: MIT
 * Protocol adapter for the locked WEBUI. Every command goes through LuCI ACLs.
 * A lost response closes this adapter; it never retries a modem write.
 */
(function () {
    'use strict';
    const nativeFetch = window.fetch.bind(window);
    let sequence = 0;
    let activeSocket = null;
    function pdcpSample(response) {
        if (!response.success || typeof response.data !== 'string') return null;
        const line = response.data.split(/[\r\n]+/).find(x => /^\^PDCPDATAINFO:\s*\d+(?:,\s*\d+){13,15}\s*$/.test(x));
        if (!line) return null;
        const values = line.split(':')[1].trim().split(',').map(Number);
        if (!values.every(Number.isSafeInteger)) return null;
        const fields = ['id', 'pduSessionId', 'discardTimerLen', 'avgDelay', 'minDelay', 'maxDelay',
            'highPriQueMaxBuffTime', 'lowPriQueMaxBuffTime', 'highPriQueBuffPktNums', 'lowPriQueBuffPktNums',
            'ulPdcpRate', 'dlPdcpRate', 'ulDiscardCnt', 'dlDiscardCnt', 'timestamp1', 'timestamp2'];
        return Object.fromEntries(fields.slice(0, values.length).map((field, i) =>
            [field, i >= 3 && i <= 7 ? values[i] / 10 : values[i]]));
    }
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
            this.pending = 0;
            this.pdcpTimer = null;
            this.pdcpGeneration = 0;
            // The socket is a compatibility object only. Its URL is never used
            // for a network connection, including on HTTPS LuCI installations.
            setTimeout(async () => {
                try {
                    // Real ACL/service check before claiming a connection.
                    const response = await job('at', 'AT+CONNECT?');
                    if (!response.success) throw new Error(response.error);
                    if (this.readyState !== LuCIWebSocket.CONNECTING) return;
                    this.readyState = LuCIWebSocket.OPEN;
                    activeSocket = this;
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
            this.enqueue(async () => {
                try {
                    const response = await job('at', command);
                    if (this.readyState === LuCIWebSocket.OPEN)
                        this.emit('message', new MessageEvent('message', { data: JSON.stringify(response) }));
                } catch (_) {
                    // No late answer may satisfy the next pending AT command.
                    this.fail();
                }
            }).catch(() => this.fail());
        }
        enqueue(task) {
            ++this.pending;
            const result = this.chain.then(() => {
                if (this.readyState !== LuCIWebSocket.OPEN) throw new Error('cellular adapter closed');
                return task();
            });
            this.chain = result.catch(() => {}).finally(() => { --this.pending; });
            return result;
        }
        async pdcpReport(enabled, interval) {
            const generation = ++this.pdcpGeneration;
            clearTimeout(this.pdcpTimer);
            if (!enabled) return { success: true, data: 'OK', error: null };
            const period = Math.max(5000, Math.min(65535, Number(interval) || 5000));
            const sample = async () => {
                const response = await job('at', 'AT^PDCPDATAINFO?');
                const data = pdcpSample(response);
                if (!data) throw new Error(response.error || 'modem_has_no_pdcp_sample');
                if (generation === this.pdcpGeneration && this.readyState === LuCIWebSocket.OPEN)
                    this.emit('message', new MessageEvent('message', { data: JSON.stringify({ type: 'pdcp_data', data }) }));
            };
            const schedule = () => {
                if (generation !== this.pdcpGeneration || this.readyState !== LuCIWebSocket.OPEN) return;
                this.pdcpTimer = setTimeout(async () => {
                    try {
                        // Do not build a telemetry backlog behind SMS, scans or writes.
                        if (this.pending === 0) await this.enqueue(sample);
                        schedule();
                    } catch (_) { this.fail(); }
                }, period);
            };
            try {
                await this.enqueue(sample);
                if (generation !== this.pdcpGeneration || this.readyState !== LuCIWebSocket.OPEN)
                    throw new Error('pdcp_sampling_cancelled');
                schedule();
                return { success: true, data: 'OK', error: null };
            } catch (e) {
                // Never report an enabled speed display without an actual sample.
                return { success: false, data: null, error: e.message };
            }
        }
        fail() {
            this.emit('error', new Event('error'));
            this.close();
        }
        close() {
            if (this.readyState === LuCIWebSocket.CLOSED) return;
            this.readyState = LuCIWebSocket.CLOSED;
            ++this.pdcpGeneration;
            clearTimeout(this.pdcpTimer);
            if (activeSocket === this) activeSocket = null;
            this.emit('close', new Event('close'));
        }
    }
    Object.assign(LuCIWebSocket, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    Object.assign(LuCIWebSocket.prototype, { CONNECTING: 0, OPEN: 1, CLOSING: 2, CLOSED: 3 });
    window.WebSocket = LuCIWebSocket;
    window.c8PDCPReport = function (enabled, interval) {
        if (!activeSocket || activeSocket.readyState !== LuCIWebSocket.OPEN)
            return Promise.resolve({ success: false, data: null, error: 'cellular_adapter_closed' });
        return activeSocket.pdcpReport(enabled, interval);
    };
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
