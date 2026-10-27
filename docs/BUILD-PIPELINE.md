# TR3000 / AP3000M / C8-688 构建流程

这三个入口继续调用 `WRT-CORE.yml`，通过 `WRT_PROFILE` 显式启用单设备流程。
其他入口不传此参数，继续走原有通用流程。README 不作为构建配置来源。

## 阶段和失败条件

1. 固定使用 Ubuntu 24.04，安装依赖；不执行整机 full-upgrade。
2. 拉取所选上游分支；C8-688 若指定完整 SHA，先切换源码，再更新 feeds 和
   计算源码版本；在 feeds 更新前排除未启用的 video、PON feeds。
3. 根据 `Profiles/<机型>.json` 只拉需要的第三方仓库，使用
   `Profiles/packages.lock.json` 中的完整提交 SHA。插件只安装一次。
4. 通过独立 bash 进程执行机型 `private.sh`，投放补丁、首启脚本等。
   必需文件缺失或复制失败立即终止，不通过 source 共享 shell 状态。
5. 按“基础及功能片段 → 机型 → PRIVATE.txt → 手动 PACKAGE”合并配置，
   执行 defconfig，检查退出码和日志中的 Kconfig 错误，再验证唯一设备及必需包
   仍然选中。不能仅凭 make 的退出码判定 Kconfig 成功。
6. 非 TEST 模式下，使用 feeds 声明的 Rust 版本及 runner Go/LLVM，恢复缓存、
   下载、并行编译；失败时保留串行重试日志。
7. 发布前要求唯一 sysupgrade、与设备对应的 manifest、全部必需包、非空
   kernel/root 以及 fwtool 提取的设备和平台标识全部正确。
8. 只整理需要的固件与证据文件，先上传 draft Release，附件上传成功后公开。
   不再查找“最新 Release”、下载固件、事后裁剪或修改说明。

TEST 只生成经过 defconfig 检查的配置 Artifact，不创建 Release、不恢复和保存编译缓存。
同时执行工具链准备和缓存输入计算，以便在预览阶段发现工具链版本和指纹错误。
勾选 BUILD_ONLY 则执行完整编译、镜像校验并上传固件 Artifact，不创建 Release。

## 配置和插件维护

`Config/BASE.txt` 及 `Config/Features/*.txt` 是原 GENERAL 配置的拆分。
首次迁移保留全部现有配置选择，单元测试检查两者等价；本次没有擅自删除
Samba、USB 网卡、存储工具等功能。可在每个 profile 的 `config_fragments`
列表中单独移除不需要的功能组。

机型配置最后生效。`required_packages` 是不能通过手动 PACKAGE 关闭的关键功能；
确实需要移除关键功能时，必须同步修改此清单。

`excluded_feed_packages` 仅去掉当前未选用、但存在上游 Kconfig 递归依赖的
`luci-app-fwknopd` 和 `squeezelite` 的安装入口，保留 feeds 源文件。即使包没选中，
它们的菜单依赖也会影响 defconfig。如果手动配置请求这些包，会明确报错；
上游修复后可审查并移除排除项。

增加或更新插件时，修改 lock 中的提交及精确替换目录，必要时更新 profile。
保留 TR3000 原来审阅过的 Aurora/OpenClash/TurboACC 提交；其余插件在迁移时
锁定当前提交。插件锁定不会自动锁定主源码和 feeds：每次构建仍跟随所选分支，
实际版本通过 Release 附件和 build-reports 留存。

## 缓存

- `dl`：独立滚动缓存，下载由 OpenWrt 检查哈希。
- `.ccache`：独立滚动缓存，上限 1 GiB，每次运行产生新 key 以积累结果；
  即使编译失败也保留已完成的缓存。
- host/toolchain staging：严格匹配系统、主源码、feeds、配置、工具链版本、
  插件锁和构建脚本指纹，无前缀回退，不刷新 stamp 冒充已构建。
- 恢复后始终重建 Rust 工具链接，避免缓存指向旧 runner 路径。
- 成功后只清理本机型、本 ref 的旧缓存，保留当前及最新一代；找不到当前缓存时
  不删回退缓存。GitHub 仍可能按仓库容量配额自动淘汰缓存。

新缓存与旧流程使用不同命名空间，首次构建可能冷启动；不能直接用首次耗时衡量提速。

## 已知兼容处理

TR3000 改为标准 `kmod-tcp-bbr` 依赖，不再硬编码修改 config-6.18；启动脚本仍
在 TurboACC 之后启用 BBR。512MB 分区补丁保留原始布局断言，上游变化时失败退出。

AP3000M 仅对已知的 firewall4 bac1d656 完整提交与旧 fullcone 补丁 hash 组合
移除该补丁，并将处理记录写入 Release。其他组合按上游补丁正常编译，失败会红，
不会把未知组合当成兼容。该例外中的固件不含 firewall4 fullcone 支持。

C8-688 保留 `source-pin.txt` 的 auto / 跟随分支 / 完整 SHA 三种策略。
auto 继续按原策略移除 firewall4 fullcone 补丁，并在构建报告及 Release 中记录；
跟随分支模式不移除补丁。指定 SHA 时如果拉取失败，直接失败退出，不再偷偷回退到
另一个版本。DTS、第二系统布局、LAN/WAN、MAC、风扇和覆盖层继续由机型钩子负责。
MT5700M 固定为原 v3.0.7 对应提交，通过插件清单只安装一次。

## 同步 CI 模板与更新源码

从 CI 模板上游同步时，应保留本仓库的机型清单和公共核心扩展，正常合并双方修改，
不要用上游文件覆盖已修改的 `WRT-CORE.yml`、Rust 准备脚本等文件。新增文件降低了
冲突范围，但不能消除公共文件的合并冲突。同步后先跑 Build logic checks，再跑
三个机型的 TEST；公共构建逻辑变化时应再跑 BUILD_ONLY。

OpenWrt/ImmortalWrt 源码和 feeds 则在每次构建时跟随所选分支，工具链缓存会随
构建输入变化失效，设备与包清单校验可以阻止不完整镜像发布。上游接口发生变化时
仍然需要适配，检查通过也不等同于真机验证通过。

## 验证

本地和 PR 检查可执行 `python3 .github/scripts/test_device_build.py -v`，
并用 actionlint 检查工作流。真实的 feeds/Kconfig 依赖需要分别运行三个入口的
TEST 构建；完整 Rust、内核、驱动及固件生成还需要完整构建验证。
