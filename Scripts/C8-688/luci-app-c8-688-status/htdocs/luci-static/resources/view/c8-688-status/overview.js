'use strict';
'require view';
'require rpc';
'require poll';
'require dom';

var callStatus = rpc.declare({ object: 'luci.c8_688_status', method: 'status', expect: {} });
var unknown = '未知';

function display(value) {
	return value == null || value === '' ? unknown : String(value);
}

function rows(headings, values) {
	return E('table', { 'class': 'table' }, [
		E('tr', { 'class': 'tr table-titles' }, headings.map(function(h) { return E('th', { 'class': 'th' }, h); }))
	].concat(values.map(function(row) {
		return E('tr', { 'class': 'tr' }, row.map(function(value) { return E('td', { 'class': 'td' }, display(value)); }));
	})));
}

function temperature(value) {
	return value == null ? unknown : (value / 1000).toFixed(1) + ' ℃';
}

function defaults(items) {
	if (items == null) return unknown;
	return items.length ? items.map(function(r) { return r.device + '（metric ' + r.metric + '）'; }).join('、') : '主路由表未发现';
}

function content(status) {
	var b = status.board, n = status.network, t = status.thermal;
	var fan = t.fans[0];
	var warnings = {
		network_status_unavailable: '接口状态读取失败，当前数据不完整。',
		cpu_temperature_unknown: '无法读取 CPU 温度，请检查温控服务和传感器。',
		cpu_temperature_high: 'CPU 已达到 75℃，请检查风扇实际转动和散热。',
		fan_pwm_unknown: '无法读取风扇 PWM，请检查驱动和温控服务。'
	};
	return E('div', {}, [
		E('div', {}, (status.warnings || []).map(function(w) { return E('p', { 'class': 'alert-message warning' }, warnings[w] || w); })),
		E('h3', {}, '设备与温控'),
		rows(['项目', '当前状态'], [
			['设备', b.model], ['固件', b.firmware], ['构建标签', b.build_tag], ['内核', b.kernel],
			['当前启动槽', status.boot.second_slot_running ? '第二系统（根据本次启动参数）' : '未确认第二系统'],
			['CPU 温度', temperature(t.cpu_millidegrees)],
			['风扇 PWM', fan && fan.pwm != null ? fan.pwm + ' / 255（' + Math.round(fan.pwm * 100 / 255) + '%）' : unknown],
			['实测风扇转速', fan && fan.rpm != null ? fan.rpm + ' RPM' : '硬件未提供转速反馈']
		]),
		E('p', { 'class': 'cbi-section-descr' }, 'PWM 是控制值，不等于实测转速。本页不改变温控策略，也不检查或修改下一次启动选择。'),
		E('h3', {}, '网口与实际连接'),
		rows(['网卡', '链路', '协商速率', '网桥 / 逻辑接口', '收 / 发错误'], n.devices.map(function(d) {
			return [d.name, !d.present ? '未发现' : d.carrier == null ? unknown : d.carrier ? '已连接' : '未连接',
				d.carrier === 1 && d.speed_mbps != null ? d.speed_mbps + ' Mbit/s' : unknown,
				[d.bridge].concat(d.interfaces).filter(Boolean).join(' / ') || '—',
				display(d.rx_errors) + ' / ' + display(d.tx_errors)];
		})),
		E('p', {}, 'eth1 是 C8 板载模组的 2.5G 连接；USB 拨号网卡按实际接口名称显示。协商速率不能代表实际联网吞吐。'),
		E('h3', {}, '网络接口'),
		rows(['接口', '协议', '设备', '状态', 'IPv4 / IPv6 地址数', 'IPv6 委派前缀数'], n.interfaces.map(function(i) {
			return [i.name, i.proto, i.device, i.up == null ? unknown : i.up ? '已启动' : i.pending ? '等待连接' : '未启动',
				i.ipv4_addresses + ' / ' + i.ipv6_addresses, i.ipv6_prefixes];
		})),
		rows(['主路由表', '默认出口'], [['IPv4', defaults(n.ipv4_defaults)], ['IPv6', defaults(n.ipv6_defaults)]]),
		E('p', { 'class': 'cbi-section-descr' }, '此处只读取接口和主路由表。策略路由需另行检查；有地址、有默认路由均不证明互联网、DNS 或 LAN 转发正常。'),
		E('h3', {}, 'USB 设备'),
		rows(['连接位置', '厂商 / 产品 ID', '实际协商速率'], status.usb.map(function(d) {
			return [d.port, d.vendor + ':' + d.product, d.speed_mbps == null ? unknown : d.speed_mbps + ' Mbit/s'];
		})),
		E('p', {}, '仅显示实际 USB 设备，排除根集线器速率。诊断导出不包含密码、Wi-Fi 密钥、IP/MAC 地址或 SIM 标识。')
	]);
}

return view.extend({
	load: function() { return callStatus(); },
	render: function(initial) {
		var latest = null;
		var stamp = E('p', { 'role': 'status' }, '正在读取设备状态…');
		var body = E('div');
		var download = E('button', { 'class': 'cbi-button cbi-button-action', 'disabled': true }, '导出诊断 JSON');
		function update(status) {
			if (!status || status.error || status.schema_version !== 1) throw new Error('设备状态读取失败，请检查 RPC 服务和机型。');
			latest = status;
			download.disabled = false;
			dom.content(body, content(status));
			stamp.textContent = '更新时间：' + new Date().toLocaleTimeString() + '；每 10 秒刷新。';
		}
		function failed() {
			latest = null;
			download.disabled = true;
			stamp.textContent = '刷新失败；下方如有数据为上一次快照，请勿作为当前状态。';
		}
		download.addEventListener('click', function() {
			if (!latest) return;
			var blob = new Blob([JSON.stringify(latest, null, 2) + '\n'], { type: 'application/json' });
			var url = URL.createObjectURL(blob), a = document.createElement('a');
			a.href = url; a.download = 'c8-device-status.json'; a.click();
			setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
		});
		try { update(initial); } catch (e) { failed(); }
		poll.add(function() { return callStatus().then(update).catch(failed); }, 10);
		return E('div', { 'class': 'cbi-map' }, [
			E('h2', {}, 'C8 设备状态'),
			E('p', { 'class': 'cbi-map-descr' }, '集中查看网口、模组连接、温度与启动槽，便于排查固件问题。'),
			stamp, download,
			E('p', {}, [E('a', { href: L.url('admin/network/network') }, '网络接口'), ' · ',
				E('a', { href: L.url('admin/status/syslog') }, '系统日志'), ' · ',
				E('a', { href: L.url('admin/system/c8-688-ota') }, '固件升级')]),
			body
		]);
	},
	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});
