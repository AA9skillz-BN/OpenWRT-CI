# Scripts 目录结构与扩展约定

> 2026-10-25 重构。**所有机型请各自维护自己的 `private.sh`，不要再往 `PRIVATE.sh` 里写机型逻辑。**

## 扩展钩子

`Packages.sh` 末尾只 source 一个文件：

```bash
if [ -f "$GITHUB_WORKSPACE/Scripts/PRIVATE.sh" ]; then
	source "$GITHUB_WORKSPACE/Scripts/PRIVATE.sh"
fi
```

`PRIVATE.sh` 现在**只做目录扫描分发**：

```bash
for _ext in "${GITHUB_WORKSPACE:-/github/workspace}"/Scripts/*/private.sh; do
	[ -f "$_ext" ] || continue
	set +e
	source "$_ext"
	set +e
done
```

## 各机型的位置

| 机型 | 文件 | 判断条件 |
|---|---|---|
| AP3000M | `Scripts/AP3000M/private.sh` | `WRT_CONFIG` 含 `ap3000m` |
| TR3000 | `Scripts/TR3000-512/private.sh` | `WRT_CONFIG` 含 `tr3000` |

## 约定

1. 每个 `private.sh` **开头先判断 `WRT_CONFIG`**，不属于自己就 `return 0`：
   ```bash
   if [[ "${WRT_CONFIG,,}" != *"tr3000"* ]]; then
       return 0 2>/dev/null || exit 0
   fi
   ```
2. **判断放在 `set -e` 之前**，避免影响其它项目的 source。
3. 分发器已在每次 `source` 前后 `set +e`（2026-10-25 起还会 `set +u` 与 `set +o pipefail`），但仍建议私有脚本自己收好副作用。
4. **不要再往 `Scripts/PRIVATE.sh` 里加机型逻辑**——它只负责扫描。
5. 新增机型 = 新建 `Scripts/<机型>/private.sh`，无需改动任何公共文件。

## private.sh 三条硬规则（2026-10-25 加固）

所有 `private.sh` 都在**同一个 shell** 里被依次 `source`，所以光靠"各写各的文件"还不够，脚本本身要守规矩：

1. **早退用 `return`，不要 `exit`**
   `exit` 会直接终止 `Packages.sh`，让构建停在中途（后面的投放步骤全部丢失，而且往往还是"看起来在编"）。

2. **变量加机型前缀**（`_ap3000m_*`、`_tr_*`、`_x86_*`）
   所有机型共享同一个变量空间，同名变量会互相污染（例如后者覆盖前者的 `_dir`/`_bf` 之类）。

3. **收尾复位 shell 选项**
   如果脚本里用了 `set -euo pipefail`，结束前务必恢复：

   ```bash
   set +eu
   set +o pipefail
   ```

   分发器虽然也会复位，但**不要依赖它**——万一哪天分发器被改或漏了，踩雷的是别的机型。

> 为什么专门强调 `-u` 和 `pipefail`：`-e` 只影响单条命令的失败处理，而这两项会**残留到同一个 shell 的后续所有命令**。
> 2026-10-25 审计发现 `Scripts/TR3000-512/private.sh` 里 `set -euo pipefail` 会残留：目前 TR3000 按字母序排在最后所以没炸，
> 一旦新增 `X86/`、`ZYXEL/` 这类排在后面的机型，就会继承 nounset 出现"变量未定义→静默中断构建"。因此加固了分发器。

## 为什么改成这样

原先所有机型逻辑都堆在 `Scripts/PRIVATE.sh` 里，靠 `WRT_CONFIG` 分支分发。
两个会话并行开发时，后提交的一方会把**整个文件**写回，直接覆盖另一方刚加的分支：

> 2026-10-25 实际事故：TR3000 分支在 AP3000M 的提交 `1833251` / `7aee419` 中被整体覆盖，
> 导致 `TR3000-512-VIKINGYFY-owrt-26.10.25-16.04.54` 这一版构建**全绿但产物残缺**——
> 分区补丁没打、TurboACC 没装、F50 脚本/DNS/RPS/BBR 全没投放。
> 最后是 `trim-release` 的 manifest 校验报出「缺少 luci-app-turboacc」才暴露。

拆开之后，改哪台机只动它自己的文件，冲突面为零。

## 冻结清单（FROZEN，2026-10-25 定稿）

以下文件属于**基础设施**，除"修改本条约定"外**不要再动**：

| 文件 | 职责 | 谁需要改 |
| :--- | :--- | :--- |
| `Scripts/PRIVATE.sh` | 目录分发器（按序 source 所有 `Scripts/*/private.sh`） | **没有人**。新增/修改机型只动自己目录 |
| `Scripts/Packages.sh` | 通用插件克隆清单（上游文件） | 极少。机型专属插件请在自己的 `private.sh` 里 clone |
| `Config/GENERAL.txt` | 所有机型共用配置（WRT-CORE 固定追加） | 极少。机型专属包写 `Config/<机型>.txt` |
| `.github/workflows/WRT-CORE.yml` | 编译核心（所有机型共用） | 极少。机型差异走 `with:` 输入或 `private.sh` |
| `.github/workflows/Auto-Clean.yml` | Release 保留策略（按机型分组留 3 个） | 不需要。已按 tag 前缀 / `firmware-model` marker 通用化 |

> 必须改动其中之一时，两条硬要求：
> 1. **先取主干最新版再改**——禁止拿本地旧副本整文件覆盖（这正是 TR3000 事故的成因）；
> 2. 提交信息里写明**改什么、影响哪些机型、为什么不能在机型目录里解决**。

## 新增一个机型：照抄这份清单

以 `<机型>` 为目录名（AP3000M / TR3000-512 / …），**只新增文件，不改公共文件**：

| # | 要创建的文件 | 内容要点 |
| :--- | :--- | :--- |
| 1 | `Config/<机型>.txt` | `CONFIG_TARGET_*` + 设备选择 + 机型专属包。文件名即 `WRT_CONFIG` 的值 |
| 2 | `.github/workflows/<机型>.yml` | 薄壳：`uses: ./.github/workflows/WRT-CORE.yml`，在 `with:` 里给 `WRT_CONFIG / WRT_THEME / WRT_NAME / WRT_SSID / WRT_WORD / WRT_IP / WRT_REPO / WRT_BRANCH / WRT_SOURCE`；再加一个 `trim-release` job（裁附件 + 改说明 + **产物校验闸门**） |
| 3 | `Scripts/<机型>/private.sh` | **第一段必须是 `WRT_CONFIG` 守卫 + `return`**；变量统一 `<机型>` 前缀；结尾复位 `set +eu; set +o pipefail`；只做构建期动作（投放文件、打补丁、写构建标签） |
| 4 | `Scripts/<机型>/**` 或 `Files/<机型>/**` | 真正的脚本/页面/配置模板。二选一：`Scripts/` 里由 `private.sh` 逐条 `cp`，或按 `Files/<机型>/etc/**` 整目录拷进源码 `files/` |
| 5 | `docs/<机型>.md` | 设备/源码/文件位置/默认参数/改哪里/绝对不碰 |

**不要碰**：`Scripts/PRIVATE.sh`、`Scripts/Packages.sh`、`Config/GENERAL.txt`、`.github/workflows/WRT-CORE.yml`、`.github/workflows/Auto-Clean.yml`（见上表）。

**Release 约定**：tag 必须以 `<机型>` 前缀区分（如 `AP3000M-…`、`tr3000-512-…`），Auto-Clean 才能按机型分组只留最近 3 个。

**推送约定**：只提交自己机型目录下的路径；多人/多会话并行时先 `git fetch` 再改，提交信息写清影响面。

**构建约定**：手动触发；改了 `Config/<机型>.txt` 时先 `TEST=true` 跑一遍（约 10 分钟，只出配置不编固件），确认包名没被 `defconfig` 静默丢弃，再跑全量。

**验收约定**：构建末尾必须下载镜像校验 `tar` 合法 + 核对 manifest 里的关键包（这是"绿但残缺"事故唯一可靠的兜底）。
