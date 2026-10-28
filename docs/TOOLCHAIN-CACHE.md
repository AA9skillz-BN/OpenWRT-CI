# 工具链缓存

`DEVICE-CORE.yml` 为三个单机型流程共用此实现；通用 `WRT-CORE.yml` 不使用它。
下载缓存和 ccache 保持原有策略。安装产物缓存仅接受完整内容 key 精确命中，固定构建路径
仍为 `/mnt/build_wrt`。AP3000M/C8-688 只有实际依赖指纹完全一致时才共享；同平台不等于必然共享。

## 输入

`.github/scripts/toolchain_cache.py` 用 OpenWrt Make 规则展开实际 host tools、toolchain 列表、
stamp 路径、CPU/ABI、编译与链接参数及内核版本。读取这些源码实际引用的 CONFIG 符号，
包括 `CONFIG_PACKAGE_kmod-b43` 等例外；不使用容易漏项的手工配置白名单。

源码输入在所有设备补丁、配置解析之后计算：`tools/`、`toolchain/`、`include/`、上游
`scripts/`、`config/`、根 Makefile/rules/Config.in、`target/linux/generic/`（含内核版本/哈希、
通用补丁和文件），以及 Make 实际包含的 target/subtarget 等源文件。
Git 文件清单覆盖已修改文件和未忽略的新补丁，避免构建后生成的二进制文件污染指纹。
源码内容、文件类型、权限、符号链接计入 key，源码提交号和 mtime 本身不计入。

宿主输入包含 OS、架构、CPU 指令特性、系统包完整版本清单、GCC/glibc 版本、关键构建程序的
路径与内容哈希，以及显式编译环境变量。系统包清单和部分源码目录采用保守覆盖，可能产生
额外失效；优先保证兼容性，不能承诺所有工具链无关变化都保留命中。

本仓库测试、Profiles、机型名、feeds/插件版本、Rust 及固件日期不直接进入此 key；它们若
改变了上述有效输入，仍会失效。`build-inputs.json` 继续保留完整来源记录，另有
`toolchain-inputs.json` 解释本次工具链 key。

外部工具链、外部内核树、自定义内核 Git 源、工具配方动态拼接 CONFIG 依赖或未被覆盖的
外部源码链接会明确中止并要求审查依赖；不能为命中率放宽校验。

## 恢复与保存

缓存只保存 `staging_dir/host`、`staging_dir/toolchain-*` 和 `.ci-toolchain-cache.json`。
不保存 `hostpkg`、`target-*/host` 或 `build_dir`。Rust 链接继续在每次构建中单独刷新。

完整固件及镜像校验成功后，核对输入未改变、真实 tools/toolchain 完成 stamp 存在，再为
所有安装文件生成 SHA-256/权限清单及链接记录。链接不能依赖未缓存的 build_dir 文件；
外部单文件链接还记录目标内容哈希。仅完成上述步骤才保存，竞争保存同一不可变 key 不影响固件。

精确命中时再次核对输入、全部安装文件和链接、必要 stamp、runner 外部链接目标；不匹配会
明确失败，不跳过检查继续编译。通过后只规范化 `tools/`、`toolchain/` 源码时间为
2000-01-01，保留原有 stamp。随后用锁定上游自己的 `timestamp.pl` 确认能跳过总构建。
未命中不改变源码时间；`package/`、`target/` 正常重编逻辑始终保留。

## 证据与清理

`toolchain-cache.json` 记录精确命中、完整性验证、tools/toolchain compile 事件和活动时间。
活动时间合并重叠区间，不累加并行任务当作墙钟节省。命中后仍重编或缺少时间日志会警告。
这个警告只能发现未复用，不能证明没有错误跳过，因此同时保留输入失效测试和完整性校验。

共享 key 仅在本 ref、本 target 命名空间清理；当前 key、最新两代及全部七天内记录保留。
新缓存可见后回收本机型旧 v2 staging，不删除其他机型旧 staging 或 dl/ccache。

回归测试覆盖配置/规则/内核头文件/环境变化失效、插件/日期等变化保持 key、缺文件与损坏链接
拒绝复用、原 stamp 不被刷新，以及两套锁定上游真实 Make/stamp 规则在无 build_dir 时跳过构建。
该规则测试使用安装文件夹具，不代表完成了真实工具链冷/暖构建。实际收益需后续完整构建
精确命中后，从原始日志、`toolchain-cache.json` 和最终镜像验证一起确认。
