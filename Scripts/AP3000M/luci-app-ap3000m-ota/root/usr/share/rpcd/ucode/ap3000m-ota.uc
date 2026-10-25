'use strict';

import { popen, readfile } from 'fs';
import { cursor } from 'uci';

const CLI = '/usr/sbin/ap3000m-ota';

function run(args) {
	let p = popen(CLI + ' ' + args + ' 2>/dev/null', 'r');

	if (!p)
		return null;

	let out = p.read('all') ?? '';
	p.close();

	return out;
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
				let st = parse_json(run('ui-status'), null) ?? {};
				st['build_tag'] = current_version();
				return st;
			}
		},

		check: {
			call: function() {
				let raw = run('check --json');
				let r = parse_json(raw, null);

				if (!r)
					return { error: raw ? trim(raw) : '检查失败：无输出（检查 DNS / 网络）' };

				return r;
			}
		},

		download: {
			call: function() {
				run('download --background');
				return { started: true };
			}
		},

		upgrade: {
			args: { keep: true },
			call: function(req) {
				let mode = (req.args.keep == true) ? '--keep' : '--wipe';
				run('upgrade ' + mode + ' --yes');
				return { started: true };
			}
		},

		/* 指定版本（回滚）：只允许 tag 形状，防止把参数拼进 shell */
		upgrade_tag: {
			args: { tag: '', keep: true },
			call: function(req) {
				let tag = req.args.tag ?? '';

				if (length(tag) == 0 || !match(tag, /^[A-Za-z0-9._-]+$/))
					return { error: '非法版本号' };

				let mode = (req.args.keep == true) ? '--keep' : '--wipe';
				run('upgrade --tag ' + tag + ' ' + mode + ' --yes');
				return { started: true };
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
