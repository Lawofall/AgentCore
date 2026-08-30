---
status: landed
code: apps/server/agentcore/runtime/runs/
related:
  - docs/03-AI核心/运行时总览.md
  - docs/03-AI核心/编排器与CEO主Agent.md
skip_if:
  - 只改 CEO delegate 字段（读编排器）
---

# Agent 协作模式

> **权威**：协作哲学、通信、`escalate` / handoff、冲突裁决。编排字段 → [编排器](/docs/03-AI核心/编排器与CEO主Agent.md)；辩论 → [辩论编排](/docs/03-AI核心/辩论编排设计.md)。
>
> **主循环归属**：「**组**」（队员之间怎么协作）；`escalate` 阻塞升级是「**拍**」的入口之一 → [主循环](/docs/01-产品/产品定位与品牌.md)。
>
> → 见代码: `apps/server/agentcore/runtime/runs/`

## 一、哲学

Multi-Agent First：组合优于堆叠；单 Agent = 无成员的 Team（统一执行路径）；委派一等公民（depth&lt;3 默认 `delegate`，`replan` 在已有子计划后挂上；depth=3 叶子；单 lead ≤4 sub）；形状由 `depends_on` 数据决定，非独立模式枚举。

| 范式 | 表示 |
|---|---|
| 串行 / 并行 / 混合 | `depends_on` DAG |
| 辩论 / 审查 | `debate` 工具内主持人循环（底层仍普通 DAG） |

## 二、通信：不直连

上游产物经调度器注入；被动通道 = 扇出感知 / 拓扑 / `escalate`。共享口径开局走 `team_brief`；完整成稿互吃走 `depends_on` 或短规格岗。无 worker 侧向广播。

**否决** Agent 直聊：成本翻倍、不可观测。**否决** 便签墙（波内 `post_note` 黑板）：独立价值只剩「无规格并肩时的边信道」，与「墙不代替 `depends_on`」互拆；对齐用 brief / DAG / 落盘 / `escalate`。

### `escalate`

worker 唯一向上通道。`blocking=false`（默认）= 已有合理默认、报后按假设续跑、主管收尾纠偏；`blocking=true` = 猜错作废 / 用户要不确定就问 / 只有上级能定 → 挂起求决（须写 assumption；默认无限期等 +「按假设继续」按钮）。经典路径直挂**用户**（否决挂 CEO——会死锁）；协调模式例外：CEO 波内存活 → 等 `resolve_escalation`（单 worker 同样进协调，一并适用）。等 CEO 时该队员不算短调用 in-flight，wait 不得空等该队员。仅嵌套 lead / 成篇套餐提纲把关 / 画布人工把关等阻塞路径永不走 resolve——那时 CEO 卡在 `delegate` 内，挂 CEO 必死锁。快跑还是停下由 **worker 按题自选** `blocking`（省着用、该停别装非阻塞），不设用户总开关。

前端分卡：真·非阻塞 escalate →「边干边上报」+「暂定假设」；引擎早停 / 硬顶打转（wire `source=validation_thrash|ceiling_backstop`）→「卡住早停」，**不**冒充边干边上报或「已按假设继续」。真挂起 →「请你拍板」。

| kind | 语义 |
|---|---|
| `normal` | 普通上报 |
| `scope` | 职责偏离 → 波边界操舵 |
| `dep` | 缺尚不存在的输入 → `replan(add)` |

### 交付三面（正文 / 产出 / 简报）

一次交付分三条通道，各装一类信息、各有唯一读者——**按信息类型切，不按详细程度切**：切成「长版 / 短版」时短版必是长版子集，重复无法靠提示词消除。

| 通道 | 装什么 | 读者 |
|---|---|---|
| **产出**（`deliverable`：落盘文件；`form=prose` 时即正文） | 事实的唯一完整载体，可寻址、可回读 | 要完整内容的人；按需回读的机器 |
| **正文**（`RunState.content`） | 给人的说明：结论、根因、关键取舍、意外、怎么用 | 人；叶子 + `form=prose` 另加 CEO（见下） |
| **简报**（`handoff` → `debrief`） | 接力状态 + 一行标题 | 机器：下游 worker、CEO 综述、计划复核卡。人看侧栏时挂在该队员的成功 `handoff` 工具行，不另开页脚（降级简报除外）→ [前端 UX · 详情面板](/docs/04-前端/前端UX设计.md#十详情面板右坞) |

铁律：正文与简报**只指向产出、不复述其内容**——`form=files` 时正文只交代路径 / 怎么运行 / 关键取舍，落盘产物才装完整说明。`form=prose` 时产出 ≡ 正文；简报要不要再带结论见下节矩阵。

下游整合（扇入写总稿）：引擎把上游落盘路径注入「前置结果」；终端环**先读这些路径再写**，不把开工做成全仓勘探。CEO 派单应点名路径；缺稿用同一整合员续派。**否决**「整合员必须 `file_write`」硬闸。

### `handoff`

收尾接力契约（非正文复述）。有下游依赖则强制；叶节点仅有增量才写。门禁不足则合成降级 debrief。

**简报能否去结论化，取决于 CEO 读不读得到正文。** 这条耦合横跨写侧提示词与读侧综述，单看一边看不出来：

| 节点 | CEO 读什么 | 简报 |
|---|---|---|
| 叶子 + `form=prose` | 简报打头 + 正文（整份 allowance） | 去结论化：一行标题 + 接力状态 |
| 叶子 + `files` / 省略 `form` | 只读简报（正文走 pointer 丢弃） | 保留结论——简报是 CEO 唯一信息源 |
| 有下游 | 只读简报（正文由下游经依赖上下文池完整读走） | 保留结论 |

后两档的「正文 ↔ 简报重复」是**功能性冗余**（两个读者各需一份），不是缺陷，别再当重复去修。写侧只能按 `form` 代理判断（prose 禁落盘 → 必然 pass_through），故 `files` / 省略 `form` 一律按「保留结论」处理：宁留重复，不造「简报去了结论、CEO 又读不到正文」的静默空洞。→ 见代码: `runtime/runs/executor/identities.py` · `runtime/delegate/ceo_format.py`

被否决：`key_points` 换成纯接力状态（它是计划复核卡 / CEO 确定性评审 / 审计 playbook 的事实载荷，换血同时饿死三方）；门禁改「必须 ≥2 条 key_points」（数条数挡不住结论复述，却误伤只写长 summary 的合规上游 → [拦截纪律](/.cursor/rules/intercept-discipline.mdc)）。`summary` 长度只作 schema 提示，不做运行时拒收（harvest 不 enforce `maxLength`）。

约束边界：`degraded` 不是 `RunDebrief` wire 字段，两端读 dict 额外键（降级 debrief = 正文切片，展示必然与正文重复，故只留提示）——payload 若加严格校验或剔未声明键会静默失效。8 员全平行 prose 叶子经协调态 `ALL_COMPLETED` 二次裁后每员只剩残片，未提预算。

### 自主度三档

琐碎自修 → 执行层试一轮再 escalate → 方案层立刻 escalate。与用户会话 **PermissionAxes** / 权限配方正交。

Worker 工具后还有确定性 **Escalation Gate**：只把工具失败当执行层自愈，**不**扫工具输出自由文猜方案层。方案层 /「职责偏离」只走结构化 `escalate(kind=scope|dep|…)`（真写越界由写工具层硬拒）。同 run 同 question 只 live 上报一次。若仍产出内部 `gate_kind=contract|contradiction`，**wire** `kind` 诚实落为 `normal`（保留 `gate_kind`），**不得**占用户面 `scope` 职责偏离——仅结构化 `scope`/`dep` 占对应 wire kind。→ 见代码: `runtime/routing/models.py` · `runtime/routing/gate.py`

### 协调态与视图（写/读分工）

一句话：**写只走 `drive*` / `CoordinationSession`，`pipeline_view` 与前端协作图只是读投影**。完整分工表（含各面的「禁止」与定案）→ [编排器 · 执行写路径 vs 进度读视图](/docs/03-AI核心/编排器与CEO主Agent.md#执行写路径-vs-进度读视图)（**权威，勿在本文复制**）。

本文只钉一条协作侧边界：路径写权账本（§三）与「图上怎么画」**正交**——账本管能不能写文件，图只反映进度。

## 三、冲突与文件写权

CEO 唯一裁决；置信度低才 `ask_user`。资源冲突靠 DAG。

### 交接式写权（C3）

协调会话内一本路径账本（`WriteCoordinator`）；内部键 = **桌 × 相对路径**（`desk_id = target_folder_id or 会话出生 desk`，跨桌同 `rel_path` 不互拦；用户可见冲突仍点名裸路径）。跨文件夹换桌写盘见 [双模式工作区 · 跨文件夹](/docs/02-架构/双模式工作区.md)。

| 阶段 | 行为 |
|---|---|
| **派发 declare** | 无主路径由首个声明 artifact 的节点成为写主；**下游不因祖先关系在派发瞬间抢锁**（只登记计划意图）。嵌套 lead→child 显式允许派发交接。**跨波次**：新节点声明的路径若锁主已在 `completed_run_ids`，派发时自动移交（审校→修订无需用户点卡；入闸不再因「已完成占位」拒单）。 |
| **同岗位续派** | 座位（规范化角色名）上前任已完成或已 vacated，再派同座且无在跑同座 → 自动填 `replaces_run_id`，继承其写锁（预算触顶未落盘后再派同一岗位可直接写）。 |
| **写入 claim** | 真写时：本人 / 无主可写；祖先持有则可交接覆写；无关队友拒写。 |
| **完成交接** | worker 完成后，若其持有路径恰好被**唯一**未完成依赖方列入 artifacts，则自动移交。 |
| **显式移交** | `resolve_escalation(transfer_ownership=true)`；或用户写权卡「移交写权 / 保持原主」。**仅锁主仍在跑**时写权冲突直达用户；已完成占位不走用户移交卡（走同座续派 / declare）。 |

写权冲突 escalate：**锁主进行中**才直达用户（与 `browser_login` 同属用户直达例外），卡上结构化动作真正转锁——自然语言「移交」 alone 不会改账本。锁主已完成却仍撞账本 → 协调活跃时改走主管裁决，提示同座续派，不弹「移交写权」。

**编排纪律（✅ 提示词，非软闸）**：无 `depends_on` 的并行 sibling 勿共写同一目标文件——各写私有产出或串行 / 指定整合者。已声明同 `artifacts` 交叉由 `sibling_artifact` 硬拒（拒在 durable `run_plan` / `plan_snapshot` 之前；try_start 才拒须擦从未开工座位，不得进同构「还在跑」分母；无活跃协调时 `cancel_worker` 仍可划空座位）；**不做**「同 artifacts 软提示」、**不**扫 task 长文猜同 path、**不**改为写成功即 release。→ CEO `【并行写盘】` · skill「并行写盘·同路径纪律」· captain 嵌套扇出写盘句 · `coordination/host.py`

### 验收与座位（质量两档）

| 档 | 信号（例） | 座位 / 修路 |
|---|---|---|
| **Hard** | 写权冲突、契约 `strict` 结构失败等 | `FAILED` → 进 `vacated_run_ids`；同座可 auto-`replaces` |
| **Soft** | 薄交接、未声明路径落盘、引用可剥、批次 `files_written` soft note | 仍 `COMPLETED`；**不** vacated；修路 = **同座位** replan/append（系统 auto-`replaces` + declare 转锁） |

**不做**：把 soft 质量塞进 vacated（污染失败语义）。**已撤**：空 handoff / 零声明清单把节点或整轮打成失败（实测误伤；行业也无此闸）。**禁止**：另起 `-v2` 角色名假装新座位抢同一路径；队员对已完成锁主 escalate 要用户移交。

→ 见代码: `workspace/write_claims.py` · `coordination/append_guard.py` · `EscalationCard`

## 四、⏳ / 否决

| 项 | 状态 |
|---|---|
| 完整 Preflight Audit | ⏳；薄预览不等于编制确认；编制到即开跑 |
| 一等 Team 实体 / A2A | ⏳ 暂不启动（勿与现行 `delegate` 临时组队混淆）→ [定位 §四](/docs/01-产品/产品定位与品牌.md) |
| 独立 Arena | **否决** |
| 树级共享 Semaphore | **否决**（父子互等死锁） |
| 便签墙 / worker 侧向广播 | **否决**（第四套实体；不留波内推送。旧 journal `team_note_posted` 跳过、不展示） |
| 两篇成稿靠边信道互焊 / 互 `depends_on` 成环 | **否决**；先非空 `team_brief` 或短规格岗。不新 playbook；不扩 `consumer_deps` 漏边软提示 |
