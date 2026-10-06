---
status: reference
code: apps/desktop/src/renderer/components/ui/
related:
  - .cursor/rules/color-tokens.mdc
  - .cursor/rules/desktop-layout.mdc
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
| L3 Pattern | 产品级壳 | 裁决卡、状态条… |

→ L2 导出：`components/ui/index.ts`

## L3 Pattern 映射

| Pattern | 场景 | 指针 |
|---|---|---|
| TabChip | 内容撑宽横条 tab（右坞 / 浏览器页签 / 文件详情） | L2 `TabChip`：闲置 = 图标+标题；关闭/弹出 overlay 标题尾 |
| DecisionCard | ask_user / approval / escalation | `DecisionCard` + 各 *Card |
| StatusStrip | 协作图状态条 | `StatusStrip.tsx` |
| PatternCardHeader | 后台任务卡头 | `BackgroundTaskCard.tsx` |
| SurfaceRow | 侧栏/文件树/对话管理/设置导航/工具箱按需行 | `SurfaceRow*`；库存行组合 → `pages/toolbox/InventoryRow.tsx` |
| ToolLine | 过程工具行 | `ToolLine` |
| LiveFlow | 过程活标题字形流光 | `data-live-flow` · `LiveFlow.tsx` |
| PanelShell | 右坞；Web 应用内浮窗；桌面真 OS 窗 | `SidePanel` / `FloatingPanelShell` + `SidePanelFloatHost`；真窗 `DesktopFloatWindowBridge` + `FloatWindowPage`（`#/float?cid&tab`） |
| SearchField / *SearchTrigger | 筛选 / 全局入口 | → CommandPalette |
| BrandMark | 登录/TitleBar/侧栏/关于 | `brand/BrandMark.tsx`（仅 Latin `font-brand`） |
| EmptyHint | 列表 / 网格页空态 | `EmptyHint`；**对话草稿**仍走 `DraftEmptyState` |
| PageHeader | 设置 / 枢纽页 | L2 `PageHeader`；深页 `back`；窄屏设置不重复 h1。工具箱目录不画可见标题 |
| CanvasShell | 铺满画布深页 | `layout/CanvasShell`：返回 IconButton + 标题槽 + 状态字 + 右动作 + 可选顶栏下横幅 + 铺满剩余画布。不抽引擎、不抽画布内工具条 |
| CatalogTile | 工具箱一律矮卡 | L2 `CatalogTile`。产品页 `density=compact`：名字、一行说明、右上控件。默认 `shelf` 仍是身份行 / 两行简介 / tags，当前工具箱不用。禁止再手写第三套磁贴 |
| SectionTabs | 同一页分区 | L2 `SectionTabs`；选中 `bg-accent` 胶囊 + 线框图标。**不是**右坞 `TabChip`，也不是 `SegmentedControl`（灰槽抬卡），也不是 inverse |
| SegmentedControl | 表单里就地二选一 / 三选一（登录↔注册） | L2 `SegmentedControl`；槽底抬起选中项。**不是** `SectionTabs`，也不是右坞 `TabChip` |

新卡优先 DecisionCard+Button。

## 裁决 / 表单底栏

动作组贴右下（`justify-end`），与 `DialogFooter` 同一锚点。提示 / hint 占左侧剩余。

次要键 `outline`（与主按钮同高），主键 `primary` 实心。底栏不加装饰图标（忙碌 spinner 除外）。取消不用 `danger` / 停止牌——拒答后果用左侧 hint。审批「拒绝」仍是危险操作，不在此列。

| 类型 | 顺序 | 例子 |
|---|---|---|
| 两键（主 + 取消） | 取消 → 主 | 澄清 |
| 多选项 | **只搬家、不换序** | 审批（允许… → 拒绝）、升级、终端确认 |

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

禁：`rounded-md/sm/2xl`、自定义 px 字号、调色板类、hex；桌面另拦动作菜单调用方手写 `min-w-*` / `w-*`（宽只走 L2）。桌面另拦 CSS 旁路；`check-no-localstorage` → `uiStorage`（前端技术 §9.11）。

**触达即收编**：不专项清扫裸 button。token 变更：改 `packages/design-tokens` → 桌面 check → 必要时更新 `color-tokens.mdc`。

### 登记例外

| 位置 | 原因 |
|---|---|
| `AgentNode` | 复合块；图上密度另档 |
| StatusStrip Recovery 文字链 | 故意弱操作 |
| 辩论赛事页 | 长期域例外，另一 IA/密度 |
| 文件类型图标（Material） | SVG 内嵌扩展名品牌色；入口 `FileTypeIcon` / `DirTypeIcon` |
| `DraftEmptyState` | 对话草稿空态（starter chips），不并 EmptyHint |
| 侧栏 / 抽屉一行空态 | 导航密度，不套居中 EmptyHint |

## 桌面 UI 统一

产品页跟桌面 renderer；配色单源仍是 design-tokens。权威 → [前端技术 §五](/docs/04-前端/前端技术与架构.md)。

跨页手感：人在聊天里学会的认路方式，走到文件 / 消息 / 设置仍管用。不是每页同一套格子。

| 不变量 | 说明 |
|---|---|
| 两套行，禁止第三套 | 导航 / 树 = `SurfaceRow`；设置内容 = `SettingRow`。后者已收设置子页四种行，不并进 SurfaceRow。工具箱组装页的交代货架（出厂卡在前，说明行右侧标「官方」；必带右上角标「必带」；有名字的夹是一格，点进去看）、准则、信封、插头都是 `CatalogTile` `density=compact`（名字、一行说明、右上开关或来源；空夹在夹内是一行左对齐虚线）。工具开关矮卡和市场货架也是 `CatalogTile` `density=compact`
| 认路选中 | 浅底 + 线框图标 `currentColor`：画布 `bg-accent text-accent-foreground`，侧栏 `bg-sidebar-accent text-sidebar-accent-foreground`。**否决**导航用 inverse（深底浅字）。inverse 给停止生成，以及输入框可发送（浅色近黑、暗色近白，与停止同族）。发送是实心键：可发 `inverse`，空/不可发 `muted`（灰底）；**否决**可发送走品牌蓝 `primary` 或幽灵 `default`。＋/语音仍幽灵 |
| 页头一行 | `PageHeader`：h1 单行 + 可选同行 meta / 动作；禁副标题。设置 / 枢纽页同一组件，用有没有 `back` 区分。工具箱不画可见标题：一页货架，顶上是全部装配的名字胶囊（预置和自建同一排、同一种胶囊，当前这份在这排，选中浅底 accent，星只标以后新建用的那份），吸顶留名字胶囊，新建、删除是图标，在胶囊右侧、搜索左侧，当前胶囊再点一次就地改名，市场在这一排右侧（返回交代）；胶囊下面同一行改主模型（占剩余宽度）、思考强度、上下文长度、组队队员和后台任务，改一档就写入；交代的搜索和新建、插头的添加在各节；工具和信封的全部打开 / 全部关闭与节标题同一行、靠右；以后新建也用这份留在模型这组；预置和自建都能删；不可见 h1 留滚动锚。画布深页走 `CanvasShell` → [页头层级](#页头层级) |
| 列表空态同一骨架 | 标题 + 可选一句说明 + 可选主操作 = `EmptyHint`。`DraftEmptyState` 仍是对话草稿特例 |
| 货架卡 | 工具箱市场、组装页工具、交代货架（出厂提示词的「官方」在说明行右侧，必带右上角标「必带」）、准则、信封、插头都是 `CatalogTile` `density=compact`（名字、一行说明、右上状态或来源；空夹在夹内是一行左对齐虚线）。格子 min 200。禁止再手写第三套磁贴。填槽由 `promptShelfTile`（市场卡 + 提示词读卡 + 官方条目）共用，禁止再按叶子手写一套。准则简介是固定句；官方 HOW 与出厂工具简介是 UI 专用句 `blurb`，不进模型目录。分类 tags 进读卡，不铺在矮卡上。点卡 = 居中 Dialog。市场进场整库：库顶官方精选，下按场景组折行网格（不封顶、不挂查看全部）。默认 `shelf` 仍是图标加两行简介，当前工具箱不用 |
| 盖层分工 | 确认 = `ConfirmDialog`；列表/树里起一个名字 = 行内改名（先落地「未命名…」再改；新建文件仍先填名，因为名字带着类型）。不在列表语境、或不止一个字段 = 居中 `Dialog` `size=md`（导入 / 克隆 / Composer 新建文件夹）。读卡 / 安装 = 居中 `Dialog` `size=lg`（市场 listing；提示词读卡同档）。挨着按钮 = 弹出菜单（宽 → [动作菜单宽度](#动作菜单宽度)）；一句结果 = Toast（协作感知应用内提示只写「对话名 + 要你干什么」，禁止贴卡正文）。命令面板 = `size=xl` + `position=top`；双栏阅读（收到的上下文）= `size=2xl`。对话框宽度只走 `DialogContent.size`，禁止再手写 `max-w-*`。铬条：`DialogHeader` + 可选 `DialogBody` + 有按钮才 `DialogFooter`（取消 `outline` → 主 `primary`）。**禁止**用对话框伪装右侧抽屉。对话坞只挂在聊天页 |
| 动作菜单宽度 | `DropdownMenu` / `ContextMenu` hug 最长一行：primitive `min-w-36 max-w-64`（144–256px）。对标 Material 3（宽=最长项、112–280dp）与 Apple HIG / VS Code / Linear（内容撑开、不留空板）。调用方禁止 `min-w-*` / `max-w-*` / `w-*`（高 `max-h-*` 除外）。名单 / 筛选 / 模型 / 工作区 chip 走 `Popover`，不冒充动作菜单。**否决**跟侧栏同宽、跟 Dialog `size` 对齐、调用方在 36/40/44/48/52 里现场挑一档、固定 `w-52`。细则 → [动作菜单宽度](#动作菜单宽度) |
| 分区 vs 打开的内容 | 同一页切块 = `SectionTabs`；表单里就地互斥 = `SegmentedControl`；右坞同时开着的文件/终端/浏览器 = `TabChip`。工具箱市场种类 = 筛选 chip，不是 `SectionTabs` → [前端 UX · 工具箱](/docs/04-前端/前端UX设计.md) |
| 状态 / 角色 / 所选胶囊 | 文字标签走 `Badge`（`pill`）。计数圆点、进度条、头像圈不是徽章 |
| 动作底栏 | Decision / Dialog 右下锚点；不扫输入框、工具条、协作图干预 |
| 新面先点名 L3 | 新页 / 新交付物须先说用哪套 Primitive / Pattern，禁止第三套壳。辩论室保持登记例外（控件仍用同一套按钮与徽章） |
| 消息操作行 | 窄屏常显；md+ hover / focus-within。助手复制·克隆对话·重新生成·收到的上下文·用量·复制排查包、用户复制·编辑与发送时刻、IM 回复与 IM 时间共用 `MESSAGE_ACTION_REVEAL_CLASS`。欠包的回合把「复制排查包」钉成常显。用户复制·编辑与助手底栏同一套 `IconButton`（tooltip 标名）。用户气泡脚 md+ 叠在气泡右下沿、宽随动作、不钉气泡内容宽；闲置不占流。**否决**脚上铺「复制」「编辑」胶囊、把脚宽锁在短句气泡上。助手完成时刻常显 |
| 文档 tab 动作 | 内容撑宽横条（VS Code 编辑器 tab）：关闭/弹出 **overlay** 标题尾，闲置不占槽。活跃 tab 常显 × 并留右槽（避免压住末字）；弹出仅 hover / focus-within。未保存 = 标题前 primary 圆点（`dirty`），不改 ×。`TabChip`。**否决** Chrome 均分宽 + 流内占位（右坞不是均分条）；**否决** `opacity-0` 仍占 `size-5` |
| 列表行动作 | 固定列宽（VS Code 资源管理器 / 对话行）：hover / focus-within 才进流，标题 truncate。**否决** 对流内槽 `opacity-0`（闲置仍吃标题宽）。对话行已是；文件夹头 / Git 悬停动作对齐。最近删除右侧由保留期 Badge 定宽，不套 overlay |
| 品牌字体 | 仅 BrandMark Latin；正文系统栈 |
| 品牌文案 | 权威 → [产品定位与品牌](/docs/01-产品/产品定位与品牌.md) |

**否决**：为窄屏另写主回复/文件/IM/设置；缺规范前大改色。触达即收编：不专项清扫其余「暂无…」行内提示 / 选择器空项。

## 页头层级

产品工作面（登录后桌面树）页头只回答「我在哪」，不解释产品是什么。行业对照：Apple HIG / Material 顶栏只有标题；Linear / Notion / VS Code / GitHub 设置同样是侧栏认路 + 单行标题，说明贴在控件或空态旁（Nielsen：帮助要在用得到的地方）。

| 位置 | 写什么 | 不写什么 |
|---|---|---|
| 导航（侧栏 / 设置左栏 / 窄屏返回栏） | 当前位置 | — |
| 页头 | 单行标题；右槽放页级动作；同行可放 **meta**（日期、计数） | 口号、目录复述、标题同义改写、这一页干什么 |
| 种类页 / 侧栏面板标题 | 单行；右槽动作 | 标题下第二行 lede |
| 区块 `SettingsSection.description` / 行 hint | **默认空**。只有不读会点错的约束，且不超过一句 | 职务说明书、标题同义改写、把确认框文案再抄一遍 |
| 空态 `EmptyHint` | 标题 `还没有{名词}`（搜索无命中：`没有匹配的{名词}`）；hint 最多一句下一步；页头已有主键且空态已有另一按钮则省略 hint | 说明书；行上才出现的动作；页头主键的同义复述；每次进页都读的 lede |
| 确认框 | 不可逆删除 / 停用的完整后果 | 常驻在区块头预告同一句话 |

**认路只留一处**：宽屏二级导航已点名时，内容区仍可留单行 h1（滚动锚 + 对齐动作）；窄屏返回栏已点名 → 不再画一遍 h1。工具箱：侧栏已点名，目录不画可见标题；一页货架，吸顶是全部装配的名字胶囊（当前这份在这排），新建、删除是图标，在胶囊右侧、搜索左侧，下面直接改模型，市场在右侧，不可见 h1「工具箱」留滚动锚。点另一颗胶囊换份，当前这颗再点一次就地改名。星只标以后新建用的那份。市场货架从右侧进入，返回交代。市场货架种类 = 筛选 chip。画布深页走 `CanvasShell`；手册仍是深页 `PageHeader`，浏览入口在设置 · 关于。→ [前端 UX · 工具箱](/docs/04-前端/前端UX设计.md)。

**会改变行动的句子下沉**，不挂在 h1 下。例：Git「公网仓不用配」进字段 hint；用量 BYOK 说明进表上方卡片；「须先接入服务商」进空态；改密码「其他设备失效」贴字段旁。空态对照 Linear / Notion：标题 + 按钮即可；没有新信息就省略 hint。不可逆删除 / 停用的完整后果只活在确认框。

**不适用**：登录页品牌锁、官网/下载页（系统要求如「Windows 10+」是选项说明，不是页头 lede）。管理后台同一文案检验：禁口号；`PageHeader.description` / `note` 只准条数、时间窗、筛选摘要、UTC 口径，不准职务说明书。图表「UTC 日切」算口径，保留。

**否决**：页头当产品说明书；窄屏顶栏与页内双标题；种类页 / 面板标题下常驻 lede。页头组件已经合成 `PageHeader`（壳的左右栏仍分家）。How → `desktop-layout.mdc`。

## 运动要点（细节权威 = design-tokens）

时长两档：`--motion-duration-fast` 150ms / `--motion-duration` 200ms。桌面 `@theme` 映射 `duration-fast` / `duration-normal`。活过程另有持续周期 `--motion-live-flow` 1.8s（标题字形流光、匀速，不是切换过渡）。尊重 `prefers-reduced-motion`（调用点 `motion-reduce:transition-none`；具名入场动画与 `[data-live-flow]` 见 `globals.css`）。

活过程三层，禁止再给单个工具发明心跳：**叙事活表面**（当前正在跑的工具行 / 折叠组头 / 空思考 / 组装参数）走 `data-live-flow` 标题字形流光；**内容在长**（思考或答案正文）只靠文字变长，答案尾光标走 `data-stream-caret`；**铬条状态**（协作图状态条 / 节点小圆标 / 侧栏灯）仍走转圈或灯，不铺流光。过程行不出秒表（记分牌在气泡脚 / 状态条 / 节点 face → [前端 UX · ProcessTimeline](/docs/04-前端/前端UX设计.md)）。折叠组只亮组头标题，展开后亮当前行标题。等你拍板保持静。减少动效时流光停、退回 `ThinkingDots`。**否决**逐字打字机；**否决**整行扫光带；**否决**已完成行继续闪。触达即用 token，不专项改已有 `transition-*`。

## 配色要点（细节权威 = color-tokens）

只用语义 token；禁止硬编码。用户面 / 执行态 / 分类三层与禁令 → `color-tokens.mdc`。tone 预设 → `ui/tone-presets.ts`。暗色：近中性表面 + 亮度层叠，品牌蓝只在 primary；数字 → `packages/design-tokens` `.dark`。

## 布局规格（细节权威 = desktop-layout）

宽度梯度、字号 4 级、圆角 3 级与禁令 → `desktop-layout.mdc`。豁免：对话/文件/设置/消息两栏壳、真全屏手册。

### 窄屏热区

宽屏图标按钮保持 28 / 32px。窄屏（<768px）或主指针为粗指针时，这些控件的可点区域至少 44px，字形不变：发送 / 停止、提问与审批的选项行和底栏、侧栏导航（新对话 / 文件 / 消息；窄屏不上工具箱）与对话行。文件树和过程行保持原密度。方形控件用 `touch-target`，通栏行用 `touch-row`。实现 → `globals.css`。

### 间距档

与字号 4 级、圆角 3 级并列。竖向块距只认下表四档。实现走 Tailwind 4px 网格（不另造 `--space-*`；间距不随主题变）。How → `desktop-layout.mdc`。

**竖向**（一列里的块，先定档再写类）：

| 档 | TW | px | 用在 |
|---|---|---|---|
| row | `2` | 8 | 同一单元内的行：过程行、caption↔答案、展开头↔体、工具组头↔子行 |
| block | `3` | 12 | 回合内独立槽：协作图 / 待拍板 / 插话；Markdown 段；警告条贴回合 |
| section | `4` | 16 | 同一列里不同区（ChatView 列） |
| turn | `6` | 24 | 消息与消息；页面 `py-6` |

过程折：`.process-turn` 默认 row；邻接 `.process-slot` 走 block。槽自己不叠外距（卡片 / 图宿主 `mt`/`mb` 清掉）。

**行内**（同一行控件，不是竖向档）：图标按钮组 `gap-0.5`；图标+标签 `gap-1.5`；表单横排 `gap-2`。

**否决** 竖向再发明 4px caption、6px（`mt-1.5` / `space-y-1.5`）当块距；把折叠栏当章节头（标题 `mt-4` / 16px）。**触达即收编**：设置 / 文件 / `SettingField` 的 `mt-1.5` 不专项清扫。

### 动作菜单宽度

动作列表（重命名 / 导出 / 删除）不是对话框。行业对照：Material 3 菜单宽 = 最长项、下限 112dp、上限 280dp；Apple HIG / VS Code / Linear / Notion 都是内容撑开。shadcn/Radix 默认 `min-w-[8rem]`（128px）再随内容长。

| | TW | px | 用在 |
|---|---|---|---|
| 撑开 | （不写 `w-*`） | 最长一行 | 定位层默认 shrink-to-fit；不写 `w-max`（和行内 `truncate` 互斥，会把长标签截进下限） |
| 下限 | `min-w-36` | 144 | 短中文（重命名）不缩成邮票；高于 Material 112dp、贴近 shadcn 128px |
| 上限 | `max-w-64` | 256 | 过长截断；低于 Material 280dp 帽 |

只写在 L2 `dropdown-menu` / `context-menu`。业务 `className` 只留定位（`align` / `side`）与事件。高可写 `max-h-*`。How → `desktop-layout.mdc`；lint → `scripts/check-ui-tokens.mjs`。

名单 / 筛选 / 模型 picker / 工作区 chip 走 `Popover`，自己定宽，不走本档。

**否决**跟侧栏同宽；跟 `DialogContent.size` 对齐；调用方现场写 `min-w-52` / 固定 `w-52`。
