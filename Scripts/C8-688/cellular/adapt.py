#!/usr/bin/env python3
"""SHA-guarded C8 adaptations. Never run either upstream installer."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
REVIEWED = json.loads((ROOT / 'reviewed-inputs.json').read_text(encoding='utf-8'))


def replace_once(value, before, after):
    if value.count(before) != 1:
        raise ValueError('C8 cellular patch anchor drift')
    return value.replace(before, after)


def originals(tree, hashes):
    result = {}
    for name, digest in hashes.items():
        backup = tree / '.c8-cellular-originals' / (name + '.original')
        value = backup.read_bytes() if backup.exists() else (tree / name).read_bytes()
        if hashlib.sha256(value).hexdigest() != digest:
            raise ValueError(f'C8 cellular reviewed source drift: {name}')
        result[name] = value
    return result


def adapt(manager, web):
    original = originals(manager, REVIEWED['manager'])
    woriginal = originals(web, REVIEWED['web'])
    actual_assets = {p.relative_to(web).as_posix() for p in (web / 'files/www/5700').rglob('*') if p.is_file()}
    if actual_assets != set(REVIEWED['web']) - {'Makefile'}:
        raise ValueError('C8 WEBUI asset list drift')
    changed, wchanged = dict(original), dict(woriginal)
    changed['Makefile'] = replace_once(original['Makefile'], b'PKG_RELEASE:=1', b'PKG_RELEASE:=2')
    name = 'root/usr/sbin/mt5700m-manager'
    source = original[name].decode('utf-8')
    source = replace_once(source, 'action="${1:-status-json}"',
                          '. /usr/share/c8-cellular/network.sh\n\naction="${1:-status-json}"')
    if source.count('recover_profile_transaction || exit 1') != 2:
        raise ValueError('manager recovery anchor drift')
    if source.count('mt5700m_action_lock || exit $?') != 2:
        raise ValueError('manager lock anchor drift')
    source = source.replace('mt5700m_action_lock || exit $?',
                            'export C8_SLOT_RECOVERY=1\n\t\tmt5700m_action_lock || exit $?')
    changed[name] = source.replace('recover_profile_transaction || exit 1',
                                   'slot_recover && recover_profile_transaction || exit 1\n\t\tunset C8_SLOT_RECOVERY').encode()
    changed['root/etc/uci-defaults/90-mt5700m'] = (
        b'#!/bin/sh\n# C8 owns create-only migration; preserve network and operator switches.\nexit 0\n')
    name = 'root/usr/share/mt5700m/profile.sh'
    source = original[name].decode('utf-8')
    source = replace_once(source, 'uci -q set "mt5700m.connection.enabled=$enabled" &&',
                          'uci -q set "mt5700m.connection.dial_mode=$mode" &&\n    uci -q set "mt5700m.connection.enabled=$enabled" &&')
    source = replace_once(source, '    rm -f "$PROFILE_JOURNAL"\n    trap - EXIT',
                          '    profile_restore_uci "$desired" || return 1\n    rm -f "$PROFILE_JOURNAL"\n    trap - EXIT')
    source = replace_once(source, '[ "$old" != "$desired" ] || return 0',
                          '[ "$old" != "$desired" ] || { profile_restore_uci "$desired"; return $?; }')
    query = '''$("${AT_HELPER}" command 'AT^SETAUTODIAL?' 2>/dev/null | sed -n 's/^\\^SETAUTODIAL: *//p' | tr -d '\\r')'''
    if source.count(query) != 3:
        raise ValueError('profile query anchor drift')
    source = source.replace(query + '"', '$(c8_profile_query)" || return 1')
    source = source.replace('    sync\n', '    sync || return 1\n')
    source = source.replace('    sleep 1\n', '    sleep 1 || return 1\n')
    source = source.replace('    rm -f "$PROFILE_JOURNAL"\n',
                            '    rm -f "$PROFILE_JOURNAL" && sync || return 1\n')
    source += '''
c8_profile_query() {
    local reply
    reply="$("${AT_HELPER}" command 'AT^SETAUTODIAL?' 2>/dev/null)" || return 1
    printf '%s\\n' "$reply" | sed -n 's/^\\^SETAUTODIAL: *//p' | tr -d '\\r'
}
'''
    changed[name] = source.encode()
    name = 'root/usr/share/mt5700m/lock.sh'
    changed[name] = replace_once(original[name], b'\texport MT5700M_ACTION_LOCKED=1', b'''\t# A killed slot operation leaves a durable recovery journal. Only the
\t# broker/manager recovery entry points may proceed until it is restored.
\tif { [ -f /etc/mt5700m/slot-rollback ] || [ -f /etc/mt5700m/profile-rollback ]; } && [ "${C8_SLOT_RECOVERY:-0}" != 1 ]; then
\t\techo 'Modem rollback pending; use cellular recovery entry point' >&2
\t\texec 9>&-
\t\treturn 75
\tfi
\texport MT5700M_ACTION_LOCKED=1''')
    name = 'root/usr/share/luci/menu.d/luci-app-mt5700m.json'
    menu = json.loads(original[name])
    old, new = 'admin/modem/mt5700m', 'admin/modem/maintenance'
    menu.pop('admin/modem')
    menu = {k.replace(old, new, 1): v for k, v in menu.items()}
    menu[new].update(title='兼容维护', order=90)
    changed[name] = (json.dumps(menu, ensure_ascii=False, indent=2) + '\n').encode()

    wchanged['Makefile'] = (ROOT / 'webui.Makefile').read_bytes()
    name = 'files/www/5700/umi.ec9b4b52.js'
    main = woriginal[name].decode('utf-8')
    main = replace_once(main, '"commandTimeout",1e3', '"commandTimeout",240e3')
    main = replace_once(main, '"maxReconnectAttempts",3', '"maxReconnectAttempts",0')
    main = replace_once(main, 'key:"clearPendingCommands",value:function(S){this.pendingCommands',
        'key:"clearPendingCommands",value:function(S){if(this.smsCollecting){'
        'clearTimeout(this.smsTimer);this.smsResolve&&this.smsResolve({success:false,error:S});'
        'this.smsCollecting=false;this.smsResolve=null;this.smsBuffer=[];this.smsTimer=null}'
        'this.pendingCommands')
    main = replace_once(main, '},1e4);var J=N.endsWith', '},240e3);var J=N.endsWith')
    # The transport is shared with the original manager. Use serialized queries
    # instead of enabling a continuous unsolicited stream with no sole reader.
    main = replace_once(main,
        'B=N?"AT^PDCPDATAINFO=1".concat(L?",".concat(L):""):"AT^PDCPDATAINFO=0",oe.abrupt("return",this.sendCommand(B))',
        'oe.abrupt("return",window.c8PDCPReport(N,L))')
    # Complete JSON results must not be interpreted as SMS payload substrings.
    main = replace_once(main, 'if(this.smsCollecting){try{var B=S;',
        'if(this.smsCollecting){try{var c8=JSON.parse(S);if(typeof c8.success==="boolean")'
        '{clearTimeout(this.smsTimer);this.smsResolve&&this.smsResolve(c8);'
        'this.smsCollecting=!1;this.smsResolve=null;this.smsBuffer=[];this.smsTimer=null;return}}'
        'catch(c8){}try{var B=S;')
    # Remove unused demo REST mocks: no fabricated data or success responses.
    start, end = main.index('_r().setup({timeout:"100-600"})'), main.index('var Ll=!1,Nl=')
    main = main[:start] + main[end:]
    wchanged[name] = main.encode()
    name = 'files/www/5700/p__CPE__Network__Info__index.30901ff8.async.js'
    network = woriginal[name].decode('utf-8')
    network = replace_once(network, 'min:200,max:65535,step:100,value:xn',
                           'min:5000,max:65535,step:1000,value:xn')
    network = network.replace('useState)(500)', 'useState)(5000)')
    network = replace_once(network, r'200-65535ms', r'5000-65535ms')
    wchanged[name] = network.encode()
    name = 'files/www/5700/p__CPE__System__Info__index.a04926af.async.js'
    info = woriginal[name].decode('utf-8')
    # The original server-address card describes a daemon we do not install.
    # Point users to the actual retained transport settings instead.
    card = '(0,e.jsx)(D.Z,{xs:24,sm:24,md:12,lg:12,children:(0,e.jsx)($.Z,{title:'
    start = info.index(card + '"AT')
    end = info.index(card, start + len(card))
    info = (info[:start] + card + '"模组连接",style:{marginBottom:"16px",height:"100%"},'
            'children:(0,e.jsxs)("div",{children:["使用当前已登录的管理会话。串口与网络 AT 连接参数请在兼容维护中设置。",'
            '(0,e.jsx)("p",{children:(0,e.jsx)("a",{href:"/cgi-bin/luci/admin/modem/maintenance/settings",'
            'target:"_top",children:"打开连接设置"})})]})})}),' + info[end:])
    start = info.index('$u=function()')
    end = info.index(',wr=function()', start)
    wchanged[name] = (info[:start] + '$u=function(n){return window.c8SwitchSlot(n).then(function(ok){if(ok)Ar()})}' + info[end:]).encode()
    for name, value in woriginal.items():
        if not name.endswith('/index.html'):
            continue
        page = value.decode('utf-8').replace('<script src="/scripts/loading.js"></script>', '')
        page = replace_once(page, '<script src="/5700/preload_helper.',
                            '<script src="/5700/c8-bridge.js"></script>\n<script src="/5700/preload_helper.')
        wchanged[name] = page.encode()

    # Preflight both trees before any writes, also on repeated invocation.
    for tree, before, after in ((manager, original, changed), (web, woriginal, wchanged)):
        for name in before:
            if (tree / name).read_bytes() not in (before[name], after[name]):
                raise ValueError(f'C8 cellular reviewed source drift: {name}')
    for tree, before, after in ((manager, original, changed), (web, woriginal, wchanged)):
        for name, value in before.items():
            backup = tree / '.c8-cellular-originals' / (name + '.original')
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                backup.write_bytes(value)
            (tree / name).write_bytes(after[name])


if __name__ == '__main__':
    adapt(Path(sys.argv[1]), Path(sys.argv[2]))
