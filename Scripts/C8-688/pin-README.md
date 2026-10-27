# 源码策略说明（Scripts/C8-688/source-pin.txt）

这个文件只有一行，决定**这次编译用哪个上游源码**。默认 `auto`，平时不用动它。

## 四种写法

| 内容 | 行为 | 什么时候用 |
|---|---|---|
| **`auto`（默认）** | 跟随 workflow 里的 `BRANCH` 最新，**并自动摘掉**上游那个会编不过的 firewall4 fullcone 补丁 | 平时；不需要任何人干预 |
| `master` / 空 | 跟随分支最新，**不动**任何补丁 | 上游把补丁修好了，想要完整上游行为 |
| 40 位 sha | 钉死到该 commit | 要严格可复现、或想绕开上游某个新问题 |
| `b16f9456…` | 就是上面那种 | 已验证能编出好镜像的提交之一 |

## 为什么要有 auto

2026-10-27 上游在换 firewall4 版本的同时塞进
`package/network/config/firewall4/patches/001-firewall4-add-support-for-fullcone-nat.patch`，
而该补丁对新源码 **8 个 hunk 全部打不上**，`firewall4` 直接编译失败。

这个补丁提供的是 **fw4 的 fullcone 规则集成**，跟 C8-688 没有任何关系
（我们的 Config 没选 turboacc，也不依赖 fw4 fullcone）。所以 `auto` 模式的做法是：
**跟上游最新 + 把这个补丁摘掉**，这样上游怎么变都不会因为这个补丁把产线卡死。

## 想恢复上游原样（带补丁）

1. 把本文件改成 `master`；
2. 跑一次 `C8-688` workflow；
3. 如果又红在 `firewall4` → 说明上游还没修，改回 `auto` 即可。

## 注意

- `auto` 会跟随上游 HEAD，**源码变了 Actions 缓存键就变**，第一次换会冷编（4~6 小时）；
- 钉 sha 时命中缓存最快，但拿不到新内核/新驱动；
- `feeds`（packages/luci 等）始终按 `feeds.conf.default` 跟各自分支，不受本文件影响。
