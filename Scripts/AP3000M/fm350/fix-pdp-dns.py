"""Keep CGCONTRDP DNS separate from addresses in the reviewed FM350 source."""
import hashlib
from pathlib import Path
import sys

UPSTREAM_SHA256 = "cf85267027fc7d3038de1cab760031b286908caa476d356c80d28bea57b48c81"
SOURCE = "rust/src/modem/pdp.rs"

REPLACEMENTS = (
    (
        '        for row in crate::at::rows(&r, "+CGCONTRDP") {\n',
        '        for row in crate::at::rows(&r, "+CGCONTRDP") {\n'
        '            if row.first().and_then(|x| x.parse::<u32>().ok()) != Some(cfg.cid) {\n'
        '                continue;\n'
        '            }\n',
    ),
    (
        "                } else if contr_ipv6.is_empty() {\n"
        "                    // 同一字段位在 IPV4V6 下按地址族切换：实机 cid=1 的\n"
        "                    // <primary>/<secondary> 位给的就是点分十进制 IPv6。\n"
        "                    if let Some(a6) = normalize_ipv6(item) {\n"
        "                        contr_ipv6 = a6;\n"
        "                    }\n",
        "                } else if let Some(a6) = normalize_ipv6(item) {\n"
        "                    // AT manual 12.2.10: fields 5/6 are DNS, never local addresses.\n"
        "                    contr_dns.push(a6);\n",
    ),
    (
        "    // 回落判据必须挑得极其小心。FM350 的 `AT+CGACT?` **只回 OK、不列 `+CGACT:`\n"
        "    // 行**（模组方言），于是 `cgact` 恒为 `None` —— 这里不是罕见的异常兜底，\n"
        "    // 而是 FM350 上的**常态执行路径**，判错就直接让拨号状态机走错分支：\n",
        "    // Some firmware/context states return bare OK; others return +CGACT rows.\n"
        "    // Keep a valid CGACT result authoritative. When it is unknown, only the\n"
        "    // matching CGCONTRDP local-address field may support this fallback:\n",
    ),
)


def patch_pdp(data):
    text = data.decode("utf-8")
    if hashlib.sha256(data).hexdigest() != UPSTREAM_SHA256:
        original = text
        for before, after in REPLACEMENTS:
            if original.count(after) != 1:
                raise ValueError("PDP source differs from the reviewed locked source")
            original = original.replace(after, before, 1)
        if hashlib.sha256(original.encode("utf-8")).hexdigest() != UPSTREAM_SHA256:
            raise ValueError("PDP source differs from the reviewed locked source")
        return data
    for before, after in REPLACEMENTS:
        if text.count(before) != 1:
            raise ValueError("PDP source anchors changed")
        text = text.replace(before, after, 1)
    return text.encode("utf-8")


if __name__ == "__main__":
    path = Path(sys.argv[1]) / SOURCE
    patched = patch_pdp(path.read_bytes())
    path.write_bytes(patched)
    print("AP3000M FM350 PDP: DNS/address separation applied; sha256=" +
          hashlib.sha256(patched).hexdigest())
