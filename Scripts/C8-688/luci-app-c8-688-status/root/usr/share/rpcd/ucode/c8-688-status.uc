'use strict';
// SPDX-License-Identifier: MIT
// Explicit status fields only: never return UCI, modem identities, addresses or logs.
import { readfile, readlink, glob } from 'fs';
import { connect } from 'ubus';

function text(path) {
	let value = readfile(path);
	return value == null ? null : trim(value);
}

function number(path, low, high) {
	let value = text(path);
	if (value == null || !match(value, /^-?[0-9]+$/)) return null;
	value = +value;
	return value >= low && value <= high ? value : null;
}

function netname(value) {
	return type(value) == 'string' && length(value) <= 15 &&
		match(value, /^[A-Za-z0-9_][A-Za-z0-9_.:-]*$/);
}

function routes(path, ipv6) {
	let result = [], raw = text(path);
	if (raw == null) return null;
	for (let line in split(raw, '\n')) {
		let fields = split(trim(line), /\s+/);
		let name, metric, flags;
		if (ipv6) {
			if (length(fields) != 10 || fields[0] != '00000000000000000000000000000000' || fields[1] != '00') continue;
			name = fields[9]; metric = hex(fields[5]); flags = hex(fields[8]);
		}
		else {
			if (length(fields) < 8 || fields[1] != '00000000' || fields[7] != '00000000') continue;
			name = fields[0]; metric = +fields[6]; flags = hex(fields[3]);
		}
		// RTF_UP and not RTF_REJECT; an unreachable default is not a usable route.
		if (netname(name) && (flags & 1) && !(flags & 512))
			push(result, { device: name, metric });
	}
	return result;
}

function device(name, interfaces) {
	let path = '/sys/class/net/' + name + '/';
	let state = text(path + 'operstate');
	let parent = readlink(path + 'master');
	let roles = [];
	for (let item in interfaces)
		if (item.device == name) push(roles, item.name);
	return {
		name, present: state != null, state,
		carrier: number(path + 'carrier', 0, 1),
		speed_mbps: number(path + 'speed', 1, 1000000),
		duplex: text(path + 'duplex'),
		bridge: parent ? split(parent, '/')[-1] : null,
		interfaces: roles,
		rx_bytes: number(path + 'statistics/rx_bytes', 0, 9007199254740991),
		tx_bytes: number(path + 'statistics/tx_bytes', 0, 9007199254740991),
		rx_errors: number(path + 'statistics/rx_errors', 0, 9007199254740991),
		tx_errors: number(path + 'statistics/tx_errors', 0, 9007199254740991)
	};
}

function snapshot() {
	let connection = connect();
	let board, network;
	try {
		board = connection?.call('system', 'board', {});
		network = connection?.call('network.interface', 'dump', {});
	}
	catch (e) {
		// Keep unavailable data unknown; do not return an exception or raw ubus data.
	}
	connection?.disconnect();
	if (board?.board_name != 'nradio,c8-688')
		return { error: board ? 'unsupported_board' : 'board_status_unavailable' };

	let interfaces = [], names = [ 'eth1', 'lan1', 'lan2', 'lan3', 'lan4' ];
	let network_ok = type(network?.interface) == 'array';
	for (let item in network_ok ? network.interface : []) {
		if (item.interface == 'loopback') continue;
		let name = item.l3_device ?? item.device;
		if (netname(name) && index(names, name) < 0) push(names, name);
		push(interfaces, {
			name: item.interface, device: netname(name) ? name : null,
			up: type(item.up) == 'bool' ? item.up : null,
			pending: type(item.pending) == 'bool' ? item.pending : null,
			proto: item.proto,
			ipv4_addresses: length(item['ipv4-address'] ?? []),
			ipv6_addresses: length(item['ipv6-address'] ?? []),
			ipv6_prefixes: length(item['ipv6-prefix'] ?? [])
		});
	}
	let devices = [];
	for (let name in names) push(devices, device(name, interfaces));

	let sensors = [], cpu = null, fans = [];
	for (let directory in glob('/sys/class/hwmon/hwmon*') ?? []) {
		let name = text(directory + '/name');
		for (let path in glob(directory + '/temp*_input') ?? []) {
			let temperature = number(path, -40000, 200000);
			push(sensors, { name, input: split(path, '/')[-1], millidegrees: temperature });
			if (name == 'cpu_thermal' && temperature != null)
				cpu = cpu == null ? temperature : max(cpu, temperature);
		}
		if (name == 'pwmfan') push(fans, {
			name, pwm: number(directory + '/pwm1', 0, 255),
			rpm: number(directory + '/fan1_input', 0, 100000)
		});
	}
	// Do not mistake either GPY211 PHY's temperature for the CPU temperature.
	if (cpu == null) for (let directory in glob('/sys/class/thermal/thermal_zone*') ?? []) {
		if (text(directory + '/type') != 'cpu-thermal') continue;
		let value = number(directory + '/temp', -40000, 200000);
		if (value != null) cpu = cpu == null ? value : max(cpu, value);
	}

	let usb = [];
	for (let directory in glob('/sys/bus/usb/devices/*') ?? []) {
		let port = split(directory, '/')[-1];
		// Physical devices only. A root hub's 20 Gbit/s does not describe a 480 Mbit/s modem.
		if (!match(port, /^[0-9]+-[0-9.]+$/)) continue;
		let vendor = text(directory + '/idVendor'), product = text(directory + '/idProduct');
		if (!vendor || !product) continue;
		push(usb, { port, vendor, product, speed_mbps: number(directory + '/speed', 1, 1000000) });
	}
	let cmdline = text('/proc/cmdline') ?? '';
	let warnings = [];
	if (!network_ok) push(warnings, 'network_status_unavailable');
	if (cpu == null) push(warnings, 'cpu_temperature_unknown');
	else if (cpu >= 75000) push(warnings, 'cpu_temperature_high');
	if (!length(fans) || fans[0].pwm == null) push(warnings, 'fan_pwm_unknown');
	return {
		schema_version: 1,
		board: {
			model: board.model, board_name: board.board_name, kernel: board.kernel,
			firmware: board.release?.description,
			build_tag: text('/etc/c8-688-build.tag')
		},
		boot: { second_slot_running: !!match(cmdline, /(^|\s)root=PARTLABEL=rootfs_2nd(\s|$)/), environment_checked: false },
		network: { available: network_ok, interfaces, devices,
			ipv4_defaults: routes('/proc/net/route', false), ipv6_defaults: routes('/proc/net/ipv6_route', true),
			internet_tested: false, forwarding_tested: false },
		thermal: { cpu_millidegrees: cpu, sensors, fans },
		usb, warnings
	};
}

return { 'luci.c8_688_status': { status: { call: snapshot } } };
