# 项目维护约定

## 在线升级

用户希望保留当前 AP3000M 在线升级设计，后续编译或新增其他机型固件时，主动评估并优先复用这套能力。
这是后续固件的功能方向；当前请求不代表立即给所有机型安装 AP3000M 脚本。

设计与接入要求见 [docs/OTA-DESIGN.md](docs/OTA-DESIGN.md)。接入前核对机型渠道、镜像格式、
分区和升级入口、配置保留及运行依赖；保留下载校验、升级前检查、互斥、兼容版本和最终镜像验证。
无法确认适配时保留明确的限制，不以直接复制脚本或绕过校验交付。

## 构建验收

先看同一 CI 提交的 Build logic checks。用 TEST 保存配置和输入快照，完整构建可填写 PREVIEW_RUN_ID。
验收固件时核对 Artifact 摘要、实际配置、必需包、FIT DTB 和 squashfs。有发布授权时可晋升
同一 BUILD_ONLY Artifact。具体流程见 [构建说明](docs/BUILD-PIPELINE.md)。

共享 OTA 函数位于 Scripts/ota，修改后运行 `python3 .github/scripts/render-ota.py`，并核对两款独立脚本。
