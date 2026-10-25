# OpenWRT-CI

基于 [VIKINGYFY/OpenWRT-CI](https://github.com/VIKINGYFY/OpenWRT-CI) 的自用固件编译仓库，用 GitHub Actions 云编译 ImmortalWrt 固件。

在原有多平台工作流的基础上，新增两条**单机型独立产线**：

| 机型 | 工作流 | 源码 | 默认后台 | 详细文档 |
| :--- | :--- | :--- | :--- | :--- |
| Cudy TR3000 v1（**512MB 闪存硬改版**） | `TR3000-512-VIKING-Standalone` | [VIKINGYFY/immortalwrt](https://github.com/VIKINGYFY/immortalwrt) `owrt` | `192.168.6.1` | [docs/TR3000-512.md](docs/TR3000-512.md) |
| AirPi AP3000M 5G CPE | `AP3000M` | [immortalwrt/immortalwrt](https://github.com/immortalwrt/immortalwrt) `master`（可改） | `192.168.10.1` | [docs/AP3000M.md](docs/AP3000M.md) |

固件下载：[Releases](../../releases)

---

## 快速开始

1. 打开 **Actions**，在左侧选择对应工作流。
2. 点击 **Run workflow**，选择分支后运行。
3. 编译成功后到 **Releases** 下载 `*-sysupgrade.bin`。
4. 刷机前核对校验值，并先执行 `sysupgrade -T /tmp/固件名.bin` 检查。

> 两条单机型产线都只能**手动触发**，推送代码不会自动编译。

---

## Cudy TR3000 512MB

> ⚠️ **仅适用于闪存硬改为 512MB、且 UBI 分区为 `0x5c0000` 起、长度 `0x1da40000` 的 TR3000 v1。**
> 原厂 128MB、256MB 版本或其他 U-Boot 布局**不能刷**。
>
> 反过来，这台 512MB 机器也**不要刷普通的 `cudy_tr3000-v1` 固件**：两者设备标识相同，`sysupgrade -T` 拦不住，刷入后 UBI 只剩 64MB，会无法启动，只能进 U-Boot 救砖。

**做法**：拉取 VIKINGYFY/immortalwrt `owrt` 分支源码，编译前用脚本修改 DTS 中的 UBI 长度和 `IMAGE_SIZE`（65536k → 485632k）。上游布局一旦变化，脚本会直接报错停止，不会产出错误固件。

**内置功能**

- Aurora 主题 + Aurora 配置插件，默认简体中文
- OpenClash（Mihomo 内核和订阅需刷机后自行下载、导入）
- TurboACC：软件 / 硬件 Flow Offloading、WED、FullCone NAT
- ttyd 网页终端（系统 → 终端）
- mt76 开源无线驱动
- USB 网络驱动：RNDIS / CDC Ethernet / CDC NCM（适配中兴 F50 等 USB 上网设备）

**默认设置**：后台 `http://192.168.6.1`，用户名 `root`，首次登录时设置密码。

**发布与校验**：编译完成后自动检查配置、镜像中的软件包清单和 sysupgrade 设备元数据，任一项不通过就不发布。Release 只包含 `sysupgrade.bin` 和 `sha256sums`，Tag 以 `tr3000-512-` 开头，不会抢占 Latest 标记。

**插件版本固定**：Aurora、Aurora 配置、OpenClash、TurboACC 都按完整 commit 拉取，不会跟随上游自动更新。要升级时修改 `Scripts/TR3000-512/` 中对应脚本里的 commit。

**编译缓存**：开启 ccache，缓存 `.ccache` 和 `dl`。首次编译约 1.5～2 小时，之后命中缓存会更快。

相关文件：

```
.github/workflows/TR3000-512-VIKING-Standalone.yml   工作流
Config/TR3000-512.txt                                软件包配置
Scripts/TR3000-512/                                  分区补丁、插件拉取、配置、校验、发布脚本
Files/TR3000-512/etc/uci-defaults/                   首次启动设置（地址、语言、主题、加速）
release-notes/TR3000-512.md                          Release 说明模板
```

---

## AirPi AP3000M

基于上游已支持的 `airpi_ap3000m`（MT7981B + eMMC），复用公共编译核心 `WRT-CORE.yml`。

**内置功能**

- FM350-GL 5G 模组管理（状态、拨号、锁频、短信、AT 透传）
- 风扇温控（自动温控 / 手动档位）
- ttyd 网页终端、Aurora 主题
- 首次开机自动识别 FM350 的 AT 口、数据承载 CID，并按 SIM 卡运营商选择 APN

**默认设置**：主机名 / Wi-Fi 名 `AP3000M`，Wi-Fi 密码 `12345678`，后台 `http://192.168.10.1`。

**运行参数**（Run workflow 时可填）：

| 参数 | 默认 | 说明 |
| :--- | :--- | :--- |
| `SOURCE` | `immortalwrt/immortalwrt` | 源码仓库 |
| `BRANCH` | `master` | 源码分支 |
| `PACKAGE` | 空 | 临时追加插件，多个用 `\n` 分隔 |
| `TEST` | `false` | 只生成配置文件，不编译固件 |

Release 只保留 `sysupgrade.bin` 和本次实际生效的 `Config-*.txt`。真机调试记录见 [docs/AP3000M.md](docs/AP3000M.md)。

---

## 原有多平台工作流

继承自上游，使用 VIKINGYFY/immortalwrt `owrt` 分支：

| 工作流 | 配置 |
| :--- | :--- |
| `MTK-ALL` | MEDIATEK-WIFI-YES / NO |
| `QCA-ALL` | IPQ60XX、IPQ807X（WIFI-YES / NO） |
| `QCB-ALL` | IPQ53XX、IPQ95XX |
| `OWRT-ALL` | AIROHA、ROCKCHIP、X86 |
| `WRT-TEST` | 手动选择任意配置和源码，用于测试 |

这几条工作流在 `Auto-Clean` 运行完成后会**自动触发**（即每天一次），也可以手动运行。默认后台 `192.168.10.1`，Wi-Fi `OWRT / 12345678`。

---

## 维护工作流

| 工作流 | 作用 |
| :--- | :--- |
| `Auto-Clean` | 每天香港时间 05:21 运行，TR3000 和 AP3000M **各自保留最新 3 个** Release；Git Tag 保留。手动运行默认只预览，勾选 `APPLY` 才删除。 |
| `Cache-Clean` | 每周一 04:00 清空全部 Actions 缓存（包括 TR3000 的 ccache，下次编译会变慢，不影响固件）。 |

---

## 目录结构

```
.github/workflows/   各工作流（WRT-CORE.yml 为公共编译核心）
Config/              各平台 / 机型的 .config 片段
Scripts/             公共脚本（Packages.sh、Settings.sh、Handles.sh、PRIVATE.sh）
Scripts/TR3000-512/  TR3000 专用脚本
Scripts/AP3000M/     AP3000M 专用首次启动脚本
Files/TR3000-512/    TR3000 固件内置文件
docs/                各机型详细文档
release-notes/       Release 说明模板
```

---

## 刷机须知

- 刷机前**备份配置**，并核对 `sha256sums`。
- 先执行 `sysupgrade -T` 检查；**不要用 `-F` 强刷**来绕过尚未查明原因的报错。
- 第一次从其他源码的固件升级过来时，建议**不保留配置**。
- TR3000：保留配置升级时，首次启动脚本仍会把后台地址重设为 `192.168.6.1`。
- USB WAN 和 OpenClash 代理流量不保证能走硬件加速。

---

## 致谢

- [VIKINGYFY/OpenWRT-CI](https://github.com/VIKINGYFY/OpenWRT-CI)：原始 CI 框架
- [VIKINGYFY/immortalwrt](https://github.com/VIKINGYFY/immortalwrt)、[immortalwrt/immortalwrt](https://github.com/immortalwrt/immortalwrt)：固件源码
- [vernesong/OpenClash](https://github.com/vernesong/OpenClash)
- [chenmozhijin/turboacc](https://github.com/chenmozhijin/turboacc)
- [eamonxg/luci-theme-aurora](https://github.com/eamonxg/luci-theme-aurora)、[eamonxg/luci-app-aurora-config](https://github.com/eamonxg/luci-app-aurora-config)
- [LianXia233/luci-app-fm350](https://github.com/LianXia233/luci-app-fm350)、[LianXia233/luci-app-airpi3000m-fancontrol](https://github.com/LianXia233/luci-app-airpi3000m-fancontrol)

U-Boot 等资源请参考上游仓库说明。
