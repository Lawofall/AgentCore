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
| **简报**（`handoff` → `debrief.summary`） | 收尾轮正文便条（接力契约 + 增量交代） | 机器：下游 worker、CEO 综述、计划复核卡。人看侧栏时挂在该队员的成功 `handoff` 工具行，不另开页脚（降级简报除外）→ [前端 UX · 详情面板](/docs/04-前端/前端UX设计.md#十详情面板右坞) |

铁律：正文与简报**只指向产出、不复述其内容**——`form=files` 时正文只交代路径 / 怎么运行 / 关键取舍，落盘产物才装完整说明。`form=prose` 时产出 ≡ 正文；简报要不要再带结论见下节矩阵。

下游整合（扇入写总稿）：引擎把上游落盘路径注入「前置结果」；终端环**先读这些路径再写**，不把开工做成全仓勘探。CEO 派单应点名路径；缺稿用同一整合员续派。**否决**「整合员必须 `file_write`」硬闸。

### `handoff`

收尾信号（非正文复述）。**有下一棒必须交，并在收尾轮正文写清结论；没有下一棒默认不交，只有正文或文件里没写的增量才补。** 便条写在收尾那一轮的普通正文，工具参数表为空——不把「做成了什么」再倒进 JSON。便条形状（一句话：现在什么已成立；下一棒要接的路径/数字/决定；便条 ≠ 文件说明。叶子仅盘上没有的假设/风险/未验证）写在 `handoff` 工具 description，不进身份。有下游且门禁不足则合成降级 debrief；叶子不补要、不降级合成。协议 `RunDebrief` 字段名不改：新回合只填 `summary` = 便条全文；`key_points` / `assumptions` / `next_steps` 新回合不写，历史四格仍可展示。队长综述不再单列「队员建议的下一步」。

**简报是否带结论，取决于下一棒 / 队长读不读得到正文。** 这条耦合横跨写侧提示词与读侧综述，单看一边看不出来：

| 节点 | CEO 读什么 | 简报 |
|---|---|---|
| 叶子 | 正文（`form=prose` 整份 allowance）或文件指针 + 已核路径 | 默认不写；有增量才补，不复述结论 |
| 有下游 | 只读简报（正文由下游经依赖上下文池完整读走） | 必须写，且带结论 |

有下游时「正文 ↔ 简报重复」是**功能性冗余**（两个读者各需一份），不是缺陷，别再当重复去修。叶子不再靠简报当队长唯一信息源——队长读正文或落盘路径。→ 见代码: `tools/builtin/handoff.py` · `runtime/delegate/ceo_format.py`

被否决：把便条塞进工具 JSON（刚写完长文再倒进 `summary`，引号/换行导致 `args_parse_failed`，窄 salvage 救不了；空 `{}` 是失败后最小合法对象）；从成稿切「## 交接简报」；JSON 里再留一个 `text` 字段；给最后一棒卸掉 handoff 工具；`key_points` 换成纯接力状态（计划复核卡 / CEO 确定性评审仍读历史四格；新回合不写这些字段）；门禁改「必须 ≥2 条 key_points」（数条数挡不住结论复述，却误伤只写长 summary 的合规上游 → [拦截纪律](/.cursor/rules/intercept-discipline.mdc)）；叶节点「干了不少」（用过工具 / 正文较长）也强制交接（误伤最后一棒，便条抄正文；队长已能看见正文或文件）。handoff 无参数字段，不做运行时拒收空便条（空交硬拒已撤）。

约束边界：`degraded` 不是 `RunDebrief` wire 字段，两端读 dict 额外键（降级 debrief = 正文切片，展示必然与正文重复，故只留提示）——payload 若加严格校验或剔未声明键会静默失效。8 员全平行 prose 叶子共享 `CEO_SYNTHESIS_BUDGET`（水填）；名册与终稿纪律不进这笔预算。禁止再对整包做第二次内容整形。收获只取**最后一次可用** handoff：有正文用正文；没正文才回落历史参数四格（失败解析后的空 `{}` 不覆盖先前正文便条）。

### 自主度三档

琐碎自修 → 执行层试一轮再 escalate → 方案层立刻 escalate。与用户会话 **PermissionAxes** / 权限配方正交。

Worker 工具后还有确定性 **Escalation Gate**：只把工具失败当执行层自愈，**不**扫工具输出自由文猜方案层。方案层 /「职责偏离」只走结构化 `escalate(kind=scope|dep|…)`（真写越界由写工具层硬拒）。同 run 同 question 只 live 上报一次。若仍产出内部 `gate_kind=contract|contradiction`，**wire** `kind` 诚实落为 `normal`（保留 `gate_kind`），**不得**占用户面 `scope` 职责偏离——仅结构化 `scope`/`dep` 占对应 wire kind。→ 见代码: `runtime/routing/models.py` · `runtime/routing/gate.py`

### 协调态与视图（写/读分工）

一句话：**写只走 `drive*` / `CoordinationSession`，`pipeline_view` 与前端协作图只是读投影**。完整分工表（含各面的「禁止」与定案）→ [编排器 · 执行写路径 vs 进度读视图](/docs/03-AI核心/编排器与CEO主Agent.md#执行写路径-vs-进度读视图)（**权威，勿在本文复制**）。

本文只钉一条协作侧边界：写占用（§三）与「图上怎么画」**正交**——图只反映进度；能不能写这一下看权限、短锁和版本，不看「文件归谁到交卷」。

## 三、冲突与文件写权

CEO 唯一裁决；置信度低才 `ask_user`。资源先后靠 DAG（`depends_on`）。

### 写占用 = 这一次工具调用

Agent 没有「文件开在编辑器里」。写盘占用只包住**这一次** `file_write` / `file_append` / `str_replace`（以及同族改盘工具）：磁盘短串行（`workspace_lock`）+ 整篇覆盖须对得上刚读的版本（CAS）。写完（成败）即放开；人还在队里也不占着。

并肩两人可以点名同一份产出、同时开工。冲突 = 原文或整篇版本对不上（`str_replace` 找不到原文；`file_write` 盘上已不是刚读到的那一版），不是「这份文件归谁」。新建空路径两人同时创建 = 后写覆盖，可接受。

**留下**：真有先后的 `depends_on`（下游吃上游产出）；同一岗位不要同时坐两个人（`sibling_role`）；写权限 / 哪张桌 / `write_scope`。

**已撤**（不要半套兼容）：派工 `declare` 当排他锁；写时因「归别人」拒绝；做完交接 / 用户卡「移交写权 / 保持原主」；对账「写权冲突未解」blocking；写权冲突把队员打成 `FAILED` / vacated；同批 `artifacts` 交叉 `sibling_artifact` 硬拒整批；队长「并行别写同一份 / 各写私有再整合」纪律。不扫 task 长文猜路径，不加「不许用命令改文件」。

同座位续派仍可 auto-`replaces`（预算触顶后再派同一岗位）。`replaces_run_id` / `continue_from_run_id` 是计划手术，与写占用无关。

### 验收与座位（质量两档）

| 档 | 信号（例） | 座位 / 修路 |
|---|---|---|
| **Hard** | 结构契约失败、能力缺失等 | `FAILED` → 进 `vacated_run_ids`；同座可 auto-`replaces` |
| **Soft** | 薄交接、引用可剥、批次 `files_written` soft note、结构不达标（缺章节） | 仍 `COMPLETED`；**不** vacated；修路 = **同座位** replan/append（系统 auto-`replaces`） |

声明未命中时工人另写下的文件即产物，不是 soft 缺口。→ [执行引擎](/docs/03-AI核心/执行引擎架构设计.md)。

**不做**：把 soft 质量塞进 vacated（污染失败语义）。**已撤**：空 handoff / 零声明清单把节点或整轮打成失败（实测误伤；行业也无此闸）。**禁止**：另起 `-v2` 角色名假装新座位。单次工具失败（原文不对、整篇 CAS 失败）不是座位失败。

→ 见代码: `tools/builtin/file_ops/integrity.py` · `coordination/append_guard.py` · `workspace_lock`

## 四、⏳ / 否决

| 项 | 状态 |
|---|---|
| 完整 Preflight Audit | ⏳；薄预览不等于编制确认；编制到即开跑 |
| 一等 Team 实体 / A2A | ⏳ 第一刀不做（商店只卖 Skill；勿与现行 `delegate` 临时组队混淆）→ [工具与能力 · 能力商店](/docs/03-AI核心/工具与能力系统.md#能力商店) |
| 独立 Arena | **否决** |
| 树级共享 Semaphore | **否决**（父子互等死锁） |
| 便签墙 / worker 侧向广播 | **否决**（第四套实体；不留波内推送。旧 journal `team_note_posted` 跳过、不展示） |
| 两篇成稿靠边信道互焊 / 互 `depends_on` 成环 | **否决**；先非空 `team_brief` 或短规格岗。不新 playbook；不复活 `consumer_deps` 漏边软提示 |
