'use strict';
'require view';
'require poll';
'require rpc';

var callStatus = rpc.declare({ object: 'luci.c8_688_ota', method: 'status', expect: {} });
var callCheck = rpc.declare({ object: 'luci.c8_688_ota', method: 'check', expect: {} });
var callDownload = rpc.declare({ object: 'luci.c8_688_ota', method: 'download', params: [ 'tag' ], expect: {} });
var callUpgrade = rpc.declare({ object: 'luci.c8_688_ota', method: 'upgrade', params: [ 'keep', 'tag' ], expect: {} });
var callUpgradeTag = rpc.declare({ object: 'luci.c8_688_ota', method: 'upgrade_tag', params: [ 'tag', 'keep' ], expect: {} });
var callSettings = rpc.declare({ object: 'luci.c8_688_ota', method: 'set_settings', params: [ 'keep_config', 'use_mirror' ], expect: {} });

var callOfficial = rpc.declare({ object: 'luci.c8_688_ota', method: 'official', params: [ 'confirm' ], expect: {} });

function mb(bytes) {
	return (Number(bytes || 0) / 1048576).toFixed(2) + ' MB';
}

function shortTag(tag, prefix) {
	if (!tag)
		return '—';
	return (prefix && tag.indexOf(prefix) === 0) ? tag.substring(prefix.length) : tag;
}

function fmtTime(epoch) {
	if (!epoch)
		return '';
	var d = new Date(Number(epoch) * 1000);
	return d.toLocaleString();
}

return view.extend({
	load: function() {
		return callStatus();
	},

	render: function(status) {
		var st = status || {};
		var latest = null;
		var prefix = st.tag_prefix || 'C8-688-';

		var curVal   = E('span', { 'class': 'label' }, st.current || '未知');
		var newVal   = E('span', { 'class': 'label' }, '尚未检查');
		var noteVal  = E('div', { 'class': 'cbi-value-description' }, '');
		var sizeVal  = E('span', {}, '—');
		var stateVal = E('span', {}, '空闲');
		var bootVal = E('div', { 'class': 'cbi-value-description' }, '');
		var btnOfficial = E('button', { 'class': 'cbi-button cbi-button-remove', 'disabled': 'disabled' }, '切回第一系统（原厂）');
		var msgVal   = E('div', { 'class': 'cbi-value-description' }, '');

		var barFill = E('div', { 'style': 'height:100%;width:0%;background:#2b8cf0;transition:width .3s' }, '');
		var barWrap = E('div', {
			'style': 'height:10px;background:#e6e6e6;border-radius:5px;overflow:hidden;margin:.3em 0'
		}, [ barFill ]);

		var btnCheck = E('button', { 'class': 'cbi-button cbi-button-action' }, '检查更新');
		var btnDownload = E('button', { 'class': 'cbi-button cbi-button-action', 'disabled': 'disabled' }, '下载并校验');
		var btnKeep = E('button', { 'class': 'cbi-button cbi-button-apply', 'disabled': 'disabled' }, '升级（保留配置）');
		var btnWipe = E('button', { 'class': 'cbi-button cbi-button-remove', 'disabled': 'disabled' }, '升级（不保留配置）');

		var selRollback = E('select', { 'style': 'min-width:22em' }, [
			E('option', { 'value': '' }, '（无本地镜像）')
		]);
		var btnRollback = E('button', { 'class': 'cbi-button cbi-button-action', 'disabled': 'disabled' }, '回滚到该版本');

		var chkKeep = E('input', { 'type': 'checkbox', 'checked': st.keep_config ? 'checked' : null });
		var chkMirror = E('input', { 'type': 'checkbox', 'checked': st.use_mirror ? 'checked' : null });

		function setMsg(text, isError) {
			msgVal.textContent = text || '';
			msgVal.style.color = isError ? '#c00' : '#333';
		}

		function localImages() {
			var cur = st.current || '';
			return (st.images || []).filter(function(t) { return t && t !== cur; });
		}

		function refreshButtons() {
			var downloading = !!st.downloading || !!(st.operation && [ 'running', 'flashing', 'switching' ].indexOf(st.operation.state) >= 0);
			var disabled = st.enabled === false;
			btnCheck.disabled = disabled || downloading;
			var target = (latest && latest.latest) || st.downloading;
			var ready = !downloading && !!target && st.ota_ready === true;

			btnDownload.disabled = disabled || downloading || !latest;
			btnKeep.disabled = disabled || !ready;
			btnWipe.disabled = disabled || !ready;

			var imgs = localImages();
			btnRollback.disabled = disabled || imgs.length === 0 || downloading || st.ota_ready !== true;
			btnOfficial.disabled = disabled || downloading || !st.boot || !st.boot.ready;
		}

		function rebuildRollback() {
			var imgs = localImages();
			var keepSel = selRollback.value;

			selRollback.innerHTML = '';
			if (imgs.length === 0) {
				selRollback.appendChild(E('option', { 'value': '' }, '（无本地镜像，先下载一个版本）'));
				return;
			}
			imgs.forEach(function(t) {
				selRollback.appendChild(E('option', { 'value': t }, shortTag(t, prefix)));
			});
			if (imgs.indexOf(keepSel) >= 0)
				selRollback.value = keepSel;
		}

		function applyStatus(s) {
			st = s || {};
			prefix = st.tag_prefix || prefix;
			curVal.textContent = st.current || '未知';
			bootVal.textContent = st.boot && st.boot.ready ?
				('当前运行：第二系统；下次启动：' + (st.boot.system === '0' ? '第一系统（原厂）' : '第二系统') + '。') :
				((st.boot && st.boot.error) || st.error || '无法确认启动环境，系统切换和升级已禁用。');

			if (st.downloading) {
				var pct = st.download_size > 0 ? Math.floor(st.download_bytes * 100 / st.download_size) : 0;
				barFill.style.width = Math.max(0, Math.min(100, pct)) + '%';
				stateVal.textContent = '下载中 ' + pct + '%（' + mb(st.download_bytes) + ' / ' + mb(st.download_size) + '）';
			}
			else {
				barFill.style.width = '0%';
				stateVal.textContent = '空闲';
			}

			if (st.download_result) {
				var r = String(st.download_result);
				setMsg(r, r.indexOf('failed') === 0);
			}

			/* 缓存命中：进页面就能看到上次检查结果，不用等网络 */
			if (st.enabled !== false && st.cached && st.cached.latest) {
				latest = st.cached;
				newVal.textContent = st.cached.latest;
				sizeVal.textContent = mb(st.cached.size) + '  sha256 ' +
					String(st.cached.sha256 || '').substring(0, 12) + '…';
				noteVal.textContent = '上次检查：' + fmtTime(st.cached.checked_at) +
					(st.cached.update ? '（有新版本）' : '（已是最新）');
			}

			if (st.operation) {
				var op = st.operation;
				if (op.state === 'switching') stateVal.textContent = '正在切回第一系统，等待设备重启';
				if (op.state === 'running' || op.state === 'flashing')
					stateVal.textContent = op.state === 'flashing' ? '镜像校验通过，正在提交升级' : '后台校验/下载任务运行中';
				if (op.state === 'failed')
					setMsg(op.detail || '后台任务失败', true);
			}
			if (st.enabled === false) {
				latest = null;
				newVal.textContent = 'OTA 页面操作已关闭';
				noteVal.textContent = '可通过 UCI enabled 开关重新启用；命令行手动操作仍可用。';
			}
			rebuildRollback();
			refreshButtons();
		}

		btnCheck.addEventListener('click', function() {
			btnCheck.disabled = true;
			setMsg('正在检查 api.github.com …');
			callCheck().then(function(r) {
				btnCheck.disabled = false;
				if (!r || r.error || !r.latest) {
					newVal.textContent = '检查失败';
					setMsg((r && r.error) || '检查失败：无有效响应', true);
					return;
				}
				latest = r;
				newVal.textContent = r.latest;
				sizeVal.textContent = mb(r.size) + '  sha256 ' + String(r.sha256 || '').substring(0, 12) + '…';
				noteVal.textContent = '检查时间：' + fmtTime(r.checked_at);
				setMsg(r.update ? '发现新版本，可点击「下载并校验」' : '已是最新版本');
				refreshButtons();
			}).catch(function(e) {
				btnCheck.disabled = false;
				setMsg('检查失败：' + e, true);
			});
		});

		btnOfficial.addEventListener('click', function() {
			if (!confirm('切回第一系统（原厂）并重启？\n\n这会切换启动系统，当前连接将断开。原厂系统需要仍然完好，启动后的地址和密码以原厂配置为准。')) return;
			callOfficial(true).then(function(r) { taskAccepted(r, 'official'); }).catch(taskFailed);
		});

		btnDownload.addEventListener('click', function() {
			if (!latest) return;
			callDownload(latest.latest).then(function(r) { taskAccepted(r, 'download'); }).catch(taskFailed);
		});

		function confirmUpgrade(text) {
			return confirm(text + '\n\n校验通过后设备将重启，期间请勿断电。继续？');
		}

		function taskFailed(e) {
			setMsg('任务失败：' + e, true);
			refreshButtons();
		}

		function taskAccepted(r, action) {
			if (!r || r.error || !r.started) {
				taskFailed((r && r.error) || '无有效响应');
				return;
			}
			st.operation = { state: 'running', action: action || 'upgrade' };
			setMsg(action === 'official' ? '系统切换任务已启动，成功后设备将重启。' : action === 'download' ? '后台下载任务已启动，完成后会校验镜像。' : '后台任务已启动，正在校验；通过后才会提交升级。');
			refreshButtons();
		}

		function upgradeLatest(keep) {
			var tag = latest && latest.latest;
			if (!tag || !confirmUpgrade('将升级到 ' + tag + (keep ? '，保留当前配置。' : '，并清空所有配置。')))
				return;
			callUpgrade(keep, tag).then(taskAccepted).catch(taskFailed);
		}
		btnKeep.addEventListener('click', function() { upgradeLatest(true); });
		btnWipe.addEventListener('click', function() { upgradeLatest(false); });

		btnRollback.addEventListener('click', function() {
			var tag = selRollback.value;
			if (!tag || !confirmUpgrade('将回滚到 ' + shortTag(tag, prefix) + '，重新校验本地镜像，' +
				(chkKeep.checked ? '保留当前配置。' : '并清空配置。')))
				return;
			callUpgradeTag(tag, chkKeep.checked).then(taskAccepted).catch(taskFailed);
		});

		chkKeep.addEventListener('change', function() { callSettings(chkKeep.checked, chkMirror.checked); });
		chkMirror.addEventListener('change', function() { callSettings(chkKeep.checked, chkMirror.checked); });

		poll.add(function() {
			return callStatus().then(applyStatus);
		}, 3);

		applyStatus(st);

		return E([], [
			E('h2', {}, 'C8-688 固件升级与系统切换'),
			E('div', { 'class': 'cbi-section' }, [
				E('div', { 'class': 'cbi-value' }, [
					E('label', { 'class': 'cbi-value-title' }, '当前版本'),
					E('div', { 'class': 'cbi-value-field' }, curVal)
				]),
				E('div', { 'class': 'cbi-value' }, [
					E('label', { 'class': 'cbi-value-title' }, '最新版本'),
					E('div', { 'class': 'cbi-value-field' }, [ newVal, E('br'), sizeVal, noteVal ])
				]),
				E('div', { 'class': 'cbi-value' }, [
					E('label', { 'class': 'cbi-value-title' }, '状态'),
					E('div', { 'class': 'cbi-value-field' }, stateVal)
				]),
				E('div', { 'class': 'cbi-value' }, [
					E('label', { 'class': 'cbi-value-title' }, '进度'),
					E('div', { 'class': 'cbi-value-field' }, barWrap)
				]),
				E('div', { 'class': 'cbi-value' }, [
					E('label', { 'class': 'cbi-value-title' }, '操作'),
					E('div', { 'class': 'cbi-value-field' }, [
						btnCheck, ' ', btnDownload, ' ', btnKeep, ' ', btnWipe
					])
				]),
				E('div', { 'class': 'cbi-value' }, [
					E('label', { 'class': 'cbi-value-title' }, '回滚'),
					E('div', { 'class': 'cbi-value-field' }, [ selRollback, ' ', btnRollback ])
				]),
				msgVal,
				st.last_result ? E('div', { 'class': 'cbi-value-description' }, '上次升级结果：' + st.last_result) : ''
			]),
			E('div', { 'class': 'cbi-section' }, [
				E('h3', {}, '第一系统（原厂）'),
				bootVal,
				E('p', {}, 'OTA 只更新第二系统；此按钮选择已有的原厂系统后重启。'),
				btnOfficial
			]),
			E('div', { 'class': 'cbi-section' }, [
				E('h3', {}, '选项'),
				E('div', { 'class': 'cbi-value' }, [
					E('label', { 'class': 'cbi-value-title' }, '默认保留配置'),
					E('div', { 'class': 'cbi-value-field' }, chkKeep)
				]),
				E('div', { 'class': 'cbi-value' }, [
					E('label', { 'class': 'cbi-value-title' }, '下载走镜像'),
					E('div', { 'class': 'cbi-value-field' }, [
						chkMirror,
						E('div', { 'class': 'cbi-value-description' },
							'镜像只用于下载大文件；版本信息与 sha256 永远来自 api.github.com')
					])
				])
			]),
			E('div', { 'class': 'cbi-page-actions' }, [
				E('span', { 'class': 'cbi-value-description' },
					'命令行等价：c8-688-ota check / download / upgrade [--wipe] / upgrade --tag <TAG>')
			])
		]);
	},

	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});
