"""Keep CGCONTRDP DNS separate from addresses in latest FM350 via anchor invariants."""
import hashlib
from pathlib import Path
import sys

SOURCE = "rust/src/modem/pdp.rs"

CID_GUARD = (
    '        for row in crate::at::rows(&r, "+CGCONTRDP") {\n'
    '            if row.first().and_then(|x| x.parse::<u32>().ok()) != Some(cfg.cid) {\n'
    '                continue;\n'
    '            }\n'
)

DNS_PUSH = (
    "                } else if let Some(a6) = normalize_ipv6(item) {\n"
    "                    // AT manual 12.2.10: fields 5/6 are DNS, never local addresses.\n"
    "                    contr_dns.push(a6);\n"
)

REPLACEMENT_SETS = (
    (
        (
            '        for row in crate::at::rows(&r, "+CGCONTRDP") {\n'
            '            // <cid>,<bearer>,',
            CID_GUARD + '            // <cid>,<bearer>,',
        ),
        (
            "                } else if contr_ipv6.is_empty() {\n"
            "                    // 同一字段位在 IPV4V6 下按地址族切换：实机 cid=1 的\n"
            "                    // <primary>/<secondary> 位给的就是点分十进制 IPv6。\n"
            "                    if let Some(a6) = normalize_ipv6(item) {\n"
            "                        contr_ipv6 = a6;\n"
            "                    }\n",
            DNS_PUSH,
        ),
    ),
    (
        (
            '        for row in crate::at::rows(&r, "+CGCONTRDP") {\n',
            CID_GUARD,
        ),
        (
            "                } else if contr_ipv6.is_empty() {\n"
            "                    // 同一字段位在 IPV4V6 下按地址族切换：实机 cid=1 的\n"
            "                    // <primary>/<secondary> 位给的就是点分十进制 IPv6。\n"
            "                    if let Some(a6) = normalize_ipv6(item) {\n"
            "                        contr_ipv6 = a6;\n"
            "                    }\n",
            DNS_PUSH,
        ),
    ),
)

COMMENT_NORMALIZATION = (
    "    // 回落判据必须挑得极其小心。FM350 的 `AT+CGACT?` **只回 OK、不列 `+CGACT:`\n"
    "    // 行**（模组方言），于是 `cgact` 恒为 `None` —— 这里不是罕见的异常兜底，\n"
    "    // 而是 FM350 上的**常态执行路径**，判错就直接让拨号状态机走错分支：\n",
    "    // Some firmware/context states return bare OK; others return +CGACT rows.\n"
    "    // Keep a valid CGACT result authoritative. When it is unknown, only the\n"
    "    // matching CGCONTRDP local-address field may support this fallback:\n",
)


def verify_postconditions(text):
    if "pub fn pdp(" not in text or "pub fn dial(" not in text:
        raise ValueError("PDP module entrypoints missing")
    if CID_GUARD not in text:
        raise ValueError("PDP target CID filter missing")
    if "contr_dns.push(a6);" not in text:
        raise ValueError("PDP IPv6 DNS separation missing")
    if "if let Some(a6) = normalize_ipv6(item) {\n                        contr_ipv6 = a6;" in text:
        raise ValueError("PDP still assigns trailing IPv6 DNS fields to contr_ipv6")


def patch_pdp(data):
    text = data.decode("utf-8")
    if CID_GUARD in text and DNS_PUSH in text:
        verify_postconditions(text)
        return data
    for pairs in REPLACEMENT_SETS:
        if all(text.count(before) == 1 for before, _ in pairs):
            updated = text
            for before, after in pairs:
                updated = updated.replace(before, after, 1)
            old_comment, new_comment = COMMENT_NORMALIZATION
            if updated.count(old_comment) == 1:
                updated = updated.replace(old_comment, new_comment, 1)
            verify_postconditions(updated)
            return updated.encode("utf-8")
    raise ValueError("PDP source anchors changed; review latest pdp.rs")


if __name__ == "__main__":
    path = Path(sys.argv[1]) / SOURCE
    patched = patch_pdp(path.read_bytes())
    path.write_bytes(patched)
    print("AP3000M FM350 PDP: DNS/address separation applied; sha256=" +
          hashlib.sha256(patched).hexdigest())
