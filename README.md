# OpenWRT-CI

基于 [VIKINGYFY/OpenWRT-CI](https://github.com/VIKINGYFY/OpenWRT-CI) 维护的自用 ImmortalWrt 固件编译仓库，通过 GitHub Actions 分别构建 TR3000 512MB、AP3000M 和 C8-688。

[下载固件](https://github.com/AA9skillz-BN/OpenWRT-CI/releases) · [运行构建](https://github.com/AA9skillz-BN/OpenWRT-CI/actions) · [构建与维护说明](docs/BUILD-PIPELINE.md) · [在线升级设计](docs/OTA-DESIGN.md) · [工具链缓存](docs/TOOLCHAIN-CACHE.md)

本页描述当前 `main` 的构建能力。已有 Release 不会随代码更新而改变；请按机型选择发布标签，
不要把仓库首页的全局 `Latest` 当作三款设备共同的最新版。BUILD_ONLY 生成的测试固件在对应
Actions 运行的 Artifact 中，验收后才另行发布。

## 机型与默认设置

| 机型 | 构建入口 | 默认源码 / 分支 | 默认后台 | Wi-Fi / 密码 | 详细说明 |
| --- | --- | --- | --- | --- | --- |
| Cudy TR3000 v1 **512MB 闪存硬改版** | [TR3000](.github/workflows/TR3000.yml) | [VIKINGYFY/immortalwrt](https://github.com/VIKINGYFY/immortalwrt) / `owrt` | `192.168.6.1` | `TR3000` / `12345678` | [TR3000-512](docs/TR3000-512.md) |
| AirPi AP3000M | [AP3000M](.github/workflows/AP3000M.yml) | [immortalwrt/immortalwrt](https://github.com/immortalwrt/immortalwrt) / `master` | `192.168.10.1` | `AP3000M` / `12345678` | [AP3000M](docs/AP3000M.md) |
| NRadio / 鲲鹏无限 C8-688 | [C8-688](.github/workflows/C8-688.yml) | [immortalwrt/immortalwrt](https://github.com/immortalwrt/immortalwrt) / `master` | `192.168.66.1` | `C8-688` / `12345678` | [C8-688](docs/C8-688.md) |

表中是新配置的默认值；保留配置升级时，后台地址和 Wi-Fi 等以恢复的用户配置为准。
三款默认均使用 Aurora 主题；C8-688 保留配置升级时会迁移旧 Argon / Bootstrap 默认主题。

**TR3000 仅适用于已核对分区的 512MB 硬改机：UBI 起点 `0x5c0000`、长度 `0x1da40000`。** 原厂闪存或其他 U-Boot 布局不能直接使用；设备标识相同也不能证明分区兼容，`sysupgrade -T` 不能代替人工核对布局。

C8-688 使用 eMMC 的 `kernel_2nd` / `rootfs_2nd` 第二系统槽。首次从原厂系统安装前，应按[机型文档](docs/C8-688.md#3-刷机只动第二系统槽)核对分区、备份并关闭存储扩展。

## 如何构建

进入 **Actions → 对应机型 → Run workflow**。三款固件均为手动触发；构建代码、配置和工作流的推送或 PR 按路径触发 `Build logic checks`。

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `SOURCE` | 见机型表 | 源码仓库，格式为 `owner/repo` |
| `BRANCH` | 见机型表 | 源码分支 |
| `SOURCE_MODE` | `locked` | 按版本锁构建；`latest` 显式跟随所选源码和 feeds |
| `PLUGIN_MODE` | `locked` | 按插件版本锁构建；`latest` 跟随各插件配置分支，未配置时跟随默认分支 |
| `PACKAGE` | 空 | 临时追加配置，例如 `CONFIG_PACKAGE_包名=y`；多项用字面量 `\n` 分隔 |
| `TEST` | `false` | 生成并校验配置，上传配置预览，不编译固件 |
| `BUILD_ONLY` | `false` | 完整编译、校验并上传固件 Artifact，不创建 Release |

建议按以下顺序验证配置或上游更新：

| 模式 | `TEST` | `BUILD_ONLY` | 结果 |
| --- | --- | --- | --- |
| 配置预览 | `true` | 任意 | 检查 Kconfig、单设备及必需包，上传 `config-*` Artifact |
| 完整验证 | `false` | `true` | 编译并检查实际镜像，上传 `firmware-*` Artifact |
| 编译并发布 | `false` | `false` | 验证通过后，由独立发布 job 创建 Release |

`SOURCE_MODE` 和 `PLUGIN_MODE` 独立，默认均为 `locked`：

| `SOURCE_MODE` | `PLUGIN_MODE` | 主源码 / feeds | 单独拉取的第三方插件 |
| --- | --- | --- | --- |
| `locked` | `locked` | 使用源码版本锁 | 使用插件版本锁 |
| `locked` | `latest` | 使用源码版本锁 | 跟随插件分支 |
| `latest` | `locked` | 跟随所选源码和 feeds | 使用插件版本锁 |
| `latest` | `latest` | 跟随所选源码和 feeds | 跟随插件分支 |

`SOURCE_MODE=locked` 要求 SOURCE / BRANCH 与 [sources.lock.json](Profiles/sources.lock.json) 一致；只改输入框不会绕过版本锁。
`PLUGIN_MODE=latest` 在每次构建中只解析一次分支，再按确切提交 SHA 下载，记录原锁与实际版本；
不会改写仓库内的 [packages.lock.json](Profiles/packages.lock.json)，这里的 latest 指分支最新提交，而非最新 Release。
本仓库自带的 OTA 和设备脚本随本仓库提交，feeds 内的软件包由 `SOURCE_MODE` 控制。

想单独尝试最新版插件，可选 `SOURCE_MODE=locked`、`PLUGIN_MODE=latest`、`TEST=false`、`BUILD_ONLY=true`。
AP 的 FM350 / 风扇和 C8 风扇仍执行严格适配检查；上游改变受检查内容时会停止构建，需审查适配。
其他源码、分支或新版插件仍需验证设备支持和包依赖；该选项不会自动更新已刷设备。

同一机型应等当前任务结束、检查结果后再启动下一版。若已确认旧任务必然失败，应先取消并确认停止，再启动修复版；不同机型可以并行。

## 构建流程与校验

三款入口共用 [DEVICE-CORE.yml](.github/workflows/DEVICE-CORE.yml)，分别执行自己的机型钩子：

```text
TR3000.yml / AP3000M.yml / C8-688.yml
  → 按 SOURCE_MODE 选择主源码和 feeds
  → 按 profile 和 PLUGIN_MODE 安装确切提交的插件
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
`plugin-resolution.json` 记录插件模式、分支、原锁和实际提交；附件 `packages.lock.json` 与
`build-inputs.json` 记录本次实际插件快照，两者与仓库原锁在 latest 模式下可能不同。
完整构建的报告见 Actions 的 `build-reports-*` Artifact。
固件和构建报告默认保留 7 天，需要长期保存时应及时下载；Artifact、正式 Release 和构建缓存分别管理。

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

- [FM350-GL 管理](https://github.com/LianXia233/luci-app-fm350)、首次开机 AT 口 / APN / CID 配置纠正、[AP3000M 风扇控制](https://github.com/LianXia233/luci-app-airpi3000m-fancontrol)和 ttyd。
- FM350 的 PDP / DNS 与打包适配、风扇页面确认和返回状态修正；关键文件在实际镜像中逐字节核对，Rust 后端检查目标架构。
- 提供用户主动开启的 FM350 USB 2.0 兼容选项，默认保留 USB 3.0；具体排障步骤和限制见机型文档。
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
ap3000m-ota self-update                  # 从兼容的正式 Release 更新 CLI，保留备份
```

默认 OTA 渠道为 `AP3000M-immortalwrt-master-`，排除草稿及预发布版本。
`ap3000m_ota.main.enabled=0` 关闭网页操作和定时检查，`auto_check=0` 只关闭每日检查；命令行手动操作仍可用。
`fm350.main.rdnis_watch=0` 关闭运行期定时巡检。开关每次调用时读取，重新启用无需重装 cron。
网页“任务已启动”表示进入后台下载 / 校验，后续错误会显示在页面；后台日志为 `/tmp/ap3000m-ota-operation.log`。

配置、安装与排障见 [AP3000M 文档](docs/AP3000M.md)。

### C8-688

- 本仓库注入 DTS、设备定义、LAN/WAN 和 MAC 适配，集成 [MT5700M 管理](https://github.com/FAN789/luci-app-mt5700m)、Aurora 主题与配置、ttyd。
- PHY 适配区分 MDIO `0x15` 与十进制 `15`；实际 FIT 检查地址、连接、模式及复位脚，板载上行仍需对应新镜像真机验收。
- 第二系统的 eMMC 升级检查、写入和配置复制适配：仅接受 `kernel_2nd=/dev/mmcblk0p8`、`rootfs_2nd=/dev/mmcblk0p9` 的块设备布局，并校验镜像成员和大小。
- 移植 [H5000M 风扇管理](https://github.com/LianXia233/luci-app-h5000m-fancontrol)，提供自动、手动 PWM 和内核控制模式。旧 `c8-fan` 只做代理，避免两个控制循环争写 PWM；温度缺失或达到 75℃时全速保护，内核最低散热需求仍有效。页面 PWM 回读值不是实测 RPM。
- LuCI「系统 → 固件升级」和 `c8-688-ota`，支持一键下载校验、保留 / 清空配置升级、指定正式版本及离线回退。
- 提供需确认的“切回第一系统”按钮与 `c8-boot-official` 工具；下载、升级、CLI 自更新和切系统共用锁。先核对板型、第二槽、分区、env 配置与 CRC，切换时写后读回再重启。
- Wi-Fi MAC 通过 UCI 设置，增量由上游函数处理进位；不修改 flash 校准数据。

```sh
c8-688-ota check                         # 检查本机型正式版更新
c8-688-ota download                      # 下载并校验
c8-688-ota upgrade --keep --yes          # 第二系统内升级，保留配置
c8-688-ota status                        # 当前版本与启动选择状态
c8-688-ota self-update                   # 仅更新兼容的 C8 CLI
```

默认 OTA 渠道为 `C8-688-immortalwrt-master-`。切回第一系统仅改变启动选择，不会复制或修复原厂固件；
第一系统必须本来就可启动。风扇物理调速、OTA 刷写和系统切换仍需按对应镜像逐项真机验收。

不含原厂 NR-OS APP 和多 SIM 自动切换界面。首次安装与后续 sysupgrade 的路径不同，操作步骤见 [C8-688 文档](docs/C8-688.md)。

AP 与 C8 的 OTA CLI、RPC、配置和兼容 ABI 分别维护。`self-update` 只接受本机型、当前渠道正式
Release 中通过大小 / 摘要 / ABI / 依赖校验的 CLI 附件，不从 `main` 下载脚本，也不更新 UI / RPC。
每日自动检查只更新提示，不会自动刷机。TR3000 当前未集成这套一键 OTA，不能直接安装 AP / C8 脚本。

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
- 源码 / feeds 与第三方插件各有独立版本模式，默认都不随分支移动。试验时分别选 `SOURCE_MODE=latest` 或 `PLUGIN_MODE=latest`，核对 `source-lock.json`、`plugin-resolution.json` 和实际插件快照；确认后再审查更新对应版本锁。
- 更新后先检查 Build logic checks 和受影响机型的 TEST，再做 BUILD_ONLY；确认后再发布。遇到上游不兼容应适配修复，不能靠删除功能或跳过校验放行。

三款默认的自动清理和缓存维护如下：

| 工作流 / 功能 | 行为 |
| --- | --- |
| [Build logic checks](.github/workflows/BUILD-CHECKS.yml) | 回归测试、真实 ucode / 文件锁与 FIT/DTB/squashfs 测试、Shell / JS / 工作流语法检查 |
| [Auto-Clean](.github/workflows/Auto-Clean.yml) | 每天香港时间 05:21，三款机型每渠道保留最近 3 个 Release；回收已核实归属、失败且最后活动超过 24 小时的流水线草稿，人工草稿/未识别机型/Git tag 保留；手动默认预览，`APPLY=true` 才删除 |
| 下载缓存 `dl` | 根据实际源码 / feeds / 插件提交、最终配置及适配后配方生成固定 key，精确命中不重复保存；旧缓存可补齐下载，内容仍由 OpenWrt 核对哈希 |
| 编译缓存 `.ccache` | 滚动保存；编译失败也可保留已完成的缓存，取消的任务不保存 |
| 安装后的 host / toolchain（v3） | 按实际工具链源码、规则、有效配置和宿主依赖精确匹配；核对文件内容、权限、链接及原始完成 stamp，通过后仅规范化 tools / toolchain 源码时间。只保存 host / toolchain 和完整性清单，Rust 链接另行刷新 |
| 缓存异常 | 损坏或部分恢复时还原本轮干净宿主环境，告警后冷编；真实输入漂移或无法还原仍失败。封存、保存、服务或统计故障不阻断已验证固件 |
| 缓存轮换 | 下载 / ccache 按机型和 ref 轮换；共享工具链按 ref / target 保护当前 key、最新两代及七天内条目。坏缓存仅精确删除已确认的 key，保护较新替代条目 |

工具链 key 不直接绑定机型、插件 / feeds 版本、Rust 或固件日期；这些变化若影响实际工具链输入，
仍会使缓存失效。AP / C8 只有本次实际指纹相同才共享，不能仅凭平台相同判断。

既有 [C8-688](https://github.com/AA9skillz-BN/OpenWRT-CI/actions/runs/40874227683) 与
[TR3000](https://github.com/AA9skillz-BN/OpenWRT-CI/actions/runs/40874250839) 冷 / 暖对照覆盖两个不同工具链指纹：
暖构建精确命中、完整性通过、tools / toolchain 没有 compile 事件，最终镜像校验通过。
该轮 AP 未做独立暖构建，后续需核对实际 key 后判断共享范围。
恢复缓存不等于证明编译复用；整体耗时还受下载、ccache 与 runner 影响，不能将全部时差归给工具链缓存。

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
- [H5000M 风扇管理](https://github.com/LianXia233/luci-app-h5000m-fancontrol)：C8-688 风扇移植的基础。
