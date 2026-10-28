'use strict';

import { popen, readfile } from 'fs';
import { cursor } from 'uci';

const CLI = '/usr/sbin/ap3000m-ota';

function run(args) {
	let p = popen(CLI + ' ' + args + ' 2>&1', 'r');
	if (!p)
		return { code: -1, output: '无法启动 OTA 命令' };
	let out = p.read('all') ?? '';
	let code = p.close();
	return { code: code, output: out };
}

function enabled() {
	let c = cursor();
	return c.get('ap3000m_ota', 'main', 'enabled') != '0';
}

function start(args) {
	if (!enabled())
		return { error: 'OTA 页面操作已关闭；仍可在命令行手动执行' };
	let r = run(args);
	if (r.code != 0 || trim(r.output) != 'started')
		return { error: trim(r.output) || '后台任务启动失败', started: false };
	return { started: true };
}

function valid_tag(tag) {
	return length(tag) > 0 && match(tag, /^[A-Za-z0-9._-]+$/);
}

function parse_json(s, fallback) {
	if (!s || length(trim(s)) == 0)
		return fallback;

	try {
		return json(s);
	}
	catch (e) {
		return fallback;
	}
}

function current_version() {
	let t = readfile('/etc/ap3000m-build.tag');

	return t ? trim(t) : null;
}

return {
	'luci.ap3000m_ota': {
		status: {
			call: function() {
				let st = parse_json(run('ui-status').output, null) ?? {};
				st['build_tag'] = current_version();
				return st;
			}
		},

		check: {
			call: function() {
				if (!enabled())
					return { error: 'OTA 页面操作已关闭' };
				let result = run('check --json');
				let raw = result.output;
				let r = result.code == 0 ? parse_json(raw, null) : null;

				if (!r)
					return { error: raw ? trim(raw) : '检查失败：无输出（检查 DNS / 网络）' };

				return r;
			}
		},

		download: {
			args: { tag: '' },
			call: function(req) {
				let tag = req.args.tag ?? '';
				if (tag != '' && !valid_tag(tag))
					return { error: '非法版本号' };
				return start('download --background' + (tag ? ' --tag ' + tag : ''));
			}
		},

		upgrade: {
			args: { keep: true, tag: '' },
			call: function(req) {
				let mode = (req.args.keep == true) ? '--keep' : '--wipe';
				let tag = req.args.tag ?? '';
				if (tag != '' && !valid_tag(tag))
					return { error: '非法版本号' };
				return start('upgrade --background ' + mode + ' --yes' + (tag ? ' --tag ' + tag : ''));
			}
		},

		/* 指定版本（回滚）：只允许 tag 形状，防止把参数拼进 shell */
		upgrade_tag: {
			args: { tag: '', keep: true },
			call: function(req) {
				let tag = req.args.tag ?? '';

				if (!valid_tag(tag))
					return { error: '非法版本号' };

				let mode = (req.args.keep == true) ? '--keep' : '--wipe';
				return start('upgrade --background --tag ' + tag + ' ' + mode + ' --yes');
			}
		},

		set_settings: {
			args: { keep_config: true, use_mirror: true },
			call: function(req) {
				let c = cursor();
				c.set('ap3000m_ota', 'main', 'keep_config', req.args.keep_config ? '1' : '0');
				c.set('ap3000m_ota', 'main', 'use_mirror', req.args.use_mirror ? '1' : '0');
				c.commit('ap3000m_ota');
				return {};
			}
		}
	}
};
