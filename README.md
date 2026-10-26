# OpenWRT-CI

基于 [VIKINGYFY/OpenWRT-CI](https://github.com/VIKINGYFY/OpenWRT-CI) 的自用固件编译仓库：用 GitHub Actions 云编译 ImmortalWrt 固件，**每个机型一条独立产线**，互不干扰。

固件下载：[Releases](../../releases)

---

## 当前机型

| 机型 | 工作流 | 源码 | 默认后台 | 文档 |
| :--- | :--- | :--- | :--- | :--- |
| AirPi AP3000M 5G CPE | `AP3000M.yml` | [immortalwrt/immortalwrt](https://github.com/immortalwrt/immortalwrt) `master` | `192.168.10.1` | [docs/AP3000M.md](docs/AP3000M.md) |
| Cudy TR3000 v1（**512MB 闪存硬改版**） | `TR3000.yml` | [VIKINGYFY/immortalwrt](https://github.com/VIKINGYFY/immortalwrt) `owrt` | `192.168.6.1` | [docs/TR3000-512.md](docs/TR3000-512.md) |

单机型产线都是**手动触发**，推送代码不会自动编译。

---

## 架构：一次构建的执行链

```
.github/workflows/<机型>.yml        薄壳：只提供参数 + 产物校验
      └─ uses ─→ .github/workflows/WRT-CORE.yml      公共编译核心
                   ├─ Custom Packages → Scripts/Packages.sh        通用插件克隆
                   │                     └─ source Scripts/PRIVATE.sh   （分发器）
                   │                              └─ Scripts/<机型>/private.sh   ← 机型专属逻辑
                   ├─ Custom Settings → Config/<机型>.txt + Config/GENERAL.txt
                   │                  → Scripts/Settings.sh / Scripts/Handles.sh
                   └─ 编译 → 打包 → 发布 Release
```

**机型之间零共享**：新增/维护任一机型，只改 `Config/<机型>.txt`、`.github/workflows/<机型>.yml`、`Scripts/<机型>/`、`docs/<机型>.md`。

### 冻结清单（7 项，勿改）

构建链路依赖以下公共文件，改动会影响**所有**机型：

`Scripts/PRIVATE.sh`、`Scripts/Packages.sh`、`Scripts/Settings.sh`、`Scripts/Handles.sh`、`Config/GENERAL.txt`、`.github/workflows/WRT-CORE.yml`、`.github/workflows/Auto-Clean.yml`

机型差异一律写进自己的目录（见 [Scripts/README.md](Scripts/README.md) 的「新增机型 checklist」）。

### 构建规则

| 规则 | 说明 |
| :--- | :--- |
| Release tag | 必须带机型前缀（`AP3000M-…` / `TR3000-512-…`），Auto-Clean 按前缀分组 |
| 产物校验 | 每个机型自带 `trim-release` job：下载镜像校验 tar、核对 manifest 关键包，缺包即失败 |
| 改配置 | 先跑 `TEST=true`（只出配置，约 10 分钟）确认包没被 `defconfig` 静默丢弃，再全量编译 |
| 提交范围 | 只提交自己机型的路径；多会话并行时先取最新再改 |

---

## AirPi AP3000M

MT7981B + MT7976（WiFi6 DBDC）+ eMMC，固件产物为 `squashfs-sysupgrade` tar。

**内置功能**

- FM350-GL 5G 模组管理（状态、拨号、锁频、短信、AT 透传）
- **首次开机自动纠错**：AT 口（`/dev/ttyUSB3`）、数据承载 `cid=2`、按 IMSI 选 APN（CMNET/CTNET/3GNET）
- **一键 OTA**：LuCI「系统 → 固件升级」+ 命令行 `ap3000m-ota`（`check / list / download / upgrade / self-update`），支持保留/不保留配置、回滚到本地任一新旧版本、每日自动检查；下载走镜像、强制 sha256
- **RNDIS 端点自愈**：开机等拨号完成后检测「有地址但收包为 0」→ 自动 `AT+CFUN=1,1` → 重定位 AT 口 → 重启 fm350d；另有 cron 每 5 分钟运行期巡检
- **数据面调优**：RPS/XPS 双核分摊、`txqueuelen 2000`、BBR、软件 Flow Offloading、MTU 实测（需 `iputils-ping`）
- ttyd 网页终端、风扇温控、Aurora 主题
- 构建侧自带产物闸门（9 个关键包）

**默认设置**：主机名 / Wi-Fi `AP3000M`，Wi-Fi 密码 `12345678`，后台 `http://192.168.10.1`。

**运行参数**（Run workflow 时可填）

| 参数 | 默认 | 说明 |
| :--- | :--- | :--- |
| `SOURCE` | `immortalwrt/immortalwrt` | 源码仓库 |
| `BRANCH` | `master` | 源码分支 |
| `PACKAGE` | 空 | 临时追加插件，多个用 `\n` 分隔 |
| `TEST` | `false` | 只生成配置文件，不编译固件 |

Release 只保留 `sysupgrade.bin` 和本次实际生效的 `Config-*.txt`。真机调试记录见 [docs/AP3000M.md](docs/AP3000M.md)。

---

## Cudy TR3000 512MB

> ⚠️ **仅适用于闪存硬改为 512MB、UBI 分区起点 `0x5c0000`、长度 `0x1da40000` 的 TR3000 v1。**
> 原厂 128MB / 256MB 或其他 U-Boot 布局**不能刷**；反过来这台 512MB 机器也不要刷普通 `cudy_tr3000-v1` 固件——设备标识相同，`sysupgrade -T` 拦不住，刷入后 UBI 只剩 64MB 会无法启动。

**做法**：拉取 VIKINGYFY/immortalwrt `owrt` 源码，编译前用 `Scripts/TR3000-512/patch-layout.sh` 修改 DTS 的 UBI 长度与 `IMAGE_SIZE`；上游布局一变脚本直接报错停机，不会产出错误固件。

**内置功能**：Aurora 主题与配置、OpenClash、TurboACC（软/硬件 Flow Offloading、WED、FullCone NAT）、ttyd、mt76 开源驱动、USB 网络驱动（RNDIS / CDC Ethernet / CDC NCM，适配 F50 类 USB 上网设备），以及 F50 即插即用（自动探测 USB 网卡并接成 WAN、不用模组自带 DNS、并入 wan 防火墙区）。

**默认设置**：后台 `http://192.168.6.1`，用户名 `root`，首次登录设置密码。

**版本固定**：Aurora、Aurora 配置、OpenClash、TurboACC 都按完整 commit 拉取，不跟随上游；升级时改 `Scripts/TR3000-512/` 里对应脚本中的 commit。

相关文件：

```
.github/workflows/TR3000.yml        工作流
Config/TR3000-512.txt               软件包配置
Scripts/TR3000-512/                 分区补丁、插件拉取、构建钩子
Files/TR3000-512/etc/               首次启动设置、F50 联网脚本
docs/TR3000-512.md                  详细说明
```

---

## 维护工作流

| 工作流 | 状态 | 作用 |
| :--- | :--- | :--- |
| `Auto-Clean` | **启用**，每天 05:21（CST） | 每个机型保留最新 3 个 Release（按 `firmware-model` 标记或 tag 前缀分组），Git tag 保留；手动运行默认只预览，`APPLY=true` 才删 |
| `Cache-Clean` | **已禁用** | 原为每周日 `gh cache delete --all`，会把所有机型构建缓存清空，导致次次冷构建 |
| `MTK-ALL` / `OWRT-ALL` / `QCA-ALL` / `QCB-ALL` | **已禁用** | 原由 `Auto-Clean` 链式触发的通用多机型固件构建，每天产出约 17 GB |
| `WRT-CORE` | 启用 | 公共编译核心（可复用工作流） |
| `WRT-TEST` | 启用（手动） | 任选配置与源码做试编译 |

> 停用 workflow 请用「禁用」而不是删文件：上游仓库有这些文件，同步时会被加回并**重新启用**。

### 构建缓存

- 缓存内容：`.ccache` + `staging_dir/host*` + `staging_dir/tool*`（**不含 `build_dir`**，所以 Rust/Go 中间产物不缓存）
- 冷构建约 3.5～6 小时，暖缓存约 45～60 分钟
- 只有**成功**的构建才会保存缓存；键包含上游 HEAD sha

---

## 目录结构

```
.github/workflows/   各工作流（WRT-CORE.yml 为公共编译核心）
Config/              各机型 .config 片段（另有 GENERAL.txt 对所有机型生效）
Scripts/             公共脚本：Packages.sh / Settings.sh / Handles.sh / PRIVATE.sh（分发器）
Scripts/<机型>/      机型专属逻辑（private.sh + 具体资产）
Files/<机型>/        需要直接放进固件 rootfs 的文件
docs/                各机型详细文档
```

---

## 刷机须知

- 刷机前**备份配置**并核对 sha256；先执行 `sysupgrade -T` 检查，**不要用 `-F` 强刷**绕过报错。
- 从其他源码的固件首次升级过来，建议**不保留配置**。
- AP3000M：升级可用 LuCI「系统 → 固件升级」或 `ap3000m-ota upgrade`；平台无 A/B 分区，刷写期间不要断电。
- TR3000：保留配置升级时，首次启动脚本仍会把后台地址重设为 `192.168.6.1`。
- USB WAN 与代理流量不保证能走硬件加速。

---

## 致谢

- [VIKINGYFY/OpenWRT-CI](https://github.com/VIKINGYFY/OpenWRT-CI)：原始 CI 框架
- [VIKINGYFY/immortalwrt](https://github.com/VIKINGYFY/immortalwrt)、[immortalwrt/immortalwrt](https://github.com/immortalwrt/immortalwrt)：固件源码
- [vernesong/OpenClash](https://github.com/vernesong/OpenClash)
- [chenmozhijin/turboacc](https://github.com/chenmozhijin/turboacc)
- [eamonxg/luci-theme-aurora](https://github.com/eamonxg/luci-theme-aurora)、[eamonxg/luci-app-aurora-config](https://github.com/eamonxg/luci-app-aurora-config)
- [LianXia233/luci-app-fm350](https://github.com/LianXia233/luci-app-fm350)、[LianXia233/luci-app-airpi3000m-fancontrol](https://github.com/LianXia233/luci-app-airpi3000m-fancontrol)

U-Boot 等资源请参考上游仓库说明。
