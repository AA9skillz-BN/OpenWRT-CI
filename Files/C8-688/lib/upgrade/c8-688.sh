#!/bin/sh
# Only the documented C8 eMMC second slot may be written. OEM slots stay intact.

c8_688_prepare_upgrade() {
	local kernel root
	[ "$(board_name)" = 'nradio,c8-688' ] || return 1
	kernel=$(find_mmc_part kernel_2nd)
	root=$(find_mmc_part rootfs_2nd)
	if [ "$kernel" != /dev/mmcblk0p8 ] || [ "$root" != /dev/mmcblk0p9 ] ||
	   [ ! -b "$kernel" ] || [ ! -b "$root" ]; then
		echo 'C8-688: second-slot partition layout differs; refusing upgrade' >&2
		return 1
	fi
	CI_ROOTDEV=mmcblk0
	CI_KERNPART=kernel_2nd
	CI_ROOTPART=rootfs_2nd
	# Overlay lives after squashfs inside rootfs_2nd, not an OEM data partition.
	CI_DATAPART=
	CI_DTBPART=
	EMMC_KERN_DEV=$kernel
	EMMC_ROOT_DEV=$root
	EMMC_DATA_DEV=
	EMMC_DTB_DEV=
	export CI_ROOTDEV CI_KERNPART CI_ROOTPART CI_DATAPART CI_DTBPART
	export EMMC_KERN_DEV EMMC_ROOT_DEV EMMC_DATA_DEV EMMC_DTB_DEV
}

c8_688_check_image() {
	local listing kernels roots kernel root kbytes rbytes ksectors rsectors value dirs
	c8_688_prepare_upgrade || return 1
	listing=$(tar tf "$1" 2>/dev/null) || return 1
	kernels=$(printf '%s\n' "$listing" | grep -E '^sysupgrade-[^/]+/kernel$')
	roots=$(printf '%s\n' "$listing" | grep -E '^sysupgrade-[^/]+/root$')
	[ "$(printf '%s\n' "$kernels" | grep -c .)" = 1 ] || return 1
	[ "$(printf '%s\n' "$roots" | grep -c .)" = 1 ] || return 1
	kernel=$kernels root=$roots
	[ "${kernel%/*}" = "${root%/*}" ] || return 1
	# emmc_upgrade_tar selects the first explicit sysupgrade directory entry.
	dirs=$(printf '%s\n' "$listing" | grep -E '^sysupgrade-.*/$')
	[ "$dirs" = "${kernel%/*}/" ] || return 1
	kbytes=$(tar xOf "$1" "$kernel" | wc -c)
	rbytes=$(tar xOf "$1" "$root" | wc -c)
	ksectors=$(cat /sys/class/block/mmcblk0p8/size) || return 1
	rsectors=$(cat /sys/class/block/mmcblk0p9/size) || return 1
	for value in "$kbytes" "$rbytes" "$ksectors" "$rsectors"; do
		case "$value" in ''|*[!0-9]*) return 1 ;; esac
	done
	[ "$kbytes" -gt 0 ] && [ "$kbytes" -le "$((ksectors * 512))" ] || return 1
	# Leave room for aligned overlay header and a configuration backup.
	[ "$rbytes" -gt 0 ] && [ "$((((rbytes + 65535) / 65536) * 128 + 2048))" -lt "$rsectors" ] || return 1
}

c8_688_do_upgrade() {
	c8_688_check_image "$1" || {
		echo 'C8-688: image/partition checks failed; no partitions written' >&2
		return 1
	}
	emmc_do_upgrade "$1"
}

c8_688_copy_config() {
	# emmc_upgrade_tar sets the aligned rootfs offset used by emmc_copy_config.
	[ "$EMMC_ROOT_DEV" = /dev/mmcblk0p9 ] && [ -z "$EMMC_DATA_DEV" ] || return 1
	case "${EMMC_ROOTFS_BLOCKS:-}" in ''|*[!0-9]*) return 1 ;; esac
	[ "$EMMC_ROOTFS_BLOCKS" -gt 0 ] || return 1
	emmc_copy_config
}
