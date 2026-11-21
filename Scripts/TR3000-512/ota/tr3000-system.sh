#!/bin/sh
# SPDX-License-Identifier: MIT
# Read-only checks for the maintained 512MB NAND layout. Never write MTD/U-Boot.
TR_BOARD=/tmp/sysinfo/board_name
TR_MTD=/proc/mtd
TR_DT=/proc/device-tree
TR_SYS_MTD=/sys/class/mtd
TR_SYS_UBI=/sys/class/ubi
TR_MOUNTS=/proc/mounts
TR_MOUNTINFO=/proc/self/mountinfo
TR_SYS_BLOCK=/sys/class/block

tr_error() { printf 'TR3000 OTA: %s\n' "$*" >&2; return 1; }

tr_check_layout() (
	[ "$(cat "$TR_BOARD" 2>/dev/null)" = 'cudy,tr3000-v1' ] || { tr_error '机型不是 Cudy TR3000 v1'; exit 1; }
	# Match both the kernel partition map and the actual running DT, since the
	# stock image uses the same board name with a different UBI capacity.
	part=$(awk '$4 == "\"ubi\"" {print $1, $2, $3}' "$TR_MTD") || exit 1
	set -- $part
	[ "$#" = 3 ] && [ "$2" = 1da40000 ] && [ "$3" = 00020000 ] || { tr_error 'UBI 不是已核对的 512MB 硬改布局'; exit 1; }
	mtd=${1%:}
	case "$mtd" in mtd[0-9]*) ;; *) exit 1;; esac
	[ "$(cat "$TR_SYS_MTD/$mtd/size" 2>/dev/null)" = 497287168 ] &&
	[ "$(cat "$TR_SYS_MTD/$mtd/erasesize" 2>/dev/null)" = 131072 ] &&
	[ "$(cat "$TR_SYS_MTD/$mtd/writesize" 2>/dev/null)" = 2048 ] || { tr_error 'NAND 容量、页或擦除块不匹配'; exit 1; }
	labels=$(find "$TR_DT" -name label -type f 2>/dev/null) || exit 1
	found=0
	while IFS= read -r label; do
		[ -n "$label" ] || continue
		[ "$(tr -d '\000' < "$label")" = ubi ] || continue
		reg=$(od -An -tx1 "${label%/label}/reg" 2>/dev/null | tr -d ' \n')
		[ "$reg" = 005c00001da40000 ] || { tr_error '运行设备树的 UBI 起点或长度不匹配'; exit 1; }
		found=$((found + 1))
	done <<EOF
$labels
EOF
	[ "$found" = 1 ] || { tr_error '无法确认唯一 UBI 设备树分区'; exit 1; }
	# Refuse initramfs and an unrelated/external root. The rootfs volume must
	# belong to the same MTD partition that was just checked.
	root=$(awk '$2 == "/rom" && $3 == "squashfs" {print $1}' "$TR_MOUNTS")
	case "$root" in
		/dev/ubiblock[0-9]*_[0-9]*) volume=ubi${root#/dev/ubiblock} ;;
		/dev/root)
			# The kernel can retain the /dev/root alias in /proc/mounts.
			# Resolve its mounted device number to an actual UBI block device.
			dev=$(awk '$5 == "/rom" {print $3}' "$TR_MOUNTINFO")
			[ -n "$dev" ] || exit 1
			volume=''
			for block in "$TR_SYS_BLOCK"/ubiblock[0-9]*_[0-9]*; do
				[ "$(cat "$block/dev" 2>/dev/null)" = "$dev" ] || continue
				[ -z "$volume" ] || exit 1
				block=${block##*/}; volume=ubi${block#ubiblock}
			done ;;
		ubi[0-9]*:rootfs)
			ubi=${root%:rootfs}; volume=''
			for name in "$TR_SYS_UBI/$ubi"_*/name; do
				[ "$(cat "$name" 2>/dev/null)" != rootfs ] || { [ -z "$volume" ] || exit 1; volume=${name%/name}; volume=${volume##*/}; }
			done ;;
		*) tr_error '未运行于已安装的 UBI squashfs 系统'; exit 1 ;;
	esac
	[ -n "$volume" ] || exit 1
	ubi=${volume%_*}
	[ "$(cat "$TR_SYS_UBI/$volume/name" 2>/dev/null)" = rootfs ] &&
	[ "$(cat "$TR_SYS_UBI/$ubi/mtd_num" 2>/dev/null)" = "${mtd#mtd}" ] || { tr_error '当前根文件系统不属于目标 UBI'; exit 1; }
)
