# Scripts 目录与机型扩展

当前同时保留两条调用路径。三款机型的完整构建阶段、版本锁和发布规则以
[构建流程](../docs/BUILD-PIPELINE.md)为准。

## 三款机型的当前流程

```text
TR3000.yml / AP3000M.yml / C8-688.yml
  → DEVICE-CORE.yml
  → bash Scripts/<机型>/private.sh
```

每次只执行当前机型的脚本，使用独立 bash 进程。机型钩子负责源码补丁、文件投放、
启动链接及构建标签；失败立即中止，不通过分发器共享 shell 状态。

插件由 `Profiles/<机型>.json` 选择，统一按 `Profiles/packages.lock.json` 安装一次。
源码和 feeds 默认由 `Profiles/sources.lock.json` 锁定。
配置按基础及功能片段、机型配置、可选 `Config/PRIVATE.txt`、手动 PACKAGE 的顺序合并。

维护位置：

| 内容 | 位置 |
| --- | --- |
| 设备选择与专属软件包 | `Config/<机型>.txt` |
| 配置片段、插件选择和镜像校验要求 | `Profiles/<机型>.json` |
| 机型补丁及投放入口 | `Scripts/<机型>/private.sh` |
| 要放进固件的文件 | `Scripts/<机型>/` 或 `Files/<机型>/`，由钩子投放 |
| 构建输入和默认参数 | `.github/workflows/<机型入口>.yml` |
| 插件、源码与 feeds 版本 | `Profiles/packages.lock.json`、`Profiles/sources.lock.json` |

改配置后先执行 TEST，再执行 BUILD_ONLY；发布前检查必需包、实际 FIT/DTB 和
squashfs 内容。独立发布 job 只处理本次 Artifact，不再使用旧的事后裁剪步骤。

## 保留的通用流程

```text
其他通用入口 → WRT-CORE.yml → Packages.sh → PRIVATE.sh
                                              → source Scripts/*/private.sh
```

`Scripts/PRIVATE.sh` 仍被通用 `Packages.sh` 引用，不能当作无用文件删除。
它只负责分发，不在这里添加机型专属逻辑。兼容 source 调用的钩子应在开头判断
`WRT_CONFIG`，使用 `return 0` 退出无关配置，并避免变量和 shell 选项影响其他机型。
三款新流程不经过这条链；通过旧通用入口调用机型钩子也不能代替新流程的完整验证。

`Packages.sh`、`Settings.sh`、`Handles.sh`、`Config/GENERAL.txt` 和通用工作流均有用途。
同步上游时审查变更，保留本仓库的定制，不整文件覆盖。
`DEVICE-CORE.yml` 不会自动继承 `WRT-CORE.yml` 的更新。

## 清理与自动触发

- `Auto-Clean.yml` 按可识别机型和正式 / 测试渠道分别保留最近三个 Release，保留 Git 标签。
- `Cache-Clean.yml` 已移除定时触发；手动执行会清空缓存，不能用于日常构建后的轮换。
- `MTK-ALL`、`OWRT-ALL`、`QCA-ALL`、`QCB-ALL` 已移除清理后自动编译的触发，文件保留供手动使用。
- 工作流是否被 GitHub 禁用属于仓库设置，应在 Actions 中核对，不能仅凭文件内容推断。
