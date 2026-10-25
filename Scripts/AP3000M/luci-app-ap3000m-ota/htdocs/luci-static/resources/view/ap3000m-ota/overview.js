'use strict';
'require view';
'require poll';
'require rpc';
'require ui';

var callStatus = rpc.declare({ object: 'luci.ap3000m_ota', method: 'status', expect: {} });
var callCheck = rpc.declare({ object: 'luci.ap3000m_ota', method: 'check', expect: {} });
var callDownload = rpc.declare({ object: 'luci.ap3000m_ota', method: 'download', expect: {} });
var callUpgrade = rpc.declare({ object: 'luci.ap3000m_ota', method: 'upgrade', params: [ 'keep' ], expect: {} });
var callUpgradeTag = rpc.declare({ object: 'luci.ap3000m_ota', method: 'upgrade_tag', params: [ 'tag', 'keep' ], expect: {} });
var callSettings = rpc.declare({ object: 'luci.ap3000m_ota', method: 'set_settings', params: [ 'keep_config', 'use_mirror' ], expect: {} });

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
		var prefix = st.tag_prefix || 'AP3000M-';

		var curVal   = E('span', { 'class': 'label' }, st.current || '未知');
		var newVal   = E('span', { 'class': 'label' }, '尚未检查');
		var noteVal  = E('div', { 'class': 'cbi-value-description' }, '');
		var sizeVal  = E('span', {}, '—');
		var stateVal = E('span', {}, '空闲');
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
			var downloading = !!st.downloading;
			var target = (latest && latest.latest) || st.downloading;
			var ready = !downloading && !!target && (st.images || []).indexOf(target) >= 0;

			btnDownload.disabled = downloading || !latest;
			btnKeep.disabled = !ready;
			btnWipe.disabled = !ready;

			var imgs = localImages();
			btnRollback.disabled = imgs.length === 0 || downloading;
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
			if (st.cached && st.cached.latest) {
				latest = st.cached;
				newVal.textContent = st.cached.latest;
				sizeVal.textContent = mb(st.cached.size) + '  sha256 ' +
					String(st.cached.sha256 || '').substring(0, 12) + '…';
				noteVal.textContent = '上次检查：' + fmtTime(st.cached.checked_at) +
					(st.cached.update ? '（有新版本）' : '（已是最新）');
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

		btnDownload.addEventListener('click', function() {
			setMsg('已开始后台下载（走镜像，失败自动回落直连）…');
			callDownload().then(refreshButtons);
		});

		function confirmUpgrade(text) {
			return confirm(text + '\n\n设备会立即重启，期间请勿断电。继续？');
		}

		btnKeep.addEventListener('click', function() {
			if (!confirmUpgrade('将升级到 ' + (latest && latest.latest) + '，保留全部配置（WiFi/密码/FM350/风扇）。'))
				return;
			setMsg('正在提交升级，设备即将重启…');
			callUpgrade(true).then(function() {
				ui.addNotification(null, E('p', {}, '升级已提交，页面将失联约 3 分钟。'), 'info');
			});
		});

		btnWipe.addEventListener('click', function() {
			if (!confirmUpgrade('将升级到 ' + (latest && latest.latest) +
				'，并清空所有配置（WiFi 恢复 AP3000M/12345678，登录密码重置）。'))
				return;
			setMsg('正在提交升级（不保留配置），设备即将重启…');
			callUpgrade(false).then(function() {
				ui.addNotification(null, E('p', {}, '升级已提交，页面将失联约 3 分钟。'), 'info');
			});
		});

		btnRollback.addEventListener('click', function() {
			var tag = selRollback.value;
			if (!tag)
				return;
			if (!confirmUpgrade('将回滚到 ' + shortTag(tag, prefix) + '（使用本地已下载的镜像），' +
				(chkKeep.checked ? '保留当前配置。' : '并清空配置。')))
				return;
			setMsg('正在提交回滚，设备即将重启…');
			callUpgradeTag(tag, chkKeep.checked).then(function(r) {
				if (r && r.error)
					setMsg('回滚失败：' + r.error, true);
				else
					ui.addNotification(null, E('p', {}, '回滚已提交，设备即将重启。'), 'info');
			});
		});

		chkKeep.addEventListener('change', function() { callSettings(chkKeep.checked, chkMirror.checked); });
		chkMirror.addEventListener('change', function() { callSettings(chkKeep.checked, chkMirror.checked); });

		poll.add(function() {
			return callStatus().then(applyStatus);
		}, 3);

		applyStatus(st);

		return E([], [
			E('h2', {}, 'AP3000M 固件升级（OTA）'),
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
					'命令行等价：ap3000m-ota check / download / upgrade [--wipe] / upgrade --tag <TAG>')
			])
		]);
	},

	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});
