---
status: landed
code: apps/desktop/src/renderer/
related:
  - docs/04-前端/前端UX设计.md
  - docs/03-AI核心/辩论编排设计.md
skip_if:
  - 只改协作图/双视图（读协作图与双视图UX）
---

# 辩论室 UX

> 入口：[前端 UX](/docs/04-前端/前端UX设计.md) · 编排 → [辩论编排设计](/docs/03-AI核心/辩论编排设计.md)（主持人循环 / 形态 / 收敛 / 站队会话内态等**不在此复述**）。

## 四、前端呈现

辩论前端 = **赛事页**（记分牌 + 剧本主列 + 终审舞台）；live 与收场同一 `toDebateModel` 流。入口：`TurnDetailPage?view=debate`（状态条「打开辩论室」）。→ `components/chat/debate/arena/`。

| 层 | 职责 |
|---|---|
| 记分牌 | 辩题/进度/VS/模型徽章（全页仅此一处常驻）/布局开关/站队 |
| 剧本主列 | 正反：轮次发言+质询；红队：finding 三拍；圆桌：点名串行。发言**无**模型徽章 |
| 终审舞台 | 唯一结论面：裁决卡 → 战果对照 → 留给你的 |

**庭前准备 UI 已退场**（热路径秒过、无任务单实质面）；后端 `debate_pretrial_*` / fold 暂留供台账与预算。旧 `pretrial:investigators:` 不入 `debate:` 命名空间。

**布局**：正反默认并排可切单栏；红队/圆桌恒单栏。阵营色 = 独立对立 token（`pro`蓝/`con`红），⊥ `--agent-N`。记分牌不 sticky。辩论回合默认落辩论室；协作图/对比平级 tab。

**约束**：纯渲染层，**不动** fold/conformance。

### 窄屏

走桌面辩论室同一套（`DebateArena` / `TurnDetailPage?view=debate`）；旧手机 `DebateView` 精简面已随 fold 退役。权威一句 → [前端 UX §十五](/docs/04-前端/前端UX设计.md)。

## 否决 / 退场

| 方案 | 理由 |
|---|---|
| IM 群聊单流 / 擂台对开 / 右坞裁判台 / ArgMap | 表达不了阶段·比分·裁决且抢宽 |
| 用户拍板 gavel 持久化 | 无人消费、与站队重叠 |
| 结构化 `DebateContinue` 收场后再辩入口 | 对 CEO 说话重开即可 |
| 辩论硬停 / `DEBATE_ROUND` 挂起卡 | 永不硬停；介入走 ambient 掌舵 |

## 团队图标记

状态条「辩论」pill；对立徽章；stance 分带对置。辩论全程**不内联聊天**——归辩论室 tab；聊天侧图默认可折叠，醒目 CTA 进赛事页。

## 介入与站队

掌舵 / 追问 → `SteeringPanel`（下一轮生效）；复盘 → `UserInterjection`。站队 = 会话内态、不持久化、不改 AI 裁决；终局软对照「你 vs AI」。