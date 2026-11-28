# OpenWRT-CI

基于 [VIKINGYFY/OpenWRT-CI](https://github.com/VIKINGYFY/OpenWRT-CI) 维护的自用 ImmortalWrt 固件编译仓库，通过 GitHub Actions 分别构建 TR3000 512MB 和 AP3000M。

[下载固件](https://github.com/AA9skillz-BN/OpenWRT-CI/releases) · [运行构建](https://github.com/AA9skillz-BN/OpenWRT-CI/actions) · [构建与维护说明](docs/BUILD-PIPELINE.md) · [在线升级设计](docs/OTA-DESIGN.md) · [工具链缓存](docs/TOOLCHAIN-CACHE.md)

本页描述当前 `main` 的构建能力。已有 Release 不会随代码更新而改变；请按机型选择发布标签，
不要把仓库首页的全局 `Latest` 当作两款设备共同的最新版。BUILD_ONLY 生成的测试固件在对应
Actions 运行的 Artifact 中，验收后才另行发布。

## 机型与默认设置

| 机型 | 构建入口 | 默认源码 / 分支 | 默认后台 | Wi-Fi / 密码 | 详细说明 |
| --- | --- | --- | --- | --- | --- |
| Cudy TR3000 v1 **512MB 闪存硬改版** | [TR3000](.github/workflows/TR3000.yml) | [VIKINGYFY/immortalwrt](https://github.com/VIKINGYFY/immortalwrt) / `owrt` | `192.168.6.1` | `TR3000` / `12345678` | [TR3000-512](docs/TR3000-512.md) |
| AirPi AP3000M | [AP3000M](.github/workflows/AP3000M.yml) | [immortalwrt/immortalwrt](https://github.com/immortalwrt/immortalwrt) / `master` | `192.168.10.1` | `AP3000M` / `12345678` | [AP3000M](docs/AP3000M.md) |

新建无线配置时，两款机型的 2.4 GHz 均使用 WPA2-PSK / CCMP。TR3000 的 5 GHz 同样使用 WPA2-PSK / CCMP，AP3000M 的 5 GHz 使用 WPA2/WPA3 混合模式。
保留配置升级时，后台地址和 Wi-Fi 等以恢复的用户配置为准。
旧固件曾因无线生成器格式变化而未写入密码；修复需重新构建后刷入，已有 Release 附件不会随 main 更新。
两款默认均使用 Aurora 主题。

**TR3000 仅适用于已核对分区的 512MB 硬改机：UBI 起点 `0x5c0000`、长度 `0x1da40000`。** 原厂闪存或其他 U-Boot 布局不能直接使用；设备标识相同也不能证明分区兼容，`sysupgrade -T` 不能代替人工核对布局。


## 如何构建

进入 **Actions → 对应机型 → Run workflow**。两款固件均为手动触发；推送或提交 PR 会触发 `Build logic checks`，也可手动运行检查。

| 参数 | 默认值 | 作用 |
| --- | --- | --- |
| `SOURCE` | 见机型表 | 源码仓库，格式为 `owner/repo` |
| `BRANCH` | 见机型表 | 源码分支 |
| `SOURCE_MODE` | `latest` | 跟随所选源码和 feeds 上游；可选 `locked` 使用源码版本锁 |
| `PLUGIN_MODE` | `auto` | 优先跟随各插件分支，已覆盖的适配检查不兼容时逐插件回退；可选严格的 `latest` 或固定 `locked` |
| `PACKAGE` | 空 | 临时追加配置，例如 `CONFIG_PACKAGE_包名=y`；多项用字面量 `\n` 分隔 |
| `TEST` | `false` | 生成并校验配置，上传配置预览，不编译固件 |
| `PREVIEW_RUN_ID` | 空 | 完整构建可复用同机型、同分支、同 CI 提交的成功配置预览 |
| `BUILD_ONLY` | `false` | 设为 `true` 时，完整编译、校验并仅上传固件 Artifact |

建议按以下顺序验证配置或上游更新：

| 模式 | `TEST` | `BUILD_ONLY` | 结果 |
| --- | --- | --- | --- |
| 配置预览 | `true` | 任意 | 检查 Kconfig、单设备及必需包，上传 `config-*` Artifact |
| 完整验证 | `false` | `true` | 编译并检查实际镜像，上传 `firmware-*` Artifact |
| 编译并发布 | `false` | `false` | 验证通过后，由独立发布 job 创建 Release |

`SOURCE_MODE` 和 `PLUGIN_MODE` 独立，分别默认 `latest` 和 `auto`：

| `SOURCE_MODE` | `PLUGIN_MODE` | 主源码 / feeds | 单独拉取的第三方插件 |
| --- | --- | --- | --- |
| `locked` | `locked` | 使用源码版本锁 | 使用插件版本锁 |
| `locked` | `latest` | 使用源码版本锁 | 跟随插件分支 |
| `latest` | `locked` | 跟随所选源码和 feeds | 使用插件版本锁 |
| `latest` | `latest` | 跟随所选源码和 feeds | 严格跟随插件分支 |
| `latest` / `locked` | `auto` | 按源码模式处理 | 逐插件预检，遇适配不兼容时回退到锁提交 |

`SOURCE_MODE=locked` 要求 SOURCE / BRANCH 与 [sources.lock.json](Profiles/sources.lock.json) 一致；只改输入框不会绕过版本锁。
`PLUGIN_MODE=latest` 在每次构建中只解析一次分支，再按确切提交 SHA 下载，记录原锁与实际版本；
不会改写仓库内的 [packages.lock.json](Profiles/packages.lock.json)，这里的 latest 指分支最新提交，而非最新 Release。
本仓库自带的 OTA 和设备脚本随本仓库提交，feeds 内的软件包由 `SOURCE_MODE` 控制。

默认构建跟随最新源码和 feeds，插件优先取最新分支，并记录实际提交。
启用 `TEST=true` 后，Artifact 包含最终配置和 `build-snapshot.json`。
完整验证可设 `TEST=false`、`BUILD_ONLY=true`，将成功预览的 Actions run ID 填入 `PREVIEW_RUN_ID`，并沿用同一机型、分支、CI 提交及构建参数。构建会按快照拉取确切版本，并在编译前核对最终配置摘要。
需要严格验证插件最新版时使用 `PLUGIN_MODE=latest`。
需要按仓库版本锁构建时，将两个模式都设为 `locked`。
AP 的 FM350、风扇及两款机型的 TurboACC 等均受适配检查约束。`auto` 可按插件回退至锁定提交。源码适配失败、插件拉取失败或回退后仍无法适配时，构建会停止。
其他源码、分支或新版插件仍需验证设备支持和包依赖；该选项不会自动更新已刷设备。

同一机型应等当前任务结束、检查结果后再启动下一版。若已确认旧任务必然失败，应先取消并确认停止，再启动修复版；不同机型可以并行。

## 构建流程与校验

两款入口共用 [DEVICE-CORE.yml](.github/workflows/DEVICE-CORE.yml)，分别执行自己的机型钩子：

```text
TR3000.yml / AP3000M.yml
  → 生成统一构建标识，正式发布模式预先固定标签到本次 CI 提交
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
- 实际 FIT 默认配置中的 DTB：设备 compatible、TR3000 的 UBI 布局。
- 实际 squashfs 中的关键文件、执行权限、启动链接、脚本内容及本次构建标签；包括适配后的无线生成器、TurboACC 资源和服务。旧版冲突入口按 profile 禁止残留。

配置被 `defconfig` 调整的情况写入 `config-resolution.json`；必需包丢失或关键校验不符会阻止发布。
构建 job 使用只读仓库权限，发布凭据仅交给独立发布 job。

固件附件保留 sysupgrade、实际生效的 `Config-*.txt`、manifest、`sha256sums`、源码 / feeds / 插件版本记录以及镜像校验证据。
`plugin-resolution.json` 记录插件模式、分支、原锁和实际提交；附件 `packages.lock.json` 与
`build-inputs.json` 记录本次实际插件快照，两者与仓库原锁在 latest 模式下可能不同。
完整构建的报告见 Actions 的 `build-reports-*` Artifact。
固件和构建报告默认保留 7 天，需要长期保存时应及时下载；Artifact、正式 Release 和构建缓存分别管理。

Release tag 包含机型、源码、分支、时间、run ID 和 attempt；非 `main` 分支发布额外使用 `preview-` 前缀，并标为 prerelease。配置预览和 BUILD_ONLY 均不会创建 Release。
正式发布模式在长时间编译前预留指向本次 CI 提交的标签，标签冲突时停止；重试发布沿用成功编译任务的 Artifact 标识。
需要发布已验收的 BUILD_ONLY 产物时，运行 `Publish existing device artifact`，填入原始 `run_id`。同一运行保留多份固件时，使用 `artifact_id` 指定已验收的那份。晋升会核对归档和附件摘要，缺少标签时创建指向原构建提交的标签。两种发布路径均要求构建提交有成功的 Build logic checks。详细规则见 [构建流程](docs/BUILD-PIPELINE.md)。

构建检查可以发现配置和镜像内容问题；刷写、散热、无线和移动网络仍需对应硬件验证。

## 机型功能

### TR3000 512MB

- 构建期适配 512MB UBI 布局，布局锚点变化时停止构建。
- Aurora 主题与配置、OpenClash、[Arthur TurboACC](https://github.com/Arthur97172/luci-app-turboacc)、BBR、ttyd，以及 USB 网络和存储支持。
- F50 类 USB 网卡自动接入 WAN，按实际枚举结果绑定设备。
- 首次创建 USB WAN 时设置 DHCP、优先级和公共 DNS；已有协议、DNS、metric、停用开关及防火墙区域保留。旧 `F50` 接口改名时迁移已有配置和区域引用。
- LAN、主题和 TurboACC 默认值在构建期设置，保留配置升级时不会由旧的自定义首启脚本强制重设。
- TurboACC 使用 JavaScript / ucode 页面与后端，沿用当前 firewall4 和 SONiC FullCone；迁移旧开关并优先保留已有新版设置。BBR 由 TurboACC 管理，移除旧独立 BBR 服务，避免互相覆盖。
- LAN IPv6 路由器优先级规范为 `medium`，保留 relay 模式；不关闭 IPv6，已有客户端路由的变化还取决于 RA 更新。
- LuCI「系统 → 固件升级」和 `tr3000-ota` 提供 512MB 专用 OTA，升级前检查当前分区布局和目标镜像的布局证明。

两款固件均预装经过摘要核对的官方 ARM64 静态 Mihomo 内核。OpenClash 订阅需要自行导入。功能及分区要求见 [TR3000 文档](docs/TR3000-512.md)。

### AP3000M

- [FM350-GL 管理](https://github.com/LianXia233/luci-app-fm350)、首次开机 AT 口 / APN / CID 配置纠正、[AP3000M 风扇控制](https://github.com/LianXia233/luci-app-airpi3000m-fancontrol)和 ttyd。
- FM350 的 PDP / DNS 与打包适配、风扇页面确认和返回状态修正；locked / latest 分别使用对应的已审查 FM350 适配。关键文件在实际镜像中逐字节核对，Rust 后端检查目标架构。
- 拨号启动前按已保存的 CID / APN 和模组状态准备非 IMS 数据 PDP。AT 口定位会解析 USB 接口符号链接，再匹配当前模组的串口。
- 提供用户主动开启的 FM350 USB 2.0 兼容选项，默认保留 USB 3.0；具体排障步骤和限制见机型文档。
- FM350 运行期与开机自愈统一由 `fm350d` 内置守卫（`data_guard` / `net_guard` / `route_guard` / `unbound_policy`）接管，升级时自动清理旧版每 5 分钟 RNDIS 定时巡检；保留 `ap3000m-rdnis-guard` 供 `--check` / `--force` 手工诊断，且仅在 `data_guard` 和 `net_guard` 均显式关闭并主动调用时才执行脚本侧复位。
- [Arthur TurboACC](https://github.com/Arthur97172/luci-app-turboacc) 管理软件 / 硬件分载、WED、FullCone 和 TCP 拥塞算法，实际可用项取决于驱动与后端能力；首次导入现有设置，保留明确关闭和已选择的算法。RPS/XPS 与 MTU 探测继续由设备调优脚本负责。
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
`fm350.main.rdnis_watch=0` 关闭开机及定时巡检。可用 `ap3000m-rdnis-guard --check` 手动检查。
网页“任务已启动”表示进入后台下载 / 校验，后续错误会显示在页面；后台日志为 `/tmp/ap3000m-ota-operation.log`。

配置、安装与排障见 [AP3000M 文档](docs/AP3000M.md)。

## 维护与上游同步

日常维护在 `main` 上进行。配置和代码以仓库文件为准：

| 位置 | 用途 |
| --- | --- |
| `.github/workflows/TR3000.yml`、`AP3000M.yml` | 机型入口和默认参数 |
| `.github/workflows/DEVICE-CORE.yml` | 两款机型共用的构建、校验及发布流程 |
| `Profiles/<机型>.json` | 配置片段、插件选择、必需包和镜像检查要求 |
| `Profiles/sources.lock.json` | 主源码与 feeds 的完整提交 SHA |
| `Profiles/packages.lock.json` | 第三方插件的来源、提交和精确替换目录 |
| `Config/BASE.txt`、`Config/Features/`、`Config/<机型>.txt` | 基础、功能与设备配置；机型配置覆盖基础片段 |
| `Scripts/<机型>/private.sh`、`Files/<机型>/` | 机型源码适配和固件文件投放 |
| `.github/scripts/` | 版本锁、构建校验、发布清理及回归测试 |

公共 `Settings.sh`、`Handles.sh` 和工具链准备脚本仍由两款共用；修改公共逻辑后应验证受影响机型。
[WRT-CORE.yml](.github/workflows/WRT-CORE.yml)、[Packages.sh](Scripts/Packages.sh) 和 [PRIVATE.sh](Scripts/PRIVATE.sh) 保留供通用入口使用，两款独立入口不经过这条分发链。[脚本目录说明](Scripts/README.md)列出了两条路径。

**同步 CI 模板**与**更新固件源码**是两件事：

- 同步模板时审查合并差异，保留本仓库的 `DEVICE-CORE.yml`、机型定制和版本锁。`DEVICE-CORE.yml` 不会自动继承 `WRT-CORE.yml` 的更新。
- 源码 / feeds 与第三方插件各有独立版本模式，默认分别为 `latest` / `auto`，并记录每次使用的确切提交。核对 `source-lock.json`、`plugin-resolution.json` 和实际插件快照；需要固定输入时显式选 `locked`。latest 不自动修改仓库版本锁，更新锁需另行审查。
- 更新后先检查 Build logic checks 和受影响机型的 TEST，再用预览 run ID 做 BUILD_ONLY，验收后可晋升同一 Artifact。遇到上游不兼容应适配修复，不能靠删除功能或跳过校验放行。

两款默认的自动清理和缓存维护如下：

| 工作流 / 功能 | 行为 |
| --- | --- |
| [Build logic checks](.github/workflows/BUILD-CHECKS.yml) | 回归测试、真实 ucode / 文件锁与 FIT/DTB/squashfs 测试、Shell / JS / 工作流语法检查 |
| [Auto-Clean](.github/workflows/Auto-Clean.yml) | 每天香港时间 05:21，两款机型每渠道保留最近 3 个 Release；回收已核实归属、失败且最后活动超过 24 小时的流水线草稿，人工草稿/未识别机型/Git tag 保留；手动默认预览，`APPLY=true` 才删除 |
| 下载缓存 `dl` | 根据实际源码 / feeds / 插件提交、最终配置及适配后配方生成固定 key，精确命中不重复保存；旧缓存可补齐下载，内容仍由 OpenWrt 核对哈希 |
| Cargo 下载缓存 | 按源码包内的 `Cargo.lock` 摘要分代，保存 registry 索引、crate 归档和 Git 下载 |
| C/C++ 编译缓存 `.ccache` | 按宿主 OS 和 GCC 划分恢复前缀并滚动保存；编译失败也可保留已完成的缓存，取消的任务不保存 |
| 安装后的 host / toolchain（v3） | 按实际工具链源码、规则、有效配置和宿主依赖精确匹配；核对文件内容、权限、链接及原始完成 stamp，通过后仅规范化 tools / toolchain 源码时间。只保存 host / toolchain 和完整性清单，Rust 链接另行刷新 |
| 缓存异常 | 损坏或部分恢复时还原本轮干净宿主环境，告警后冷编；真实输入漂移或无法还原仍失败。封存、保存、服务或统计故障不阻断已验证固件 |
| 缓存轮换 | 下载 / ccache / Cargo 按机型和 ref 轮换；共享工具链按 ref / target 保护当前 key、最新两代及七天内条目。坏缓存仅精确删除已确认的 key，保护较新替代条目 |

工具链 key 不直接绑定机型、插件 / feeds 版本、Rust 或固件日期；这些变化若影响实际工具链输入，
仍会使缓存失效；只有实际依赖指纹相同才共享，同平台不等于必然共享。

恢复缓存不等于证明编译复用；整体耗时还受下载、ccache 与 runner 影响，不能将全部时差归给工具链缓存。

`Cache-Clean`、`AIROHA-ALL`、`MTK-ALL`、`OWRT-ALL`、`QCA-ALL`、`QCB-ALL` 当前在 GitHub 中手动禁用；文件保留，启用前需重新审查。
这些通用构建入口已移除 Auto-Clean 完成后的自动触发，AIROHA 也只保留手动入口。
`Cache-Clean` 的手动动作会清空仓库缓存，不用于日常轮换。工作流启停状态以 Actions 设置为准。
缓存不含完整 `build_dir`，构建时间受上游、命中情况和 runner 影响，不给出固定耗时保证。

## 刷机前核对

- 下载与机型和分区布局一致的固件，备份配置，按附件 `sha256sums` 核对文件。
- 执行 `sysupgrade -T /tmp/实际固件文件名.bin`；报错时先定位原因，避免用 `-F` 绕过检查。
- 跨源码或大版本升级时，应评估保留旧配置的兼容性；刷写期间不要断电。
- TR3000 硬改分区要求见机型文档；AP3000M 的 OTA 回退依赖已保存的镜像及校验记录。

## 致谢

- [VIKINGYFY/OpenWRT-CI](https://github.com/VIKINGYFY/OpenWRT-CI)：原始 CI 框架。
- [VIKINGYFY/immortalwrt](https://github.com/VIKINGYFY/immortalwrt)、[immortalwrt/immortalwrt](https://github.com/immortalwrt/immortalwrt)：固件源码。
- [OpenClash](https://github.com/vernesong/OpenClash)、[Arthur TurboACC](https://github.com/Arthur97172/luci-app-turboacc)。
- [Aurora](https://github.com/eamonxg/luci-theme-aurora)、[Aurora 配置](https://github.com/eamonxg/luci-app-aurora-config)。
