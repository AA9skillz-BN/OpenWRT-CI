# 源码 pin 说明（Scripts/C8-688/source-pin.txt）

这个文件只做一件事：**决定这次编译用哪个上游 commit**。
`Scripts/C8-688/private.sh` 在注入设备支持之前会读它。

## 三种写法

| 文件内容 | 行为 | 什么时候用 |
|---|---|---|
| 一串 40 位 sha（当前） | 钉死到这个 commit，编出来的东西每次都一样 | 默认；刷机验证期、要可复现时 |
| `master` 或空文件 | 跟随 workflow 里填的 `BRANCH` 最新提交 | 上游把问题修好了、想拿新内核/驱动时 |
| 另一个 sha | 钉到另一个已知好点 | 想升级但又要可复现时 |

## 当前为什么钉

`immortalwrt/immortalwrt@b16f9456` 是**第一个编出结构正确镜像**的提交。
在那之后上游把 `firewall4` 升到 `2026.10.26~bac1d656`，而它自带的
`package/network/config/firewall4/patches/001-firewall4-add-support-for-fullcone-nat.patch`
对不上新源码（8 个 hunk 全失败），`firewall4` 直接编译失败。

## 上游修好之后怎么解除

1. 把本文件内容改成 `master`（或直接清空）；
2. 手动跑一次 `C8-688` workflow，`TEST=false`；
3. 编过了、镜像验收通过 → 就保持跟随 master；
4. 又挂了 → 把 sha 填回来，等下一次。

## 兜底

`private.sh` 里还有一层：**pin 失败（拉不到那个 sha）时，会自动退回当前 HEAD
并删掉那个失效的 fullcone 补丁**，保证至少还能编过，不会因为 pin 把产线卡死。

## 注意

- 换 pin 会导致 Actions 缓存键变化（键里含源码 HEAD 的 sha），**第一次换一定会冷编**（4~6 小时）；
- 钉住的只是“源码 commit”，`feeds`（packages/luci 等）仍然按 `feeds.conf.default` 跟各自分支，不受影响。
