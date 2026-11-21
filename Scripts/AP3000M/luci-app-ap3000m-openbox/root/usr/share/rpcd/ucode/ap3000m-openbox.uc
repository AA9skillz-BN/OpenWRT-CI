'use strict';
import { popen } from 'fs';

function run(args) {
	let p = popen('/usr/sbin/ap3000m-openbox ' + args + ' 2>&1', 'r');
	if (!p) return { code: -1, output: '无法启动安装入口' };
	let output = p.read('all') ?? '';
	return { code: p.close(), output: output };
}

return {
	'luci.ap3000m_openbox': {
		status: {
			call: function() {
				let r = run('status');
				if (r.code == 0) {
					try { return json(r.output); } catch (e) {}
				}
				return { error: '无法读取安装状态' };
			}
		},
		install: {
			args: { mirror: false },
			call: function(req) {
				let r = run('install --background' + (req.args.mirror == true ? ' --mirror' : ''));
				return r.code == 0 && trim(r.output) == 'started'
					? { started: true } : { error: trim(r.output) || '安装任务启动失败' };
			}
		}
	}
};
