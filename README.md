# OpenWRT-CI

基于 [VIKINGYFY/OpenWRT-CI](https://github.com/VIKINGYFY/OpenWRT-CI) 维护的自用 ImmortalWrt 固件编译仓库，通过 GitHub Actions 分别构建 TR3000 512MB、AP3000M 和 C8-688。

[下载固件](https://github.com/AA9skillz-BN/OpenWRT-CI/releases) · [运行构建](https://github.com/AA9skillz-BN/OpenWRT-CI/actions) · [构建与维护说明](docs/BUILD-PIPELINE.md)

## 机型与默认设置

| 机型 | 构建入口 | 默认源码 / 分支 | 默认后台 | Wi-Fi / 密码 | 详细说明 |
| --- | --- | --- | --- | --- | --- |
| Cudy TR3000 v1 **512MB 闪存硬改版** | [TR3000](.github/workflows/TR3000.yml) | [VIKINGYFY/immortalwrt](https://github.com/VIKINGYFY/immortalwrt) / `owrt` | `192.168.6.1` | `TR3000` / `12345678` | [TR3000-512](docs/TR3000-512.md) |
| AirPi AP3000M | [AP3000M](.github/workflows/AP3000M.yml) | [immortalwrt/immortalwrt](https://github.com/immortalwrt/immortalwrt) / `master` | `192.168.10.1` | `AP3000M` / `12345678` | [AP3000M](docs/AP3000M.md) |
| NRadio / 鲲鹏无限 C8-688 | [C8-688](.github/workflows/C8-688.yml) | [immortalwrt/immortalwrt](https://github.com/immortalwrt/immortalwrt) / `master` | `192.168.66.1` | `C8-688` / `12345678` | [C8-688](docs/C8-688.md) |

表中是新配置的默认值；保留配置升级时，后台地址和 Wi-Fi 等以恢复的用户配置为准。
默认主题：TR3000 / AP3000M 使用 Aurora，C8-688 使用 Argon。

**TR3000 仅适用于已核对分区的 512MB 硬改机：UBI 起点 `0x5c0000`、长度 `0x1da40000`。** 原厂闪存或其他 U-Boot 布局不能直接使用；设备标识相同也不能证明分区兼容，`sysupgrade -T` 不能代替人工核对布局。

C8-688 使用 eMMC 的 `kernel_2nd` / `rootfs_2nd` 第二系统槽。首次从原厂系统安装前，应按[机型文档](docs/C8-688.md#3-刷机只动第二系统槽)核对分区、备份并关闭存储扩展。

## 如何构建

进入 **Actions → 对应机型 → Run workflow**。三款固件均为手动触发；构建代码、配置和工作流的推送或 PR 按路径触发 `Build logic checks`。

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `SOURCE` | 见机型表 | 源码仓库，格式为 `owner/repo` |
| `BRANCH` | 见机型表 | 源码分支 |
| `SOURCE_MODE` | `locked` | 按版本锁构建；`latest` 显式跟随所选源码和 feeds |
| `PACKAGE` | 空 | 临时追加配置，例如 `CONFIG_PACKAGE_包名=y`；多项用字面量 `\n` 分隔 |
| `TEST` | `false` | 生成并校验配置，上传配置预览，不编译固件 |
| `BUILD_ONLY` | `false` | 完整编译、校验并上传固件 Artifact，不创建 Release |

建议按以下顺序验证配置或上游更新：

| 模式 | `TEST` | `BUILD_ONLY` | 结果 |
| --- | --- | --- | --- |
| 配置预览 | `true` | 任意 | 检查 Kconfig、单设备及必需包，上传 `config-*` Artifact |
| 完整验证 | `false` | `true` | 编译并检查实际镜像，上传 `firmware-*` Artifact |
| 编译并发布 | `false` | `false` | 验证通过后，由独立发布 job 创建 Release |

`locked` 要求 SOURCE / BRANCH 与 [sources.lock.json](Profiles/sources.lock.json) 一致；只改输入框不会绕过版本锁。
`latest` 仍使用 [packages.lock.json](Profiles/packages.lock.json) 固定第三方插件版本。其他源码或分支需要重新验证设备支持和包依赖。

同一机型应等当前任务结束、检查结果后再启动下一版。若已确认旧任务必然失败，应先取消并确认停止，再启动修复版；不同机型可以并行。

## 构建流程与校验

三款入口共用 [DEVICE-CORE.yml](.github/workflows/DEVICE-CORE.yml)，分别执行自己的机型钩子：

```text
TR3000.yml / AP3000M.yml / C8-688.yml
  → 锁定主源码和 feeds
  → 按 profile 安装锁定版本的插件
  → bash Scripts/<机型>/private.sh
  → 合并配置、defconfig、检查唯一设备和必需包
  → 恢复缓存、下载、编译
  → 校验 sysupgrade、manifest、设备元数据、实际 FIT/DTB 和 squashfs
  → 上传本次 Artifact
  → 独立发布 job 复核校验和、上传 draft、公开 Release
```

主要校验包括：

- Kconfig 退出状态及日志错误、单设备选择、profile 中的 `required_packages`。
- 唯一 sysupgrade 镜像、同前缀 manifest、清单中的必需包、非空 kernel / root 和设备元数据。平台通用 manifest 仅在单设备配置校验通过时接受。
- 实际 FIT 默认配置中的 DTB：设备 compatible、TR3000 的 UBI 布局、C8-688 的第二系统 rootfs 启动参数。
- 实际 squashfs 中的关键文件、执行权限、启动链接、脚本内容及本次构建标签；C8 另核对适配后的升级平台脚本。

配置被 `defconfig` 调整的情况写入 `config-resolution.json`；必需包丢失或关键校验不符会阻止发布。
构建 job 使用只读仓库权限，发布凭据仅交给独立发布 job。

固件附件保留 sysupgrade、实际生效的 `Config-*.txt`、manifest、`sha256sums`、源码 / feeds / 插件版本记录以及镜像校验证据。
完整构建的报告见 Actions 的 `build-reports-*` Artifact。

Release tag 包含机型、源码、分支、时间、run ID 和 attempt；非 `main` 分支发布额外使用 `preview-` 前缀，并标为 prerelease。配置预览和 BUILD_ONLY 均不会创建 Release。

构建检查可以发现配置和镜像内容问题；刷写、散热、无线和移动网络仍需对应硬件验证。

## 机型功能

### TR3000 512MB

- 构建期适配 512MB UBI 布局，布局锚点变化时停止构建。
- Aurora 主题与配置、OpenClash、TurboACC、BBR、ttyd，以及 USB 网络和存储支持。
- F50 类 USB 网卡自动接入 WAN，按实际枚举结果绑定设备。
- 首次创建 USB WAN 时设置 DHCP、优先级和公共 DNS；已有协议、DNS、metric、停用开关及防火墙区域保留。旧 `F50` 接口改名时迁移已有配置和区域引用。
- LAN、主题和 TurboACC 默认值在构建期设置，保留配置升级时不会由旧的自定义首启脚本强制重设。

OpenClash 的内核和订阅需要刷机后自行配置。功能及分区要求见 [TR3000 文档](docs/TR3000-512.md)。

### AP3000M

- FM350-GL 管理、首次开机 AT 口 / APN / CID 配置纠正、风扇控制和 ttyd。
- RNDIS 开机检查及每 5 分钟巡检；恢复验收要求接口、IPv4、该接口默认路由和有效收包计数，自动与手动自愈共用文件锁。
- RPS/XPS、BBR、软件 Flow Offloading 和 MTU 探测。
- LuCI「系统 → 固件升级」和 `ap3000m-ota`，支持下载校验、保留 / 不保留配置升级，以及指定版本回退。
- OTA 元数据从 GitHub API 获取，镜像站只用于下载文件；下载后核对大小和 SHA-256，刷写前执行 `sysupgrade -T`。
- 已下载镜像连同 `<TAG>.bin.json` 校验记录，可重新核对后离线回退。没有记录的旧镜像须先联网执行一次 `download --tag <TAG>`，不能直接作为已认证镜像使用。

```sh
ap3000m-ota check                        # 检查正式版更新
ap3000m-ota download                     # 下载并校验
ap3000m-ota upgrade                      # 默认保留配置升级
ap3000m-ota upgrade --wipe --yes          # 清空配置升级
ap3000m-ota upgrade --tag <TAG>           # 指定版本，可用已校验的本地镜像回退
ap3000m-ota status                       # 当前版本和本地状态
```

默认 OTA 渠道为 `AP3000M-immortalwrt-master-`，排除草稿及预发布版本。
`ap3000m_ota.main.enabled=0` 关闭网页操作和定时检查，`auto_check=0` 只关闭每日检查；命令行手动操作仍可用。
`fm350.main.rdnis_watch=0` 关闭运行期定时巡检。开关每次调用时读取，重新启用无需重装 cron。
网页“任务已启动”表示进入后台下载 / 校验，后续错误会显示在页面；后台日志为 `/tmp/ap3000m-ota-operation.log`。

配置、安装与排障见 [AP3000M 文档](docs/AP3000M.md)。

### C8-688

- 本仓库注入 DTS、设备定义、LAN/WAN 和 MAC 适配，集成 MT5700M 管理、Argon 主题和 ttyd。
- 第二系统的 eMMC 升级检查、写入和配置复制适配：仅接受 `kernel_2nd=/dev/mmcblk0p8`、`rootfs_2nd=/dev/mmcblk0p9` 的块设备布局，并校验镜像成员和大小。
- 风扇按有效 SoC 温度调速，读取失败时全速保护；Wi-Fi MAC 通过 UCI 设置，增量由上游函数处理进位。
- 提供 U-Boot 环境探测与 `c8-boot-official` 系统切换工具。

不含原厂 NR-OS APP 和多 SIM 自动切换界面。首次安装与后续 sysupgrade 的路径不同，操作步骤见 [C8-688 文档](docs/C8-688.md)。

## 维护与上游同步

日常维护在 `main` 上进行。配置和代码以仓库文件为准：

| 位置 | 用途 |
| --- | --- |
| `.github/workflows/TR3000.yml`、`AP3000M.yml`、`C8-688.yml` | 机型入口和默认参数 |
| `.github/workflows/DEVICE-CORE.yml` | 三款机型共用的构建、校验及发布流程 |
| `Profiles/<机型>.json` | 配置片段、插件选择、必需包和镜像检查要求 |
| `Profiles/sources.lock.json` | 主源码与 feeds 的完整提交 SHA |
| `Profiles/packages.lock.json` | 第三方插件的来源、提交和精确替换目录 |
| `Config/BASE.txt`、`Config/Features/`、`Config/<机型>.txt` | 基础、功能与设备配置；机型配置覆盖基础片段 |
| `Scripts/<机型>/private.sh`、`Files/<机型>/` | 机型源码适配和固件文件投放 |
| `.github/scripts/` | 版本锁、构建校验、发布清理及回归测试 |

公共 `Settings.sh`、`Handles.sh` 和工具链准备脚本仍由三款共用；修改公共逻辑后应验证受影响机型。
[WRT-CORE.yml](.github/workflows/WRT-CORE.yml)、[Packages.sh](Scripts/Packages.sh) 和 [PRIVATE.sh](Scripts/PRIVATE.sh) 保留供通用入口使用，三款独立入口不经过这条分发链。[脚本目录说明](Scripts/README.md)列出了两条路径。

**同步 CI 模板**与**更新固件源码**是两件事：

- 同步模板时审查合并差异，保留本仓库的 `DEVICE-CORE.yml`、机型定制和版本锁。`DEVICE-CORE.yml` 不会自动继承 `WRT-CORE.yml` 的更新。
- 默认 `locked` 不随源码 / feeds 分支移动。试验上游版本时显式选 `latest`，核对生成的 `source-lock.json`，再更新源码 / feeds 锁；第三方插件更新另改 `packages.lock.json`。
- 更新后先检查 Build logic checks 和受影响机型的 TEST，再做 BUILD_ONLY；确认后再发布。遇到上游不兼容应适配修复，不能靠删除功能或跳过校验放行。

三款默认的自动清理和缓存维护如下：

| 工作流 / 功能 | 行为 |
| --- | --- |
| [Build logic checks](.github/workflows/BUILD-CHECKS.yml) | 回归测试、真实 ucode / 文件锁与 FIT/DTB/squashfs 测试、Shell / JS / 工作流语法检查 |
| [Auto-Clean](.github/workflows/Auto-Clean.yml) | 每天香港时间 05:21，三款机型每渠道保留最近 3 个 Release；回收已核实归属、失败且最后活动超过 24 小时的流水线草稿，人工草稿/未识别机型/Git tag 保留；手动默认预览，`APPLY=true` 才删除 |
| 下载缓存 `dl` | 按机型与 ref 使用，下载内容仍由 OpenWrt 核对哈希 |
| 编译缓存 `.ccache` | 滚动保存；编译失败也可保留已完成的缓存，取消的任务不保存 |
| host / toolchain staging | 按源码、feeds、配置、工具链和构建脚本等输入严格匹配；恢复后刷新 Rust 工具链接 |
| 缓存轮换 | 成功后清理本机型、本 ref 的旧缓存，保留当前及最新一代；缺少当前缓存时保留回退缓存 |

`Cache-Clean`、`AIROHA-ALL`、`MTK-ALL`、`OWRT-ALL`、`QCA-ALL`、`QCB-ALL` 当前在 GitHub 中手动禁用；文件保留，启用前需重新审查。
这些通用构建入口已移除 Auto-Clean 完成后的自动触发，AIROHA 也只保留手动入口。
`Cache-Clean` 的手动动作会清空仓库缓存，不用于日常轮换。工作流启停状态以 Actions 设置为准。
缓存不含完整 `build_dir`，构建时间受上游、命中情况和 runner 影响，不给出固定耗时保证。

## 刷机前核对

- 下载与机型和分区布局一致的固件，备份配置，按附件 `sha256sums` 核对文件。
- 执行 `sysupgrade -T /tmp/实际固件文件名.bin`；报错时先定位原因，避免用 `-F` 绕过检查。
- 跨源码或大版本升级时，应评估保留旧配置的兼容性；刷写期间不要断电。
- TR3000 硬改分区和 C8 第二槽要求见机型文档；AP3000M 的 OTA 回退依赖已保存的镜像及校验记录。

## 致谢

- [VIKINGYFY/OpenWRT-CI](https://github.com/VIKINGYFY/OpenWRT-CI)：原始 CI 框架。
- [VIKINGYFY/immortalwrt](https://github.com/VIKINGYFY/immortalwrt)、[immortalwrt/immortalwrt](https://github.com/immortalwrt/immortalwrt)：固件源码。
- [OpenClash](https://github.com/vernesong/OpenClash)、[TurboACC](https://github.com/chenmozhijin/turboacc)。
- [Aurora](https://github.com/eamonxg/luci-theme-aurora)、[Aurora 配置](https://github.com/eamonxg/luci-app-aurora-config)。
- [FM350](https://github.com/LianXia233/luci-app-fm350)、[AP3000M 风扇控制](https://github.com/LianXia233/luci-app-airpi3000m-fancontrol)、[MT5700M](https://github.com/FAN789/luci-app-mt5700m)。
