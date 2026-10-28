# TR3000 / AP3000M / C8-688 构建流程

这三个入口调用独立的 `DEVICE-CORE.yml`。其他入口继续使用 `WRT-CORE.yml`
的原有通用流程；通用核心只保留凭据清理与稳定 action 版本等必要调整。
README 不作为构建配置来源。

## 阶段和失败条件

1. 固定使用 Ubuntu 24.04，安装依赖；不执行整机 full-upgrade。
2. 默认 `SOURCE_MODE=locked`，按 `Profiles/sources.lock.json` 拉取源码和
   feeds 的完整提交 SHA。拉取失败、URL 不一致或版本不一致直接失败。
   在 feeds 更新前排除未启用的 video、PON feeds。
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
   kernel/root 以及 fwtool 提取的设备和平台标识全部正确。进一步从 sysupgrade
   的实际 FIT kernel 提取默认 DTB：核对 compatible、TR3000 的 UBI reg、
   C8-688 的第二系统 rootfs bootargs。解开实际 squashfs 检查关键脚本、执行权限、
   启动链接、脚本内容和本次 build tag；证据保存为 `verified-payload.json`。
8. 编译 job 只有 `contents: read`，无全局 GitHub token，checkout 不持久化凭据。
   固件与说明通过本次运行的 Artifact 交给独立发布 job；发布 job 不 checkout 或
   执行构建代码，只复核校验和并发布。仅发布 job 拥有 `contents: write`。
   先上传 draft Release，附件上传成功后公开。
   不再查找“最新 Release”、下载固件、事后裁剪或修改说明。

AP3000M 另外打包 OTA 脚本和兼容元数据，均纳入校验和。最终 squashfs 必须含相同脚本及
`/usr/share/ap3000m-ota/runtime-abi`。修改 UCI/RPC/UI 契约或不兼容的运行依赖时必须递增
`Scripts/AP3000M/ota/runtime-abi`；独立 self-update 只允许相同兼容版本的正式版附件。

发布 job 为草稿写入仓库、run ID、attempt、提交、标签和时间标记，失败时只回收本次草稿。
Auto-Clean 保留每机型/渠道最近三版，并兜底回收已失败/取消且最后活动超过 24 小时的流水线草稿；
人工草稿、无法核实归属的旧草稿、活跃发布及 Git 标签保留。手动运行默认只预览。

非 main 分支发布始终标记 prerelease，不标记为 latest，tag 额外加 `preview-`
前缀，保证尚未更新、不会过滤 prerelease 的旧 OTA 客户端也不会匹配实验版本。
AP3000M OTA 排除
prerelease，默认只接受 `AP3000M-immortalwrt-master-` 渠道；旧配置中的
`AP3000M-` 宽前缀也会收窄。自定义 OTA 渠道可以显式配置其他前缀。
缓存清理在另一个仅有 contents/read、actions/write 的 job 中执行，不拿发布权限。

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

三款机型补齐 `audio-support`，否则当前上游会在 defconfig 中静默取消
`kmod-sound-core` 和 `kmod-usb-audio`；这三个包加入关键功能校验。
`config-resolution.json` 记录请求的包与 defconfig 实际结果的差异，并在日志中警告。
例如上游隐藏的 EHCI 选项、仅特定源码提供的 `fullconenat-sonic` 都不能仅凭
配置文件的一行 `=y` 判断生效；关键包丢失仍会直接失败。

`excluded_feed_packages` 仅去掉当前未选用、但存在上游 Kconfig 递归依赖的
`luci-app-fwknopd` 和 `squeezelite` 的安装入口，保留 feeds 源文件。即使包没选中，
它们的菜单依赖也会影响 defconfig。如果手动配置请求这些包，会明确报错；
上游修复后可审查并移除排除项。

增加或更新插件时，修改 lock 中的提交及精确替换目录，必要时更新 profile。
保留 TR3000 原来审阅过的 Aurora/OpenClash/TurboACC 提交；其余插件在迁移时
锁定当前提交。主源码和 feeds 独立锁定在 `Profiles/sources.lock.json`。
锁模式下修改 SOURCE/BRANCH 而不更新对应锁会失败；要试验其他源码，显式选择
`SOURCE_MODE=latest`，此时才跟随所选上游。无论哪种模式都保存实际版本。

更新上游时先在试验分支使用 latest 验证；将该次构建的 `source-lock.json`
对应机型条目更新到 `Profiles/sources.lock.json`，再用 locked 跑 TEST 和 BUILD_ONLY。
三款都通过后再合入 main。复查旧 Release 时可取其 `source-lock.json` 条目恢复
源码/feeds 锁，并一并恢复当时的 CI 提交、插件锁及实际配置。日期和 runner 等
环境仍会变化，因此相同源码版本不代表固件字节必然一致。

## 缓存

- `dl`：按实际源码/feeds/插件版本、最终配置和补丁后下载配方生成固定 key；精确命中不重复保存，
  未命中可借用本机型旧缓存补齐。下载由 OpenWrt 检查哈希。
- `.ccache`：独立滚动缓存，上限 1 GiB，每次运行产生新 key 以积累结果；
  即使编译失败也保留已完成的缓存。
- host/toolchain staging：使用 `wrt-v3-toolchain-<target>-<subtarget>-<hash>`，仅精确匹配，
  不使用前缀回退。缓存范围为 `staging_dir/host`、`staging_dir/toolchain-*` 和完整性清单，
  不含 `hostpkg`、目标平台的 `host` 或 `build_dir`。详细输入、验证和限制见
  [工具链缓存设计](TOOLCHAIN-CACHE.md)。
- 工具链 key 依据补丁后的源码内容、上游规则实际引用的配置、Make 展开的工具集合/CPU/ABI/flags，
  以及宿主构建依赖包、工具和显式 native 编译需要的 CPU 特性；完整构建版本记录仍单独保留。
  机型名称、feeds/插件版本、Rust 和本仓库测试/Profile 文件本身不进入工具链 key。
- 精确命中后先校验文件内容、权限、符号链接、外部工具及原有 stamp；通过后仅把
  `tools/`、`toolchain/` 源码时间规范化为固定旧时间，不创建或刷新 stamp。
  缓存校验失败时恢复预先保留的干净宿主环境，告警后冷构建；坏 key 由独立 job 定向删除。
  缓存封存、保存或报告失败不阻断已验证固件发布。命中后仍重编会产生警告和报告。
- 恢复后始终重建 Rust 工具链接，避免缓存指向旧 runner 路径。
- 下载和 ccache 成功后仅清理本机型、本 ref 的旧缓存，保留当前及最新一代；找不到当前缓存时
  不删回退缓存。共享工具链仅清理本 target、本 ref 中超过七天的旧缓存，保留当前、最新两代
  及全部七天内记录。新工具链缓存保存可见后，回收该机型旧 v2 staging。
  GitHub 仍可能按仓库容量配额自动淘汰缓存。

新缓存与旧流程使用不同命名空间，首次构建可能冷启动；不能直接用首次耗时衡量提速。

## 已知兼容处理

TR3000 改为标准 `kmod-tcp-bbr` 依赖，不再硬编码修改 config-6.18；启动脚本仍
在 TurboACC 之后按用户保存的 `bbr_cca` 开关启用 BBR。TurboACC 默认值在构建时
写入包配置，移除会重设 LAN IP、主题和加速选项的首启脚本；保留配置升级时不再
由这些脚本覆盖用户选择。512MB 分区补丁保留原始布局断言，上游变化时失败退出。

ImmortalWrt 于 2026-10-27 在提交
`21ae803cc274b52f98f99f0f75864b114b11daab` 中修复 fullcone 补丁。
已核对：旧补丁 SHA-256 `e043a5f7...` 对 firewall4 `bac1d656...` 应用失败，
修复后的补丁 SHA-256 `2b419dae...` 可正常应用。AP3000M 和 C8-688 不再
删除该补丁；未来遇到不兼容会构建失败，不以静默关闭功能换取绿灯。

C8-688 保留 `source-pin.txt` 的 auto / 跟随分支 / 完整 SHA 三种输入。
auto 服从 SOURCE_MODE；locked 下跟随版本锁，latest 下跟随分支。旧 SHA 如果
与版本锁冲突会失败，必须统一修改版本锁或显式选择 latest。拉取失败直接退出，
不再偷偷回退到另一个版本。DTS、第二系统布局、LAN/WAN、MAC、风扇和覆盖层
继续由机型钩子负责。MT5700M 固定为原 v3.0.7 对应提交，通过插件清单只安装一次。

## 同步 CI 模板与更新源码

从 CI 模板上游同步时，保留本仓库的 `DEVICE-CORE.yml`、机型清单和版本锁。
通用 `WRT-CORE.yml` 已撤出机型专属分支；Rust 准备脚本等仍有共用扩展，
正常合并双方修改，不应整文件覆盖。同步后先跑 Build logic checks，再跑
三个机型的 TEST；公共构建逻辑变化时应再跑 BUILD_ONLY。

OpenWrt/ImmortalWrt 源码和 feeds 默认不会自动漂移；更新版本锁后，工具链缓存按其实际
构建输入变化失效，设备与包清单校验可以阻止不完整镜像发布。上游接口发生变化时
仍然需要适配，检查通过也不等同于真机验证通过。

## 验证

本地和 PR 检查可执行 `python3 .github/scripts/test_device_build.py -v`，
并用 actionlint 检查工作流。真实的 feeds/Kconfig 依赖需要分别运行三个入口的
TEST 构建；完整 Rust、内核、驱动及固件生成还需要完整构建验证。
