---
status: reference
code: apps/desktop/src/renderer/components/ui/
related:
  - .cursor/rules/color-tokens.mdc
  - docs/04-前端/前端UX设计.md
skip_if:
  - 只改业务逻辑不涉及组件层
---

# UI Pattern 索引

> 配色/布局硬规则 → `color-tokens.mdc`、`desktop-layout.mdc`；IA → [前端 UX](/docs/04-前端/前端UX设计.md)。

## 三层结构

| 层 | 位置 | 职责 |
|---|---|---|
| L1 Token | `packages/design-tokens` | 语义色、动画、身份色板 |
| L2 Primitive | `components/ui/` | Button、Card、Badge… |
| L3 Pattern | 产品级壳 | 裁决卡、推进卡、状态条… |

→ L2 导出：`components/ui/index.ts`

## L3 Pattern 映射

| Pattern | 场景 | 指针 |
|---|---|---|
| TabChip | 内容撑宽横条 tab（右坞 / 浏览器页签 / 文件详情） | L2 `TabChip`：闲置 = 图标+标题；关闭/弹出 overlay 标题尾 |
| DecisionCard | ask_user / plan_review / approval / escalation | `DecisionCard` + 各 *Card |
| 推进卡 StageCard | leftover 墓碑，不是开辩入口 | `StageCard.tsx`；**不**并 DecisionCard |
| StatusStrip | 协作图状态条 | `StatusStrip.tsx` |
| PatternCardHeader | 后台任务卡头 | `BackgroundTaskCard.tsx` |
| SurfaceRow | 侧栏/文件树/对话管理/设置导航 | `SurfaceRow*` |
| ToolLine | 过程工具行 | `ToolLine` |
| PanelShell | 右坞；Web 应用内浮窗；桌面真 OS 窗 | `SidePanel` / `FloatingPanelShell` + `SidePanelFloatHost`；真窗 `DesktopFloatWindowBridge` + `FloatWindowPage`（`#/float?cid&tab`） |
| SearchField / *SearchTrigger | 筛选 / 全局入口 | → CommandPalette |
| BrandMark | 登录/TitleBar/侧栏/关于 | `brand/BrandMark.tsx`（仅 Latin `font-brand`） |
| EmptyHint | 列表 / 网格页空态 | `EmptyHint`；**对话草稿**仍走 `DraftEmptyState` |
| PageHeader | 设置 / 工具箱子页 / 枢纽页 | L2 `PageHeader`；子页 `back`；窄屏设置不重复 h1 |
| CatalogTile | 工具箱首页 / 商店货架 / 工具图鉴 | L2 `CatalogTile` |
| SectionTabs | 同一页分区（自动化任务 / 收件箱） | L2 `SectionTabs`；**不是**右坞内容页签 |
| SegmentedControl | 表单里就地二选一 / 三选一（登录↔注册、提示词角色身份） | L2 `SegmentedControl`；槽底抬起选中项。**不是** `SectionTabs`，也不是右坞 `TabChip` |

新卡优先 DecisionCard+Button。

## 裁决 / 表单底栏

动作组贴右下（`justify-end`），与 `DialogFooter` 同一锚点。提示 / hint 占左侧剩余。

次要键 `outline`（与主按钮同高），主键 `primary` 实心。底栏不加装饰图标（忙碌 spinner 除外）；开工「就这样开做」可留火箭，计划复核「调整」可留铅笔。取消不用 `danger` / 停止牌——拒答后果用左侧 hint。审批「拒绝」仍是危险操作，不在此列。

| 类型 | 顺序 | 例子 |
|---|---|---|
| 两键（主 + 取消） | 取消 → 主 | 澄清 / 开工 / 计划复核（取消 · 调整 · 继续） |
| 多选项 | **只搬家、不换序** | 审批（允许… → 拒绝）、升级、终端确认、登录继续 |

**不**扫输入框发送、工具条、协作图干预。铬条（`border-t` vs `pl-6`）正交，触达再收。窄屏长按钮折行难看时跟对话框：竖排、主按钮在上。

## 搜索 / 筛选 / 查找

| 词 | 入口 | 范围 |
|---|---|---|
| 搜索 | Cmd+K；侧栏假入口 | 跨对话/消息/文件夹+命令 |
| 切对话 | Ctrl/Cmd+1–9 | 侧栏当前可见行（折叠组不计）；按住修饰键才显示行尾数字 |
| 筛选 | 页内 `SearchField` | 当前已加载项 |
| 查找 | Cmd+F FindBar | 当前会话已加载消息 |

**禁止**：侧栏真搜索框；页内 placeholder 写「搜索」；FindBar 暗示能搜未加载历史。

## Lint 与迁移

```bash
node scripts/check-ui-tokens.mjs --src apps/desktop/src/renderer
```

禁：`rounded-md/sm/2xl`、自定义 px 字号、调色板类、hex。桌面另拦 CSS 旁路；`check-no-localstorage` → `uiStorage`（前端技术 §9.11）。

**触达即收编**：不专项清扫裸 button。token 变更：改 `packages/design-tokens` → 桌面 check → 必要时更新 `color-tokens.mdc`。

### 登记例外

| 位置 | 原因 |
|---|---|
| `AgentNode` | 复合块；图上密度另档 |
| StatusStrip Recovery 文字链 | 故意弱操作 |
| 辩论赛事页 / 白板工具条 | 长期域例外，另一 IA/密度 |
| StageCard | 推进 ≠ 裁决，独立 L3 |
| 文件类型图标（Material） | SVG 内嵌扩展名品牌色；入口 `FileTypeIcon` / `DirTypeIcon` |
| `DraftEmptyState` | 对话草稿空态（starter chips / 协作提示），不并 EmptyHint |
| 侧栏 / 抽屉一行空态 | 导航密度，不套居中 EmptyHint |

## 桌面 UI 统一

产品页跟桌面 renderer；配色单源仍是 design-tokens。权威 → [前端技术 §五](/docs/04-前端/前端技术与架构.md)。

跨页手感：人在聊天里学会的认路方式，走到文件 / 消息 / 设置仍管用。不是每页同一套格子。

| 不变量 | 说明 |
|---|---|
| 推进卡 ⊥ 裁决卡 | 禁硬并 |
| 两套行，禁止第三套 | 导航 / 树 = `SurfaceRow`；设置内容 = `SettingRow`。后者已收设置子页四种行，不并进 SurfaceRow |
| 页头一行 | `PageHeader`：h1 单行 + 可选同行 meta / 动作；禁副标题。设置 / 工具箱子页 / 枢纽页同一组件，用有没有 `back` 区分 → [页头层级](#页头层级) |
| 列表空态同一骨架 | 标题 + 可选一句说明 + 可选主操作 = `EmptyHint`。`DraftEmptyState` 仍是对话草稿特例 |
| 货架卡 | 工具箱 / 商店 / 工具图鉴 = `CatalogTile`。禁止再手写第三套磁贴 |
| 盖层分工 | 确认 = `ConfirmDialog`；填一小段 = 居中 `Dialog`；本页详情 = 页内右侧栏（商店技能）；挨着按钮 = 弹出菜单；一句结果 = Toast。**禁止**用对话框伪装右侧抽屉。对话坞只挂在聊天页 |
| 分区 vs 打开的内容 | 同一页切块 = `SectionTabs`；表单里就地互斥 = `SegmentedControl`；右坞同时开着的文件/终端/浏览器 = `TabChip` |
| 状态 / 角色 / 所选胶囊 | 文字标签走 `Badge`（`pill`）。计数圆点、进度条、头像圈不是徽章 |
| 动作底栏 | Decision / Dialog 右下锚点；不扫输入框、工具条、协作图干预 |
| 新面先点名 L3 | 新页 / 新交付物须先说用哪套 Primitive / Pattern，禁止第三套壳。白板**画布工具条** / 辩论室保持登记例外（控件仍用同一套按钮与徽章） |
| 消息操作行 | 窄屏常显；md+ hover / focus-within。助手复制·重新生成、用户复制·编辑、IM 回复与时间共用 `MESSAGE_ACTION_REVEAL_CLASS` |
| 文档 tab 动作 | 内容撑宽横条（VS Code 编辑器 tab）：关闭/弹出 **overlay** 标题尾，闲置不占槽。活跃 tab 常显 × 并留右槽（避免压住末字）；弹出仅 hover / focus-within。`TabChip`。**否决** Chrome 均分宽 + 流内占位（右坞不是均分条）；**否决** `opacity-0` 仍占 `size-5` |
| 列表行动作 | 固定列宽（VS Code 资源管理器 / 对话行）：hover / focus-within 才进流，标题 truncate。**否决** 对流内槽 `opacity-0`（闲置仍吃标题宽）。对话行已是；文件夹头 / Git 悬停动作对齐。最近删除右侧由保留期 Badge 定宽，不套 overlay |
| 品牌字体 | 仅 BrandMark Latin；正文系统栈 |
| 品牌文案 | 权威 → [产品定位与品牌](/docs/01-产品/产品定位与品牌.md) |

**否决**：为窄屏另写主回复/文件/IM/设置；缺规范前大改色。触达即收编：不专项清扫其余「暂无…」行内提示 / 选择器空项。

## 页头层级

产品工作面（登录后桌面树）页头只回答「我在哪」，不解释产品是什么。行业对照：Apple HIG / Material 顶栏只有标题；Linear / Notion / VS Code / GitHub 设置同样是侧栏认路 + 单行标题，说明贴在控件或空态旁（Nielsen：帮助要在用得到的地方）。

| 位置 | 写什么 | 不写什么 |
|---|---|---|
| 导航（侧栏 / 设置左栏 / 窄屏返回栏） | 当前位置 | — |
| 页头 | 单行标题；右槽放页级动作；同行可放 **meta**（日期、计数） | 口号、目录复述、标题同义改写 |
| 区块 `SettingsSection.description` / 行 hint | 会改变下一步怎么点的约束 | 把整页说明书再抄一遍 |
| 空态 `EmptyHint` | 第一次面对空列表时怎么开始 | 每次进页都读的 lede |

**认路只留一处**：宽屏二级导航已点名时，内容区仍可留单行 h1（滚动锚 + 对齐动作）；窄屏返回栏已点名 → 不再画一遍 h1。工具箱子页：返回链 + 单行标题 + 动作，无副标题（hub-and-spoke；**否决**兄弟分段条顶替标题）。

**会改变行动的句子下沉**，不挂在 h1 下。例：Git「公网仓不用配」进字段 hint；用量 BYOK 说明进表上方卡片；「须先接入服务商」进空态。

**不适用**：登录页品牌锁、官网/下载页（系统要求如「Windows 10+」是选项说明，不是页头 lede）。管理后台同一文案检验：禁口号；统计口径 / 筛选摘要算 meta。

**否决**：页头当产品说明书；窄屏顶栏与页内双标题。页头组件已经合成 `PageHeader`（壳的左右栏仍分家）。How → `desktop-layout.mdc`。

## 运动要点（细节权威 = design-tokens）

时长两档：`--motion-duration-fast` 150ms / `--motion-duration` 200ms。桌面 `@theme` 映射 `duration-fast` / `duration-normal`。尊重 `prefers-reduced-motion`（调用点 `motion-reduce:transition-none`；具名入场动画见 `globals.css`）。**否决**逐字打字机；流式答案尾光标走 `data-stream-caret`。触达即用 token，不专项改已有 `transition-*`。

## 配色要点（细节权威 = color-tokens）

只用语义 token；禁止硬编码。用户面 / 执行态 / 分类三层与禁令 → `color-tokens.mdc`。tone 预设 → `ui/tone-presets.ts`。暗色：近中性表面 + 亮度层叠，品牌蓝只在 primary；数字 → `packages/design-tokens` `.dark`。

## 布局规格（细节权威 = desktop-layout）

宽度梯度、字号 4 级、圆角 3 级与禁令 → `desktop-layout.mdc`。豁免：对话/文件/设置/消息两栏壳、真全屏手册。
