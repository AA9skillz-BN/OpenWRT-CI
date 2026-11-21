// SPDX-License-Identifier: GPL-3.0-only
// Only the new packaged template carries this marker. A retained Arthur or
// legacy configuration remains the authority, including explicit off choices.
function import_ap_defaults(cursor) {
	if (cursor.get(CONFIG, SECTION, 'ap3000m_import_firewall') == null)
		return false;
	cursor.delete(CONFIG, SECTION, 'ap3000m_import_firewall');
	if (boolval(cursor.get(CONFIG, SECTION, 'initialized')))
		return true;
	for (let key in [ 'sw_flow', 'hw_flow', 'sfe_flow', 'hw_wed', 'bbr_cca', 'fullcone_nat' ])
		if (cursor.get(CONFIG, SECTION, key) != null)
			return true;

	let sw = cursor.get(FWCONFIG, FWDEFAULTS, 'flow_offloading');
	let hw = cursor.get(FWCONFIG, FWDEFAULTS, 'flow_offloading_hw');
	let fc = cursor.get(FWCONFIG, FWDEFAULTS, 'fullcone');
	let fc6 = cursor.get(FWCONFIG, FWDEFAULTS, 'fullcone6');
	if (sw == '0' || sw == '1')
		cursor.set(CONFIG, SECTION, 'fastpath', sw == '1' ? 'flow_offloading' : 'disabled');
	if (hw == '0' || hw == '1')
		cursor.set(CONFIG, SECTION, 'fastpath_fo_hw', hw);
	if (fc == '0' || fc == '1') {
		let features = detect();
		cursor.set(CONFIG, SECTION, 'fullcone', fc == '0' ? '0' :
			(features.fullcone_nft ? '1' : (features.fullcone_broadcom ? '2' : '0')));
	}
	if (fc6 == '0' || fc6 == '1')
		cursor.set(CONFIG, SECTION, 'fullcone6', fc6);
	let cca = cursor.get(FWCONFIG, FWDEFAULTS, 'tcpcca');
	if (cca != null && index(read_cca(), cca) >= 0)
		cursor.set(CONFIG, SECTION, 'tcpcca', cca);
	let wed = find_wed();
	let value = wed != null ? read_text(wed) : null;
	if (value == 'Y' || value == 'N' || value == '1' || value == '0')
		cursor.set(CONFIG, SECTION, 'fastpath_mh_eth_hnat_wed',
			value == 'Y' || value == '1' ? '1' : '0');
	return true;
}
