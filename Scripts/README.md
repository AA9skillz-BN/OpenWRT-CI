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
3. 分发器已在每次 `source` 前后 `set +e`，但仍建议私有脚本自己收好副作用。
4. **不要再往 `Scripts/PRIVATE.sh` 里加机型逻辑**——它只负责扫描。
5. 新增机型 = 新建 `Scripts/<机型>/private.sh`，无需改动任何公共文件。

## 为什么改成这样

原先所有机型逻辑都堆在 `Scripts/PRIVATE.sh` 里，靠 `WRT_CONFIG` 分支分发。
两个会话并行开发时，后提交的一方会把**整个文件**写回，直接覆盖另一方刚加的分支：

> 2026-10-25 实际事故：TR3000 分支在 AP3000M 的提交 `1833251` / `7aee419` 中被整体覆盖，
> 导致 `TR3000-512-VIKINGYFY-owrt-26.10.25-16.04.54` 这一版构建**全绿但产物残缺**——
> 分区补丁没打、TurboACC 没装、F50 脚本/DNS/RPS/BBR 全没投放。
> 最后是 `trim-release` 的 manifest 校验报出「缺少 luci-app-turboacc」才暴露。

拆开之后，改哪台机只动它自己的文件，冲突面为零。
