'use strict';
'require view';
'require rpc';
'require poll';
'require ui';

var callStatus = rpc.declare({ object: 'luci.ap3000m_openbox', method: 'status', expect: {} });
var callInstall = rpc.declare({ object: 'luci.ap3000m_openbox', method: 'install', params: [ 'mirror' ], expect: {} });

return view.extend({
	load: function() { return callStatus(); },
	render: function(initial) {
		var self = this, status = E('p'), message = E('p');
		var mirror = E('input', { type: 'checkbox' });
		var panel = E('a', { class: 'cbi-button cbi-button-neutral', target: '_blank', rel: 'noopener noreferrer' }, '打开 Open-Box 面板');
		var button = E('button', { class: 'cbi-button cbi-button-apply', click: function() {
			ui.showModal('安装或恢复 Open-Box', [
				E('p', {}, '将从 Open-Box 官方项目下载并运行安装程序，安装最新版官方发行包。已有数据会保留；安装完成后请在面板中确认配置。'),
				E('div', { class: 'right' }, [
					E('button', { class: 'cbi-button', click: ui.hideModal }, '取消'), ' ',
					E('button', { class: 'cbi-button cbi-button-positive', click: function() {
						ui.hideModal(); button.disabled = true;
						callInstall(mirror.checked).then(function(r) {
							if (!r.started) throw new Error(r.error || '启动失败');
							return callStatus().then(update);
						}).catch(function(e) { button.disabled = false; ui.addNotification(null, E('p', {}, e.message)); });
					} }, '开始安装')
				])
			]);
		} }, '安装 / 恢复官方版本');
		function update(st) {
			if (st.error) { status.textContent = st.error; button.disabled = true; panel.style.display = 'none'; return; }
			status.textContent = st.installed ? '已安装' + (st.version ? '：' + st.version : '') :
				(st.data_present ? '程序未安装，检测到保留的数据；可安装恢复程序' : '尚未安装；没有检测到可恢复的数据');
			message.textContent = st.busy ? ((st.operation || {}).message || '安装中，请稍候') : ((st.operation || {}).message || '');
			button.disabled = !!st.busy || !!st.installed || !!self.readonly;
			mirror.disabled = !!st.busy || !!self.readonly;
			panel.style.display = st.installed ? '' : 'none';
			if (st.installed) {
				var address = new URL(window.location.href);
				address.protocol = 'http:';
				address.port = String(st.port);
				address.pathname = '/'; address.search = ''; address.hash = '';
				panel.href = address.toString();
			}
		}
		update(initial || {});
		poll.add(function() { return callStatus().then(update); }, 3);
		return E('div', { class: 'cbi-map' }, [
			E('h2', {}, 'Open-Box 安装与恢复'),
			E('p', {}, '固件提供官方安装入口。首次安装需要联网；软件更新在 Open-Box 面板中完成。'),
			status, message,
			E('p', {}, [ E('label', {}, [ mirror, ' 下载失败时使用 gh-proxy.com 镜像，并通过镜像下载官方安装包' ]) ]),
			E('p', {}, [ button, ' ', panel ]),
			E('p', {}, '保留配置升级会保留 /opt/open-box/data/ 中的订阅、规则和面板设置，程序需要在升级后重新安装。清空配置升级不会保留这些数据。'),
			E('p', {}, '此前未备份而已经丢失的数据无法通过重新安装找回。建议同时使用 Open-Box 自带的导出备份。')
		]);
	},
	handleSave: null, handleSaveApply: null, handleReset: null
});
