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
| DecisionCard | ask_user / plan_review / approval / escalation | `DecisionCard` + 各 *Card |
| 推进卡 StageCard | 阶段推进（三键） | `StageCard.tsx`；**不**并 DecisionCard |
| StatusStrip | 协作图状态条 | `StatusStrip.tsx` |
| PatternCardHeader | 后台任务卡头 | `BackgroundTaskCard.tsx` |
| SurfaceRow | 侧栏/文件树/对话管理/设置导航 | `SurfaceRow*` |
| ToolLine / FinishReasonChip | 过程工具行 / 非正常收尾 | `ToolLine` / `finish-reason-chip` |
| PanelShell | 右坞；Web 应用内浮窗（B）；桌面真 OS 窗（C） | `SidePanel` / `FloatingPanelShell` + `SidePanelFloatHost`；真窗 `DesktopFloatWindowBridge` + `FloatWindowPage`（`#/float?cid&tab`） |
| SearchField / *SearchTrigger | 筛选 / 全局入口 | → CommandPalette |
| BrandMark | 登录/TitleBar/侧栏/关于 | `brand/BrandMark.tsx`（仅 Latin `font-brand`） |

新卡优先 DecisionCard+Button。

## 搜索 / 筛选 / 查找

| 词 | 入口 | 范围 |
|---|---|---|
| 搜索 | Cmd+K；侧栏假入口 | 跨对话/消息/文件夹+命令 |
| 筛选 | 页内 `SearchField` | 当前已加载项 |
| 查找 | Cmd+F FindBar | 当前会话已加载消息 |

**禁止**：侧栏真搜索框；页内 placeholder 写「搜索」；FindBar 暗示能搜未加载历史。

## Lint 与迁移

```bash
node scripts/check-ui-tokens.mjs --src apps/desktop/src/renderer
node scripts/check-ui-tokens.mjs --src apps/mobile/src
```

禁：`rounded-md/sm/2xl`、自定义 px 字号、调色板类、hex。桌面另拦 CSS 旁路；`check-no-localstorage` → `uiStorage`（前端技术 §9.11）。

**触达即收编**：不专项清扫裸 button。token 变更：改 `packages/design-tokens` → 两端 check → 必要时更新 `color-tokens.mdc`。

### 登记例外

| 位置 | 原因 |
|---|---|
| `AgentNode` | 复合块；图上密度另档 |
| StatusStrip Recovery 文字链 | 故意弱操作 |
| 辩论赛事页 / 白板工具条 | 长期域例外，另一 IA/密度 |
| StageCard | 推进 ≠ 裁决，独立 L3 |
| 文件类型图标（Material） | SVG 内嵌扩展名品牌色；入口 `FileTypeIcon` / `DirTypeIcon` |

## 桌面 UI 统一

他端只共享 design-tokens，不追组件统一。

| 不变量 | 说明 |
|---|---|
| 推进卡 ⊥ 裁决卡 | 禁硬并 |
| 品牌字体 | 仅 BrandMark Latin；正文系统栈 |
| 品牌文案 | 权威 → [产品定位与品牌](/docs/01-产品/产品定位与品牌.md) |

**否决**：跨端共享业务 React；全仓一次收编；缺规范前大改色；为统一而统一辩论室/白板。

## 配色要点（细节权威 = color-tokens）

只用语义 token；禁止硬编码。用户面提示：需要你→primary，可恢复中断→`noticeChipNeutral`（灰），危险操作→destructive。执行态：pending→muted、running→primary、completed→success、failed→destructive（红点；整卡提示走用户面档）。分类色板（agent / artifact / debate-side / git / 风险）≠ 状态。**手机仅浅色**（明确决策）。`accent` ≠ 成功色。三层细则 → `color-tokens.mdc`。tone 预设 → `ui/tone-presets.ts`。

## 布局规格（细节权威 = desktop-layout）

宽度：content `max-w-4xl` / canvas `max-w-[1200px]`；对话页阅读列 `max-w-3xl`；IM 会话列 `max-w-[40rem]`（消息+输入，≠ 对话页）。字号严格 4 级；圆角 3 级（8/12/pill）；按钮 sm/md。豁免：对话/文件/设置/消息两栏壳、真全屏手册。
