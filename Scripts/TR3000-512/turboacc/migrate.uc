// SPDX-License-Identifier: GPL-3.0-only
// TR3000 migration from chenmozhijin TurboACC; never rewrite network or zones.
function migrate_settings(cursor) {
	// Arthur owns the JSON exec trigger; retaining the old UCI init trigger
	// would invoke apply twice. Leave unrelated or explicitly custom triggers.
	let trigger_changed = false;
	cursor.foreach('ucitrack', 'turboacc', function(section) {
		if (section.init == 'turboacc' && section.exec == null) {
			cursor.delete('ucitrack', section['.name']);
			trigger_changed = true;
		}
	});
	if (trigger_changed)
		cursor.commit('ucitrack');

	ensure_section(cursor);
	let legacy = [ 'sw_flow', 'hw_flow', 'sfe_flow', 'hw_wed', 'bbr_cca', 'fullcone_nat' ];
	let old = {};
	for (let key in legacy)
		old[key] = cursor.get(CONFIG, SECTION, key);

	// Modern settings (including a deliberate disabled choice) take precedence.
	let changed = false;
	if (cursor.get(CONFIG, SECTION, 'fastpath') == null) {
		let features = detect();
		let values = {
			fastpath: boolval(old.sw_flow) ? 'flow_offloading' : (boolval(old.sfe_flow) ? 'fast_classifier' : 'disabled'),
			fastpath_fo_hw: boolval(old.hw_flow) ? '1' : '0',
			fastpath_fo_hw_ipv6: '1',
			fastpath_fc_br: '1',
			fastpath_fc_ipv6: '1',
			fastpath_mh_eth_hnat: '1',
			fastpath_mh_eth_hnat_v6: '1',
			fastpath_mh_eth_hnat_wed: boolval(old.hw_wed) ? '1' : '0',
			fullcone: !boolval(old.fullcone_nat) ? '0' : (features.fullcone_nft ? '1' : (features.fullcone_broadcom ? '2' : '0')),
			fullcone6: '0',
			tcpcca: boolval(old.bbr_cca) ? 'bbr' : 'cubic'
		};
		for (let key in values)
			if (cursor.get(CONFIG, SECTION, key) == null)
				cursor.set(CONFIG, SECTION, key, values[key]);
		changed = true;
	}
	for (let key in legacy) {
		if (old[key] != null) {
			cursor.delete(CONFIG, SECTION, key);
			changed = true;
		}
	}
	// Stop the upstream one-time hw adoption from undoing a migrated hw=0.
	if (cursor.get(CONFIG, SECTION, 'fastpath_fo_hw_applied') == null) {
		cursor.set(CONFIG, SECTION, 'fastpath_fo_hw_applied',
			boolval(cursor.get(CONFIG, SECTION, 'fastpath_fo_hw')) ? '1' : '0');
		changed = true;
	}
	if (!boolval(cursor.get(CONFIG, SECTION, 'initialized'))) {
		cursor.set(CONFIG, SECTION, 'initialized', '1');
		changed = true;
	}
	if (changed)
		cursor.commit(CONFIG);
}
