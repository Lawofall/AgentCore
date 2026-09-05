---
status: landed
code: apps/desktop/src/renderer/whiteboard/
related:
  - docs/03-AI核心/工具与能力系统.md
  - docs/02-架构/核心接口定义.md
skip_if:
  - 不涉及白板 / 画布引擎 / AI 读图摆元素
---

# AI 协作白板

> 桌面内页面（非独立 app）；空间 JSON 为真相。→ `apps/desktop/src/renderer/whiteboard/`。冲突以代码为准。

## 定位与差异

真白板（无限画布）✅ + **AI 团队进板**（读图 / 摆元素 / 照白板干活）**⏳ 未落地**。人主画、AI 助手。**不做**把团队图做成对话页第三宿主（否决 → [协作图 UX §六](/docs/04-前端/协作图与双视图UX.md)）、不做独立 app。空间 JSON 与「文本典范」不冲突——scene 本质空间，文本表达不了。重叠面（团队图/mermaid/文件列表）不重做。

**前台入口（现状）**：手动画布 ✅ 可用；老板命令栏与选区 AI 动作（整理 / 让团队实现 / 迭代）**⏳ 暂下线**，画布不摆「即将上线」命令栏空壳。`sendBoardTurn` / `board_ops` / `board_read` 协议与注册保留，恢复时不开新契约。工具箱文案：「画布可用 · AI 指挥即将上线」。跟进面 → [路线图 · AI 协作白板的 AI 指挥](/docs/01-产品/产品路线图摘要.md)。

主循环归属：服务「**收**」（把协作产物变成可空间编辑的东西）；AI 指挥未落地前，白板是**支线**创作工具而非交付主路径 → [定位 §二](/docs/01-产品/产品定位与品牌.md)。

## 关键决策

| # | 决策 |
|---|---|
| 形态 | desktop 路由 `/whiteboard`；工具箱入口，**不**进侧栏主导航 |
| 引擎 | **自研**原生引擎；`agentNode`/`artifactCard` 一等形状 |
| 归属 | board ∈ folder；独立 `boards` 表 + scene blob（<256KB JSONB，否则 S3）+ CAS version |
| AI 写 | 结构化 `board_ops`（catalog 工具），非裸 REST、非整图生成 |
| 读图 | 选区混合：结构→JSON，手绘/截图→栅格化→`VisionReader` |
| 团队 | 复用 `sendBoardTurn` + CEO `delegate`/`debate`；**零新编排/fold** |
| 前台闸 ⏳ | AI 入口暂关（命令栏 + 选区 AI）；画布与后端能力保留 |

**否决**：独立 web app；stock Excalidraw（美学+非原生节点）；tldraw（授权）；Fork Excalidraw（merge 税）；侧栏直达；`briefRegion` 原生形状（brief=选区/`frame`）。

## 引擎不变量

历史深拷不别名；仅 engine 持可变态；编组扁平；箭头绑定删除连带；CAS 冲突不覆盖；applier 仅画布打开时在册；M3 进度=`setOverlay` 瞬时浮层，终态才 `addElements` crystallize（按 runId 幂等）。配色走 token。→ `WhiteboardApi`（`whiteboard/types.ts`）。

## 接缝

| 面 | 约束 |
|---|---|
| 后端 | 又一个 run+SSE 客户端；发现改编排器 → 停（绊线） |
| 协议 | 复用现有 run fold；组件层不进 conformance |
| 读图 | `board_read` CLIENT_TOOL；视觉子调用 `role=vision` 独立入账；与对话贴图共用 `VisionReader`（组合 `vision` 槽，或槽空且 main 收图时复用 main，或 platform + `VISION_*`）；空配置干净失败。对话附件识图权威 → [平台 LLM 接入 · 识图槽](/docs/05-平台与运维/平台LLM接入.md) |
| 产物 | 文本卡先行；`@` 回工作区待文件信号；迭代空间留痕 |

桌面画布前台栅格化仍依赖打开白板的真实 CLIENT_TOOL 回填。读图路由 → [平台 LLM 接入 · 识图槽](/docs/05-平台与运维/平台LLM接入.md)。

## 风险护栏

通用画布够用即止、火力在 AI；拖累 Hero 即停。实时协作 v1 不做（可留 yjs 缝）。
