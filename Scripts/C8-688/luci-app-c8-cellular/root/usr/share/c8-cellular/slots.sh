#!/bin/sh
# SPDX-License-Identifier: MIT
# Called under the inherited MT5700M action lock. The mux mapping comes from
# the inspected C8 reference script, and still requires physical acceptance.
SLOT_JOURNAL="${C8_SLOT_JOURNAL:-/etc/mt5700m/slot-rollback}"
slot_gpio() {
    local node name="$1" found=''
    node="${C8_GPIO_ROOT:-/sys/class/gpio}/cpe-sel$name/value"
    if [ -r "$node" ] && [ -w "$node" ]; then printf '%s\n' "$node"; return 0; fi
    for node in "${C8_GPIO_ROOT:-/sys/class/gpio}"/gpio*; do
        [ -f "$node/label" ] || continue
        [ "$(cat "$node/label")" = "cpe-sel$name" ] || continue
        [ -z "$found" ] || return 1
        found="$node/value"
    done
    [ -n "$found" ] && [ -r "$found" ] && [ -w "$found" ] || return 1
    printf '%s\n' "$found"
}
slot_at() { "$AT_HELPER" command "$1"; }
slot_snapshot() {
    local sci fun v0 v1
    SLOT_GPIO0="$(slot_gpio 0)" && SLOT_GPIO1="$(slot_gpio 1)" || return 1
    v0="$(cat "$SLOT_GPIO0")" && v1="$(cat "$SLOT_GPIO1")" || return 1
    case "$v0:$v1" in 0:0|0:1|1:0|1:1) ;; *) return 1;; esac
    sci="$(slot_at 'AT^SCICHG?')" || return 1
    fun="$(slot_at 'AT+CFUN?')" || return 1
    sci="$(printf '%s\n' "$sci" | sed -n 's/^\^SCICHG: *\([01]\), *\([01]\)\r*$/\1 \2/p')"
    fun="$(printf '%s\n' "$fun" | sed -n 's/^+CFUN: *\([01]\)\r*$/\1/p')"
    case "$sci:$fun" in '0 1:0'|'0 1:1'|'1 0:0'|'1 0:1') ;; *) return 1;; esac
    printf '%s %s %s %s\n' "$v0" "$v1" "$sci" "$fun"
}
slot_restore() {
    local v0="$1" v1="$2" s0="$3" s1="$4" fun="$5" actual
    printf '0\n' >"$SLOT_GPIO0" && printf '0\n' >"$SLOT_GPIO1" || return 1
    sleep 2 || return 1
    printf '%s\n' "$v0" >"$SLOT_GPIO0" && printf '%s\n' "$v1" >"$SLOT_GPIO1" || return 1
    slot_at "AT^SCICHG=$s0,$s1" >/dev/null || return 1
    slot_at 'AT^HVSST=1,1' >/dev/null || return 1
    slot_at "AT+CFUN=$fun" >/dev/null || return 1
    actual="$(slot_snapshot)" || return 1
    [ "$actual" = "$v0 $v1 $s0 $s1 $fun" ]
}
slot_recover() {
    local old
    [ -f "$SLOT_JOURNAL" ] || return 0
    old="$(cat "$SLOT_JOURNAL")" || return 1
    printf '%s\n' "$old" | grep -Eq '^[01] [01] [01] [01] [01]$' || return 1
    SLOT_GPIO0="$(slot_gpio 0)" && SLOT_GPIO1="$(slot_gpio 1)" || return 1
    # Journal is validated numbers only, never evaluated as shell code.
    slot_restore $old || { log_message 'SIM rollback unconfirmed; journal retained'; return 1; }
    rm -f "$SLOT_JOURNAL" && sync
}
slot_switch() (
    local target="$1" v0 v1 sci old expected actual
    case "$target" in
        0) v0=0; v1=1; sci='0,1';;
        6) v0=1; v1=1; sci='0,1';;
        1) v0=1; v1=1; sci='1,0';;
        *) return 64;;
    esac
    slot_recover || return 1
    old="$(slot_snapshot)" || return 1
    # Command substitutions execute in a subshell; rediscover paths here.
    SLOT_GPIO0="$(slot_gpio 0)" && SLOT_GPIO1="$(slot_gpio 1)" || return 1
    expected="$v0 $v1 $(printf '%s' "$sci" | tr ',' ' ') 1"
    [ "$old" != "$expected" ] || return 0
    umask 077
    mkdir -p "${SLOT_JOURNAL%/*}" || return 1
    printf '%s\n' "$old" >"$SLOT_JOURNAL.new" &&
        mv "$SLOT_JOURNAL.new" "$SLOT_JOURNAL" && sync || return 1
    trap 'slot_recover >/dev/null 2>&1 || true' EXIT
    trap 'exit 1' HUP INT TERM
    printf '0\n' >"$SLOT_GPIO0" && printf '0\n' >"$SLOT_GPIO1" || return 1
    sleep 2 || return 1
    printf '%s\n' "$v0" >"$SLOT_GPIO0" && printf '%s\n' "$v1" >"$SLOT_GPIO1" || return 1
    slot_at "AT^SCICHG=$sci" >/dev/null || return 1
    slot_at 'AT^HVSST=1,0' >/dev/null || return 1
    sleep 3 || return 1
    slot_at 'AT^HVSST=1,1' >/dev/null || return 1
    slot_at 'AT+CFUN=0' >/dev/null || return 1
    sleep 3 || return 1
    slot_at 'AT+CFUN=1' >/dev/null || return 1
    actual="$(slot_snapshot)" || return 1
    [ "$actual" = "$expected" ] || return 1
    rm -f "$SLOT_JOURNAL" && sync || return 1
    trap - EXIT HUP INT TERM
)
