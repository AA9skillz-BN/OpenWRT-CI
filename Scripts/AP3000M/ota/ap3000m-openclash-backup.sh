# Sourced by sysupgrade after it selects the backup list builder.
ap3000m_filter_bundled_core() {
	local list="$1" core=/etc/openclash/core/clash_meta live_meta rom_meta
	[ -f "$list" ] && [ -f "$core" ] && [ -f "/rom$core" ] || return 0
	[ "$(readlink -f "$core")" = "$core" ] || return 0
	live_meta=$(ls -nd "$core" | awk 'NF >= 4 { print $1, $3, $4 }') || return 0
	rom_meta=$(ls -nd "/rom$core" | awk 'NF >= 4 { print $1, $3, $4 }') || return 0
	[ -n "$live_meta" ] && [ "$live_meta" = "$rom_meta" ] && cmp -s "$core" "/rom$core" || return 0
	sed -i '\|^/\?etc/openclash/core/clash_meta$|d' "$list"
}

case "${sysupgrade_init_conffiles:-}" in
	build_list_of_backup_config_files|build_list_of_backup_overlay_files)
		append sysupgrade_init_conffiles ap3000m_filter_bundled_core ;;
esac
