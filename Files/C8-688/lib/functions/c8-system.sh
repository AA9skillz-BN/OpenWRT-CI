#!/bin/sh
# SPDX-License-Identifier: MIT
# Shared second-slot/environment guards; loading and status never write.
C8_BOARD=/tmp/sysinfo/board_name
C8_CMDLINE=/proc/cmdline
C8_BLOCK=/sys/class/block
C8_DEV=/dev
C8_ENV_CONF=/etc/fw_env.config
C8_LOCK=/var/lock/c8-system.lock

c8_error() { printf 'C8-688: %s\n' "$*" >&2; return 1; }

c8_check_platform() {
 [ "$(cat "$C8_BOARD" 2>/dev/null)" = 'nradio,c8-688' ] ||
  { c8_error '机型不匹配，拒绝操作'; return 1; }
 local roots part
 roots=$(tr ' ' '\n' < "$C8_CMDLINE" | sed -n 's/^root=//p')
 case "$roots" in PARTLABEL=rootfs_2nd|/dev/mmcblk0p9) ;;
  *) c8_error '当前未从已确认的第二系统启动，拒绝操作'; return 1 ;;
 esac
 for part in 'mmcblk0p2:u-boot-env' 'mmcblk0p8:kernel_2nd' 'mmcblk0p9:rootfs_2nd'; do
  [ -b "$C8_DEV/${part%%:*}" ] &&
   grep -qx "PARTNAME=${part#*:}" "$C8_BLOCK/${part%%:*}/uevent" ||
   { c8_error "分区布局不匹配：${part#*:}"; return 1; }
 done
}

c8_read_boot_system() {
 c8_check_platform || return 1
 command -v fw_printenv >/dev/null 2>&1 || { c8_error '缺少 uboot-envtools'; return 1; }
 # Single reviewed p2 environment at zero; fw_printenv must validate its CRC.
 awk '
  /^[[:space:]]*(#|$)/ { next }
  { n++; if (NF != 3 || $1 != "/dev/mmcblk0p2" || $2 != "0x0" ||
    $3 !~ /^(0x2000|0x4000|0x8000|0x10000|0x20000|0x80000)$/) bad=1 }
  END { exit (n != 1 || bad) }
 ' "$C8_ENV_CONF" 2>/dev/null || { c8_error '启动环境配置未通过检查，请先探测 env'; return 1; }
 local current
 current=$(fw_printenv -c "$C8_ENV_CONF" -n boot_system 2>/dev/null) ||
  { c8_error '无法读取 boot_system（CRC 或 env 大小不匹配）'; return 1; }
 case "$current" in 0|1) printf '%s\n' "$current" ;;
  *) c8_error 'boot_system 不是 0 或 1，拒绝操作'; return 1 ;;
 esac
}

c8_lock() {
 mkdir -p "${C8_LOCK%/*}" || return 1
 exec 9>"$C8_LOCK"
 flock -n 9 || { c8_error '已有下载、升级或系统切换任务运行中'; return 1; }
}

c8_select_system() {
 local target="$1" current now
 case "$target" in 0|1) ;; *) return 2 ;; esac
 current=$(c8_read_boot_system) || return 1
 command -v fw_setenv >/dev/null 2>&1 || return 1
 if [ "$current" != "$target" ]; then
  fw_setenv -c "$C8_ENV_CONF" boot_system "$target" ||
   { c8_error '写入 boot_system 失败，未请求重启'; return 1; }
 fi
 now=$(c8_read_boot_system) || return 1
 [ "$now" = "$target" ] || { c8_error '写后读回不一致，未请求重启'; return 1; }
 printf '已选择系统 %s，3 秒后重启。\n' "$target"
 sync
 sleep 3
 reboot || { c8_error '重启失败；下次启动将进入所选系统'; return 1; }
}
