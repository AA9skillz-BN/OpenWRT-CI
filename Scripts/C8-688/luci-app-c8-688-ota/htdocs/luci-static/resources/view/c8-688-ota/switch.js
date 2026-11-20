'use strict';
'require view';
'require poll';
'require rpc';

var callStatus = rpc.declare({ object: 'luci.c8_688_ota', method: 'status', expect: {} });
var callOfficial = rpc.declare({ object: 'luci.c8_688_ota', method: 'official', params: [ 'confirm' ], expect: {} });

return view.extend({
	load: function() {
		return callStatus();
	},

	render: function(status) {
		var readonly = this.readonly === true;
		var st, pending = false, fresh = false;
		var current = E('span', {}, '未知');
		var next = E('span', {}, '未知');
		var message = E('p', { 'class': 'cbi-value-description', 'role': 'status', 'aria-live': 'polite' }, '');
		var button = E('button', { 'class': 'cbi-button cbi-button-remove', 'disabled': 'disabled' }, '切回第一系统并重启');

		function busy() {
			return pending || !!st.downloading || !!(st.operation &&
				[ 'running', 'flashing', 'switching' ].indexOf(st.operation.state) >= 0);
		}
		function refreshButton() {
			button.disabled = readonly || !fresh || busy() || st.enabled === false ||
				!st.boot || st.boot.ready !== true || [ '0', '1' ].indexOf(st.boot.system) < 0;
		}
		function unavailable(error) {
			fresh = false;
			current.textContent = '未知';
			next.textContent = '未知';
			message.textContent = error || '状态读取失败，系统切换已禁用。';
			refreshButton();
		}
		function applyStatus(value) {
			st = value || {};
			if (st.error) { unavailable(st.error); return; }
			fresh = true;
			current.textContent = st.boot && st.boot.ready === true ? '第二系统（当前固件）' : '无法确认';
			next.textContent = st.boot && st.boot.ready === true && [ '0', '1' ].indexOf(st.boot.system) >= 0 ?
				(st.boot.system === '0' ? '第一系统（原厂）' : '第二系统（当前固件）') : '无法确认';
			if (pending || (st.operation && st.operation.state === 'switching'))
				message.textContent = '系统切换任务已提交，请等待设备重启。当前连接会断开，请勿重复操作。';
			else if (busy())
				message.textContent = '下载或升级任务正在运行，请完成后再切换系统。';
			else if (!st.boot || st.boot.ready !== true || [ '0', '1' ].indexOf(st.boot.system) < 0)
				message.textContent = (st.boot && st.boot.error) || '启动环境未通过校验，系统切换已禁用。';
			else if (st.enabled === false)
				message.textContent = '页面操作已关闭，系统切换不可用。';
			else if (st.operation && st.operation.state === 'failed')
				message.textContent = st.operation.detail || '上次任务失败，请检查状态后再操作。';
			else
				message.textContent = readonly ? '当前账号只有查看权限。' : '启动环境校验通过。切换前请确认第一系统仍然完好。';
			refreshButton();
		}
		button.addEventListener('click', function() {
			if (button.disabled) return;
			if (!confirm('切回第一系统（原厂）并重启？\n\n当前连接将断开，原厂系统必须仍然完好。切换后的管理地址、账号和 Wi-Fi 按原厂系统配置。\n\n本操作不刷写固件；回到第二系统需使用原厂自身的切换入口。')) return;
			pending = true;
			refreshButton();
			message.textContent = '正在提交系统切换，请勿重复操作。';
			callOfficial(true).then(function(result) {
				if (!result || result.error || result.started !== true) {
					pending = false;
					unavailable((result && result.error) || '系统切换未启动，请刷新状态后再操作。');
					return;
				}
				// Retain the local latch even if a poll returns an older snapshot.
				message.textContent = '系统切换任务已提交，请等待设备重启。当前连接会断开，请勿重复操作。';
			}).catch(function() {
				// A lost response does not prove that the write was rejected.
				message.textContent = '切换结果未确认，请先重新连接并检查启动状态，勿重复提交。';
			});
		});
		applyStatus(status);
		poll.add(function() {
			return callStatus().then(applyStatus).catch(function() {
				unavailable(pending ? '连接已断开或状态未知，请确认设备启动后的系统。' : '状态读取失败，系统切换已禁用。');
			});
		}, 3);
		return E([], [
			E('h2', {}, '切换系统'),
			E('div', { 'class': 'cbi-section' }, [
				E('h3', {}, '双系统启动选择'),
				E('p', {}, [ '当前运行：', current ]),
				E('p', {}, [ '下次启动：', next ]),
				E('p', {}, '此操作选择已有的第一系统并重启，不安装或覆盖固件。两个系统使用各自保存的设置。'),
				E('p', {}, '第一系统必须仍然完好；回到原厂后，需要从原厂管理页面切回第二系统。'),
				button, message
			])
		]);
	},
	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});
