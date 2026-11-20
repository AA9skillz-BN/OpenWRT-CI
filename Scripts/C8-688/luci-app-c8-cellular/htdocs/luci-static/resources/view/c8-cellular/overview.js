'use strict';
'require view';
'require ui';

return view.extend({
    render: function () {
        const frame = E('iframe', { src: '/5700/index.html', title: 'C8 蜂窝设置',
            style: 'width:100%;height:82vh;min-height:600px;border:0;background:white',
            referrerpolicy: 'same-origin' });
        const slot = E('select', { 'class': 'cbi-input-select' }, [
            E('option', { value: '0' }, '外置卡 1（正面）'),
            E('option', { value: '6' }, '外置卡 2（反面）'),
            E('option', { value: '1' }, '内置卡')
        ]);
        const output = E('pre', { style: 'white-space:pre-wrap;max-height:40vh;overflow:auto' });
        return E('div', {}, [
            E('h2', {}, '蜂窝设置'),
            E('div', { 'class': 'cbi-section' }, [slot,
                E('button', { 'class': 'btn', click: ui.createHandlerFn(this, async function () {
                    if (frame.contentWindow && frame.contentWindow.c8SwitchSlot)
                        await frame.contentWindow.c8SwitchSlot(Number(slot.value));
                    else ui.addNotification(null, E('p', {}, '蜂窝页面尚未加载'), 'warning');
                }) }, '切换卡槽'),
                E('button', { 'class': 'btn', click: ui.createHandlerFn(this, async function () {
                    try {
                        const result = await frame.contentWindow.c8CellularJob('diagnostics', '');
                        if (!result.success) throw new Error(result.error);
                        output.textContent = JSON.stringify(result.data, null, 2);
                    } catch (e) { output.textContent = '诊断未完成：' + e.message; }
                }) }, '检查 IPv4 / IPv6 / DNS'),
                E('button', { 'class': 'btn', click: ui.createHandlerFn(this, async function () {
                    try {
                        const job = frame.contentWindow.c8CellularJob;
                        if (!job) throw new Error('蜂窝页面尚未加载');
                        const current = await job('at', 'AT+WEBHOOK?');
                        if (!current.success) throw new Error(current.error);
                        const match = current.data.match(/\+WEBHOOK:\s*([01]),"([^"\r\n]*)"/);
                        if (!match) throw new Error('通知配置读取失败');
                        const enabled = E('input', { type: 'checkbox' });
                        enabled.checked = match[1] === '1';
                        const url = E('input', { type: 'password', value: match[2],
                            autocomplete: 'off', style: 'width:100%', placeholder: 'https://…' });
                        ui.showModal('企业微信新短信提醒', [
                            E('p', {}, '仅提醒新短信数量，不发送短信正文；默认关闭。'),
                            E('label', {}, [enabled, ' 启用提醒']), url,
                            E('div', { 'class': 'right' }, [
                                E('button', { 'class': 'btn', click: ui.hideModal }, '取消'),
                                E('button', { 'class': 'btn cbi-button-positive', click:
                                    ui.createHandlerFn(this, async function () {
                                        if (/["\r\n]/.test(url.value)) throw new Error('通知地址包含非法字符');
                                        const result = await job('at', 'AT+WEBHOOK=' +
                                            (enabled.checked ? '1' : '0') + ',"' + url.value + '"');
                                        if (!result.success) throw new Error(result.error);
                                        ui.hideModal();
                                    }) }, '保存')
                            ])
                        ]);
                    } catch (e) { ui.addNotification(null, E('p', {}, e.message), 'warning'); }
                }) }, '新短信提醒'),
                E('p', {}, '查询、短信和切卡共用互斥锁。切卡会断网；未确认结果时请先查询，避免重复提交。'),
                output
            ]), frame
        ]);
    },
    handleSaveApply: null,
    handleSave: null,
    handleReset: null
});
