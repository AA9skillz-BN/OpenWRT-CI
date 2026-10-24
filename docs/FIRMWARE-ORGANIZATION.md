# 多型号固件配置和发布

## 应用方式

将本包内容按目录合并到 OpenWRT-CI 仓库。更新三个已有工作流：
TR3000-512-VIKING-Standalone.yml、AP3000M.yml、Auto-Clean.yml；新增 AP3000M-CORE.yml。
AP3000M.txt、GENERAL.txt 和 PRIVATE.sh 无需变更。
WRT-CORE.yml 已恢复原版。AP3000M 改为调用专用 AP3000M-CORE.yml，发布与 TEST 改动仅在专用核心中维护。

## 配置结构

- Config/TR3000-512.txt：原已验证的 TR3000 编译配置。
- Scripts/TR3000-512/：源码分区修改、插件下载和配置检查。
- Files/TR3000-512/：首次启动设置；后台 192.168.6.1、简体中文、Aurora。
- Config/AP3000M.txt、Scripts/PRIVATE.sh：继续使用现有 AP3000M 配置。
- .github/workflows/AP3000M-CORE.yml：AP3000M 专用构建与发布核心。
- release-notes/：各型号独立介绍。
- Scripts/Release/：两个型号共用的发布和清理工具。

## 发布规则

每次成功构建分别创建 Release；Tag 含稳定型号、香港时间、运行编号及重跑编号。
不竞争 Latest 标记。AP3000M 的 TEST 模式只上传配置 Artifact，不发 Release。
构建详情自动附上源码、源码提交、配置提交及运行链接。
TR3000 继续保留原 Artifact，并额外发布 Release。

## 清理规则

每天香港时间 05:21，每型号按 published_at 保留最新三项；预发布也计入三项，草稿跳过。
型号由 Release 介绍中的 `<!-- firmware-model: 型号 -->` 决定。
旧 AP3000M-* 和 TR3000-512-* Tag 会自动归组，其余没有标记的旧 Release 保留。
不删除 Git Tag、不清理 Actions 运行记录。手动运行 Auto-Clean 默认只预览。

## 后续新型号

为新型号添加独立 Config、Scripts、Files（如需要）和 release-notes 文件。
其构建完成后调用 Scripts/Release/publish.py，并传入稳定且唯一的 --model。
同型号不同源码分支共用三项配额；需要分支各保留三项时使用不同 model。

## 验证范围

已验证 YAML/Python 语法、配置和启动文件与现有版一致、清理及发布参数模拟测试。
尚未在 GitHub 执行此次重构后的构建或实际发布/删除。建议先手动预览清理，
再运行 TR3000 构建验证发布；AP3000M 下次构建验证独立介绍。
